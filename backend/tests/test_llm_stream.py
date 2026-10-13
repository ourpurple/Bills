"""Synthetic SSE and loopback sockets only; never call a bill/model provider."""

import json
import socket
import threading
import time

import pytest
from test_llm import account, bill, config, parse, prepared, result
from test_llm_diagnostics import CONFIG, KEY, PRIVATE
from test_mail import task

import app.llm_adapter as adapter
from app.llm_adapter import ModelFailure, PinnedHTTP, PinnedHTTPS, chat


def chunk(text=None, reason=None, **delta):
    if text is not None:
        delta["content"] = text
    return dict(choices=[dict(index=0, delta=delta, finish_reason=reason)])


def event(value):
    return b"data: " + json.dumps(value, ensure_ascii=False).encode() + b"\n\n"


def complete(text="合成输出"):
    return event(chunk(text)) + event(chunk(reason="stop")) + b"data: [DONE]\n\n"


def transport(monkeypatch, wire, *, size=7, callback=None):
    events, requests = [], []

    class Response:
        status = 200

        def __init__(self):
            self.offset = 0

        def getheader(self, name, default=None):
            return "text/event-stream; charset=utf-8" if name == "Content-Type" else default

        def read1(self, count):
            if callback:
                callback(self.offset, len(wire))
            data = wire[self.offset : self.offset + min(size, count)]
            self.offset += len(data)
            return data

        def close(self):
            events.append("response_closed")

    class Connection:
        def __init__(self, *_):
            self.timed_out = threading.Event()

        def request(self, method, path, body, headers):
            requests.append((json.loads(body), headers))
            assert method == "POST" and path.endswith("/chat/completions")

        def getresponse(self):
            return Response()

        def enable_stream(self, idle, hard):
            events.append("stream_enabled")
            assert idle >= 5 and hard > time.monotonic()

        def stream_activity(self):
            pass

        def finish(self):
            events.append("connection_closed")

    monkeypatch.setattr(adapter, "public_address", lambda *a, **kw: "1.1.1.1")
    monkeypatch.setattr(adapter, "PinnedHTTP", Connection)
    monkeypatch.setattr(adapter, "PinnedHTTPS", Connection)
    return events, requests


@pytest.mark.parametrize("size", [1, 2, 7, 8192])
@pytest.mark.parametrize("newline", [b"\n", b"\r\n", b"\r"])
def test_stream_request_utf8_framing_usage_and_cleanup(monkeypatch, size, newline):
    wire = b"\xef\xbb\xbf: heartbeat\n\nid: safe\nretry: 100\n"
    wire += event(chunk(role="assistant", reasoning_content=PRIVATE))
    wire += event(chunk('{"合成":"工')) + event(chunk('商银行"}'))
    wire += event(chunk(reason="stop"))
    # Multiline data fields must be joined before parsing JSON.
    wire += b'data: {"choices": [],\ndata: "usage": {"prompt_tokens": 100, "completion_tokens": 20}}\n\n'
    wire += b"data: [DONE]\n\n"
    wire = wire.replace(b"\n", newline)
    events, requests = transport(monkeypatch, wire, size=size)
    reply = chat({**CONFIG, "json_mode": True}, [{"role": "user", "content": PRIVATE}])
    assert reply.content == '{"合成":"工商银行"}'
    assert (reply.input_tokens, reply.output_tokens) == (100, 20)
    payload, headers = requests[0]
    assert payload["stream"] is True
    assert payload["stream_options"] == {"include_usage": True}
    assert payload["response_format"] == {"type": "json_object"}
    assert "tools" not in payload
    assert headers["Accept"] == "text/event-stream, application/json"
    assert events == ["stream_enabled", "response_closed", "connection_closed"]
    assert len(requests) == 1


@pytest.mark.parametrize(
    "usage",
    [
        None,
        {},
        {"prompt_tokens": True, "completion_tokens": -1},
        {"prompt_tokens": 100_000_001, "completion_tokens": "20"},
    ],
)
def test_stream_unknown_usage_stays_unknown(monkeypatch, usage):
    wire = event(chunk("ok")) + event({**chunk(reason="stop"), "usage": usage}) + b"data: [DONE]\n\n"
    transport(monkeypatch, wire)
    reply = chat(CONFIG, [])
    assert reply.input_tokens is None and reply.output_tokens is None


@pytest.mark.parametrize(
    "wire, code",
    [
        (event(chunk("partial")), "LLM_STREAM_INCOMPLETE"),
        (event(chunk("partial", reason="stop")), "LLM_STREAM_INCOMPLETE"),
        (event(chunk("partial")) + b"data: [DONE]\n\n", "LLM_RESPONSE_INVALID"),
        (event(chunk(reason="stop")) + b"data: [DONE]\n\n", "LLM_RESPONSE_INVALID"),
        (event(chunk("partial", reason="length")), "LLM_OUTPUT_TRUNCATED"),
        (event(chunk("partial", reason="content_filter")), "LLM_OUTPUT_REJECTED"),
        (event(chunk(tool_calls=[{"id": PRIVATE}])), "LLM_OUTPUT_REJECTED"),
        (event(chunk(function_call={"name": PRIVATE})), "LLM_OUTPUT_REJECTED"),
        (event(chunk(refusal=PRIVATE)), "LLM_OUTPUT_REJECTED"),
        (event(chunk()) + event({"error": {"message": PRIVATE}}), "LLM_RESPONSE_INVALID"),
        (b"event: error\n" + event(chunk(PRIVATE)), "LLM_RESPONSE_INVALID"),
        (b'data: {"choices": [], "choices": []}\n\n', "LLM_RESPONSE_INVALID"),
        (b'data: {"choices": [], "usage": NaN}\n\n', "LLM_RESPONSE_INVALID"),
        (b"data: {broken-private-message}\n\n", "LLM_RESPONSE_INVALID"),
        (b"data: \xff\n\n", "LLM_RESPONSE_INVALID"),
        (event([]), "LLM_RESPONSE_INVALID"),
        (event({"choices": "wrong"}), "LLM_RESPONSE_INVALID"),
        (event({"choices": []}), "LLM_RESPONSE_INVALID"),
        (event({"choices": [None]}), "LLM_RESPONSE_INVALID"),
        (event({"choices": [{"index": 1, "delta": {}}]}), "LLM_RESPONSE_INVALID"),
        (event({"choices": [{"index": False, "delta": {}}]}), "LLM_RESPONSE_INVALID"),
        (event({"choices": [{"index": 0, "delta": []}]}), "LLM_RESPONSE_INVALID"),
        (event({"choices": [chunk()["choices"][0]] * 2}), "LLM_RESPONSE_INVALID"),
        (event(chunk([PRIVATE])), "LLM_RESPONSE_INVALID"),
        (event(chunk("ok", reason="stop")) + event(chunk("extra")), "LLM_RESPONSE_INVALID"),
        (event(chunk("ok", reason="stop")) + b"data: [DONE]", "LLM_STREAM_INCOMPLETE"),
    ],
)
def test_stream_rejects_incomplete_or_unsafe_output(monkeypatch, wire, code):
    events, _ = transport(monkeypatch, wire)
    with pytest.raises(ModelFailure) as raised:
        chat(CONFIG, [{"role": "user", "content": PRIVATE}])
    failure = raised.value
    assert failure.code == code
    diagnostics = failure.public_diagnostics()
    assert diagnostics["stage"] == "response_body"
    assert diagnostics["total_timeout_seconds"] == 600
    public = json.dumps(dict(message=failure.message, diagnostics=diagnostics))
    assert PRIVATE not in public and KEY not in public
    assert events[-2:] == ["response_closed", "connection_closed"]


@pytest.mark.parametrize("kind", ["output", "line", "multiline", "wire"])
def test_stream_bounds(monkeypatch, kind):
    monkeypatch.setattr(adapter, "MAX_RESPONSE", 256)
    if kind == "output":
        wire = event(chunk("中" * 45)) * 2
    elif kind == "line":
        wire = b":" + b"x" * 257 + b"\n\n"
    elif kind == "multiline":
        wire = (b"data: " + b"x" * 140 + b"\n") * 2 + b"\n"
    else:
        monkeypatch.setattr(adapter, "MAX_STREAM_BYTES", 256)
        wire = b": heartbeat\n\n" * 50
    transport(monkeypatch, wire)
    with pytest.raises(ModelFailure) as raised:
        chat(CONFIG, [])
    assert raised.value.code == "LLM_RESPONSE_TOO_LARGE"


@pytest.mark.parametrize("connection_type", [PinnedHTTP, PinnedHTTPS])
@pytest.mark.parametrize("mode", ["continuous", "idle", "total", "disconnect"])
def test_real_stream_deadlines_and_cleanup(monkeypatch, connection_type, mode):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen()
    listener.settimeout(2)
    port = listener.getsockname()[1]
    stopped = threading.Event()
    connections = []
    monkeypatch.setattr(adapter, "MAX_STREAM_SECONDS", 1)

    class Passthrough:
        def wrap_socket(self, sock, server_hostname, do_handshake_on_connect):
            assert server_hostname == "provider.example.test"
            self.socket = sock
            return self

        def do_handshake(self):
            pass

        def __getattr__(self, name):
            return getattr(self.socket, name)

    class ShortDeadlines(connection_type):
        def __init__(self, host, actual_port, address, deadline):
            super().__init__(host, actual_port, address, time.monotonic() + 0.25)
            if connection_type is PinnedHTTPS:
                self._context = Passthrough()
            connections.append(self)

        def enable_stream(self, idle_seconds, hard_deadline):
            super().enable_stream(0.18, hard_deadline)

    def serve():
        peer, _ = listener.accept()
        with peer:
            request = b""
            while b"\r\n\r\n" not in request:
                request += peer.recv(8192)
            headers, body = request.split(b"\r\n\r\n", 1)
            length = next(
                int(line.split(b":", 1)[1])
                for line in headers.split(b"\r\n")
                if line.lower().startswith(b"content-length:")
            )
            while len(body) < length:
                body += peer.recv(8192)
            peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nConnection: close\r\n\r\n")
            try:
                if mode == "continuous":
                    for _ in range(6):
                        peer.sendall(event(chunk("合成")))
                        if stopped.wait(0.08):
                            return
                    peer.sendall(event(chunk(reason="stop")) + b"data: [DONE]\n\n")
                elif mode == "idle":
                    peer.sendall(event(chunk("partial")))
                    stopped.wait(2)
                elif mode == "disconnect":
                    peer.sendall(event(chunk("partial")))
                else:
                    while not stopped.wait(0.04):
                        peer.sendall(b": heartbeat\n\n")
            except OSError:
                pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    monkeypatch.setattr(adapter, "public_address", lambda *a, **kw: "127.0.0.1")
    monkeypatch.setattr(
        adapter, "PinnedHTTPS" if connection_type is PinnedHTTPS else "PinnedHTTP", ShortDeadlines
    )
    scheme = "https" if connection_type is PinnedHTTPS else "http"
    started = time.monotonic()
    try:
        if mode == "continuous":
            reply = chat({**CONFIG, "base_url": f"{scheme}://provider.example.test:{port}/v1"}, [])
            assert reply.content == "合成" * 6
            assert time.monotonic() - started > 0.25
            assert not connections[0].timed_out.is_set()
        else:
            with pytest.raises(ModelFailure) as raised:
                chat({**CONFIG, "base_url": f"{scheme}://provider.example.test:{port}/v1"}, [])
            failure = raised.value
            if mode == "disconnect":
                assert failure.code == "LLM_STREAM_INCOMPLETE"
            else:
                assert failure.code == "LLM_TIMEOUT"
                assert failure.public_diagnostics()["timeout_kind"] == mode
                if mode == "total":
                    assert time.monotonic() - started >= 0.9
        assert time.monotonic() - started < 2
        conn = connections[0]
        assert conn.deadline_socket is None and conn.sock is None and conn._finished
        conn.timer.join(timeout=1)
        assert not conn.timer.is_alive()
    finally:
        stopped.set()
        thread.join(timeout=3)
        listener.close()


@pytest.mark.parametrize("completed", [True, False])
def test_worker_only_creates_candidate_after_stream_done(admin, monkeypatch, completed):
    config(admin, task_call_limit=1)
    account(admin)
    source = prepared(admin)
    text = json.dumps(result(bill()), ensure_ascii=False)
    wire = event(chunk(text[:100])) + event(chunk(text[100:])) + event(chunk(reason="stop"))
    if completed:
        wire += event({"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}})
        wire += b"data: [DONE]\n\n"

    def during_stream(offset, total):
        assert admin.get("/api/v1/admin/candidates").json()["total"] == 0
        assert admin.get("/api/v1/admin/statements").json()["total"] == 0

    transport(monkeypatch, wire, size=8192, callback=during_stream)
    run = parse(admin, source, chat)
    assert admin.get("/api/v1/admin/statements").json()["total"] == 0
    if completed:
        assert len(run["candidate_ids"]) == 1
        assert task(admin, run["task"]["id"])["state"] == "succeeded"
    else:
        assert run["candidate_ids"] == []
        failed = task(admin, run["task"]["id"])
        assert failed["error_code"] == "LLM_STREAM_INCOMPLETE"
        assert failed["result"]["diagnostics"]["total_timeout_seconds"] == 600


def test_activity_does_not_create_one_timer_per_chunk_and_finish_cannot_revive(monkeypatch):
    timers = []

    class Timer:
        def __init__(self, delay, callback):
            self.cancelled = False
            timers.append(self)

        def start(self):
            pass

        def cancel(self):
            self.cancelled = True

    class Socket:
        def settimeout(self, value):
            assert value > 0

        def close(self):
            pass

    monkeypatch.setattr(adapter.threading, "Timer", Timer)
    monkeypatch.setattr(adapter.socket, "create_connection", lambda *a, **kw: Socket())
    connection = PinnedHTTP("model.example.test", 80, "1.1.1.1", time.monotonic() + 5)
    connection.connect()
    connection.enable_stream(5, time.monotonic() + 600)
    for _ in range(1000):
        connection.stream_activity()
    assert len(timers) == 1
    connection._abort()  # A stale deadline callback reschedules, instead of closing early.
    assert len(timers) == 2 and not connection.timed_out.is_set()
    connection.finish()
    connection._abort()  # Finish wins the race: a late callback cannot restart the timer.
    assert len(timers) == 2 and timers[-1].cancelled
    with pytest.raises(TimeoutError):
        connection.stream_activity()


def test_stream_diagnostic_whitelist():
    failure = ModelFailure(
        "LLM_TIMEOUT",
        "fixed",
        diagnostics={
            "timeout_kind": "idle",
            "total_timeout_seconds": 600,
            "provider_error": PRIVATE,
            "usage": {"token": KEY},
        },
    )
    assert failure.public_diagnostics() == {"timeout_kind": "idle", "total_timeout_seconds": 600}
    failure.diagnostics.update(timeout_kind=PRIVATE, total_timeout_seconds=601)
    assert failure.public_diagnostics() == {}


@pytest.mark.parametrize("transfer_size", [1, 7, 8192])
def test_real_http_response_decodes_chunked_sse(monkeypatch, transfer_size):
    """Exercise HTTPResponse's actual chunk decoder, not just the fake read1."""
    import http.client
    import io

    wire = complete("合成分块传输")
    chunks = [wire[i : i + transfer_size] for i in range(0, len(wire), transfer_size)]
    encoded = b"".join(f"{len(data):x}\r\n".encode() + data + b"\r\n" for data in chunks)
    encoded += b"0\r\n\r\n"
    response_bytes = (
        b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\nTransfer-Encoding: chunked\r\n\r\n" + encoded
    )

    class Socket:
        def makefile(self, *args):
            return io.BytesIO(response_bytes)

    class Connection:
        def stream_activity(self):
            pass

    response = http.client.HTTPResponse(Socket())
    response.begin()
    try:
        reply = adapter.stream_reply(response, Connection())
        assert reply.content == "合成分块传输"
    finally:
        response.close()
