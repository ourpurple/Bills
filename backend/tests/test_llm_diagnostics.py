"""Safe failure diagnostics and deadline regression tests; no live model calls."""

import http.client
import json
import socket
import ssl
import threading
import time

import pytest
from test_llm import FakeModel, config, parse, prepared, result
from test_mail import drain, task

from app.llm_adapter import ModelFailure, PinnedHTTP, chat
from app.mail_jobs import Worker

KEY = "private-key-do-not-expose"
PRIVATE = "private-provider-response-and-bank-text"
CONFIG = dict(
    timeout_seconds=5,
    base_url="http://models.example.test/v1",
    model="synthetic-model",
    max_tokens=512,
    json_mode=False,
    api_key=KEY,
)


def fake_transport(monkeypatch, *, status=200, problem=None, stage="response_headers", timed_out=False):
    import app.llm_adapter as adapter

    events = []

    class Response:
        def __init__(self):
            self.status = status

        def getheader(self, *_):
            return "identity"

        def read(self, *_):
            events.append("read")
            if stage == "response_body" and problem:
                raise problem
            return json.dumps(dict(choices=[dict(message=dict(content="ok"), finish_reason="stop")])).encode()

        def close(self):
            events.append("response_closed")

    class Connection:
        def __init__(self, *_):
            self.timed_out = threading.Event()
            if timed_out:
                self.timed_out.set()

        def request(self, *_args, **_kwargs):
            if stage == "request" and problem:
                raise problem

        def getresponse(self):
            if stage == "response_headers" and problem:
                raise problem
            return Response()

        def finish(self):
            events.append("connection_closed")

    monkeypatch.setattr(adapter, "public_address", lambda *a, **kw: "1.1.1.1")
    monkeypatch.setattr(adapter, "PinnedHTTP", Connection)
    return events


@pytest.mark.parametrize(
    "status,hint,retryable",
    [
        (400, "JSON", False),
        (404, "API", False),
        (408, "超时", True),
        (413, "请求过大", False),
        (422, "参数", False),
        (429, "配额", True),
        (500, "网关", True),
        (502, "网关", True),
        (504, "网关", True),
    ],
)
def test_http_status_is_actionable_and_safe(monkeypatch, status, hint, retryable):
    events = fake_transport(monkeypatch, status=status)
    with pytest.raises(ModelFailure) as raised:
        chat(CONFIG, [{"role": "user", "content": PRIVATE}])
    error = raised.value
    assert error.code == ("LLM_RATE_LIMITED" if status == 429 else "LLM_HTTP_FAILED")
    assert f"HTTP {status}" in error.message and hint in error.message
    assert error.retryable is retryable
    diagnostic = error.public_diagnostics()
    assert diagnostic["http_status"] == status
    assert diagnostic["stage"] == "response_headers"
    assert diagnostic["request_bytes"] > len(PRIVATE)
    assert diagnostic["timeout_seconds"] == 5
    assert diagnostic["elapsed_ms"] >= 0
    public = json.dumps(dict(message=error.message, diagnostics=diagnostic))
    assert KEY not in public and PRIVATE not in public and "example.test" not in public
    assert events == ["response_closed", "connection_closed"]  # Error body was never read.


@pytest.mark.parametrize(
    "problem,stage,hint",
    [
        (ConnectionRefusedError(PRIVATE), "request", "连接被拒绝"),
        (ConnectionResetError(PRIVATE), "response_headers", "连接被中断"),
        (BrokenPipeError(PRIVATE), "request", "连接被中断"),
        (http.client.RemoteDisconnected(PRIVATE), "response_headers", "关闭连接"),
        (http.client.IncompleteRead(PRIVATE.encode()), "response_body", "未接收完整"),
        (OSError(PRIVATE), "request", "网络连接"),
    ],
)
@pytest.mark.parametrize("timed_out", [False, True])
def test_network_failures_distinguish_deadline_shutdown(monkeypatch, problem, stage, hint, timed_out):
    events = fake_transport(monkeypatch, problem=problem, stage=stage, timed_out=timed_out)
    with pytest.raises(ModelFailure) as raised:
        chat(CONFIG, [])
    error = raised.value
    assert error.code == ("LLM_TIMEOUT" if timed_out else "LLM_NETWORK_FAILED")
    assert ("5 秒" if timed_out else hint) in error.message
    assert error.retryable
    assert error.public_diagnostics()["stage"] == stage
    assert PRIVATE not in json.dumps(dict(message=error.message, diagnostics=error.public_diagnostics()))
    assert events[-1] == "connection_closed"


def test_elapsed_deadline_without_timer_event(monkeypatch):
    import app.llm_adapter as adapter

    fake_transport(monkeypatch, problem=http.client.RemoteDisconnected(PRIVATE))
    clock = iter([100.0] * 2 + [106.0] * 10)
    monkeypatch.setattr(adapter.time, "monotonic", lambda: next(clock))
    with pytest.raises(ModelFailure) as raised:
        chat(CONFIG, [])
    assert raised.value.code == "LLM_TIMEOUT"


@pytest.mark.parametrize(
    "problem,expected,retryable",
    [
        (TimeoutError(PRIVATE), "LLM_TIMEOUT", True),
        (ssl.SSLError(PRIVATE), "LLM_TLS_FAILED", False),
    ],
)
def test_timeout_and_tls_diagnostics(monkeypatch, problem, expected, retryable):
    fake_transport(monkeypatch, problem=problem, stage="request")
    with pytest.raises(ModelFailure) as raised:
        chat(CONFIG, [])
    assert raised.value.code == expected
    assert raised.value.retryable is retryable
    assert raised.value.public_diagnostics()["stage"] == "request"
    assert PRIVATE not in raised.value.message


@pytest.mark.parametrize("stage", ["response_headers", "response_body"])
def test_real_socket_deadline_maps_to_timeout(monkeypatch, stage):
    import app.llm_adapter as adapter

    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(2)
    port = listener.getsockname()[1]
    stopped = threading.Event()

    def serve():
        peer, _ = listener.accept()
        with peer:
            peer.recv(8192)
            if stage == "response_body":
                peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\nx")
            stopped.wait(2)

    class ShortDeadline(PinnedHTTP):
        def __init__(self, host, port, address, deadline):
            # Simulate expiry while the caller's clock still has time remaining.
            super().__init__(host, port, address, time.monotonic() + 0.15)

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(adapter, "public_address", lambda *a, **kw: "127.0.0.1")
    monkeypatch.setattr(adapter, "PinnedHTTP", ShortDeadline)
    started = time.monotonic()
    try:
        with pytest.raises(ModelFailure) as raised:
            chat({**CONFIG, "base_url": f"http://example.test:{port}/v1"}, [])
        assert raised.value.code == "LLM_TIMEOUT"
        assert raised.value.public_diagnostics()["stage"] == stage
        assert time.monotonic() - started < 1.5
    finally:
        stopped.set()
        thread.join(timeout=3)
        listener.close()


def test_diagnostics_reject_untrusted_fields_and_values():
    failure = ModelFailure(
        "LLM_NETWORK_FAILED",
        "固定提示",
        diagnostics={
            "stage": PRIVATE,
            "http_status": 999,
            "elapsed_ms": -1,
            "request_bytes": True,
            "timeout_seconds": 121,
            "api_key": KEY,
            "body": PRIVATE,
            "exception": PRIVATE,
        },
    )
    assert failure.public_diagnostics() == {}


@pytest.mark.parametrize("bank", ["工商银行", "兴业银行"])
def test_failure_diagnostics_persist_without_publishing_bank_data(admin, bank):
    config(admin, task_call_limit=1)
    source = prepared(admin, text=f"{bank} 合成账单资料")
    fake = FakeModel(
        ModelFailure(
            "LLM_TIMEOUT",
            "模型请求超过 60 秒时限",
            retryable=True,
            diagnostics={
                "stage": "response_body",
                "http_status": 200,
                "elapsed_ms": 60000,
                "timeout_seconds": 60,
                "request_bytes": 1200,
                "api_key": KEY,
                "body": PRIVATE,
            },
        )
    )
    run = parse(admin, source, fake)
    failed = task(admin, run["task"]["id"])
    assert failed["state"] == "failed"
    assert failed["result"] == {
        "diagnostics": {
            "stage": "response_body",
            "http_status": 200,
            "elapsed_ms": 60000,
            "timeout_seconds": 60,
            "request_bytes": 1200,
        }
    }
    assert KEY not in json.dumps(failed) and PRIVATE not in json.dumps(failed)
    assert run["candidate_ids"] == []
    assert admin.get("/api/v1/admin/statements").json()["total"] == 0
    retried = admin.post(f"/api/v1/admin/tasks/{failed['id']}/retry")
    assert retried.status_code == 202
    drain(Worker(admin.app.state.settings, admin.app.state.sessions, model_client=FakeModel(result())))
    success = task(admin, retried.json()["id"])
    assert success["state"] == "succeeded"
    assert "diagnostics" not in success["result"]
    assert task(admin, failed["id"])["result"] == failed["result"]


def test_connection_test_task_also_persists_safe_diagnostics(admin):
    config(admin, active=False, task_call_limit=1)
    queued = admin.post("/api/v1/admin/model-config/test").json()
    fake = FakeModel(
        ModelFailure(
            "LLM_HTTP_FAILED",
            "模型服务返回 HTTP 404",
            diagnostics={
                "stage": "response_headers",
                "http_status": 404,
            },
        )
    )
    drain(Worker(admin.app.state.settings, admin.app.state.sessions, model_client=fake))
    failed = task(admin, queued["id"])
    assert failed["state"] == "failed"
    assert failed["result"] == {"diagnostics": {"stage": "response_headers", "http_status": 404}}
