"""Non-streaming, provider-neutral chat completions. No tools, redirects or proxies."""

from __future__ import annotations

import http.client
import json
import socket
import ssl
import threading
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

from .mail_transport import MailFailure, public_address

MAX_RESPONSE = 2 * 1024 * 1024


class ModelFailure(MailFailure):
    def __init__(self, code, message, retryable=False, unsupported=False, *, diagnostics=None):
        super().__init__(code, message, retryable, unsupported)
        self.diagnostics = diagnostics or {}

    def public_diagnostics(self):
        """Only bounded counters/enums; never persist provider text or exception strings."""
        result = {}
        if not isinstance(self.diagnostics, dict):
            return result
        stage = self.diagnostics.get("stage")
        if isinstance(stage, str) and stage in {"dns", "request", "response_headers", "response_body"}:
            result["stage"] = stage
        for key, low, high in (
            ("http_status", 100, 599),
            ("timeout_seconds", 5, 120),
            ("elapsed_ms", 0, 3_600_000),
            ("request_bytes", 0, 20 * 1024 * 1024),
        ):
            value = self.diagnostics.get(key)
            if type(value) is int and low <= value <= high:
                result[key] = value
        return result


def strict_json(text):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def invalid_constant(_):
        raise ValueError("non-finite JSON number")

    return json.loads(text, object_pairs_hook=pairs, parse_constant=invalid_constant)


@dataclass
class ModelReply:
    content: str
    input_tokens: int | None = None
    output_tokens: int | None = None


class PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, host, port, address, deadline):
        super().__init__(host, port, timeout=max(0.01, deadline - time.monotonic()))
        self.address, self.deadline = address, deadline
        self.timer = None
        self.deadline_socket = None
        self.timed_out = threading.Event()

    def connect(self):
        self.sock = socket.create_connection(
            (self.address, self.port), timeout=max(0.01, self.deadline - time.monotonic())
        )

        self.deadline_socket = self.sock

        def abort():
            sock = self.deadline_socket
            if sock:
                self.timed_out.set()
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        self.timer = threading.Timer(max(0.01, self.deadline - time.monotonic()), abort)
        self.timer.daemon = True
        self.timer.start()
        if time.monotonic() >= self.deadline:
            self.finish()
            raise TimeoutError()

    def finish(self):
        """Keep the deadline alive after getresponse transfers socket ownership."""
        if self.timer:
            self.timer.cancel()
        self.close()
        if self.deadline_socket:
            self.deadline_socket.close()
            self.deadline_socket = None


class PinnedHTTPS(PinnedHTTP):
    default_port = http.client.HTTPS_PORT

    def __init__(self, host, port, address, deadline):
        super().__init__(host, port, address, deadline)
        self._context = ssl.create_default_context()

    def connect(self):
        super().connect()
        try:
            self.sock = self._context.wrap_socket(
                self.sock, server_hostname=self.host, do_handshake_on_connect=False
            )
            self.deadline_socket = self.sock
            self.sock.settimeout(max(0.01, self.deadline - time.monotonic()))
            self.sock.do_handshake()
            if time.monotonic() >= self.deadline:
                raise TimeoutError()
        except Exception:
            self.finish()
            raise


def chat(config, messages) -> ModelReply:
    """Bounded response and absolute DNS + request deadline. Never expose provider errors."""
    started = time.monotonic()
    deadline = started + config["timeout_seconds"]
    stage, request_bytes, response = "dns", None, None

    def diagnostics():
        values = {
            "stage": stage,
            "timeout_seconds": config["timeout_seconds"],
            "elapsed_ms": max(0, int((time.monotonic() - started) * 1000)),
        }
        if request_bytes is not None:
            values["request_bytes"] = request_bytes
        if response is not None:
            values["http_status"] = response.status
        return values

    def timeout_failure():
        return ModelFailure(
            "LLM_TIMEOUT",
            f"模型请求超过 {config['timeout_seconds']} 秒时限，请适当增大超时或减少输入；是否计费需向服务商确认",
            retryable=True,
            diagnostics=diagnostics(),
        )

    u = urlsplit(config["base_url"])
    if u.scheme not in {"http", "https"}:
        raise ModelFailure("LLM_TARGET_BLOCKED", "模型基础地址仅支持 HTTP 或 HTTPS")
    port = u.port or (443 if u.scheme == "https" else 80)
    try:
        address = public_address(u.hostname, port, timeout=max(0.01, deadline - time.monotonic()))
    except MailFailure as error:
        code = "LLM_TARGET_BLOCKED" if error.code == "MAIL_TARGET_BLOCKED" else "LLM_DNS_FAILED"
        raise ModelFailure(
            code,
            "模型服务器须为公网地址，DNS 解析或地址检查失败",
            retryable=error.retryable,
            diagnostics=diagnostics(),
        ) from None
    payload = dict(model=config["model"], messages=messages, stream=False, max_tokens=config["max_tokens"])
    if config["json_mode"]:
        payload["response_format"] = {"type": "json_object"}
    encoded_payload = json.dumps(payload, ensure_ascii=False).encode()
    request_bytes = len(encoded_payload)
    connection_type = PinnedHTTPS if u.scheme == "https" else PinnedHTTP
    connection = connection_type(u.hostname, port, address, deadline)
    try:
        stage = "request"
        connection.request(
            "POST",
            u.path.rstrip("/") + "/chat/completions",
            body=encoded_payload,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connection": "close",
                "Authorization": "Bearer " + config["api_key"],
            },
        )
        stage = "response_headers"
        response = connection.getresponse()
        if response.status != 200:
            if response.status in {401, 403}:
                raise ModelFailure("LLM_AUTH_FAILED", "模型凭证或权限被拒绝，请检查配置")
            if 300 <= response.status < 400:
                raise ModelFailure(
                    "LLM_REDIRECT_BLOCKED", "模型重定向已拒绝，请填写最终 HTTP 或 HTTPS 基础地址"
                )
            hints = {
                400: "请检查模型名、JSON 模式、输入长度和输出 token 配置",
                404: "请检查模型基础地址是否包含正确的 API 路径，以及模型名是否可用",
                408: "模型服务等待请求超时，请稍后重试或减少输入",
                413: "请求过大，请减少输入字符上限或拆分资料",
                422: "请求参数不被支持，请检查模型名、JSON 模式和输出 token 配置",
                429: "请求限流或配额不足，请检查服务商额度并稍后重试",
            }
            hint = hints.get(response.status, "请检查模型服务状态及配置")
            if response.status >= 500:
                hint = "模型服务或网关异常，请稍后重试；若持续失败，请检查服务状态、网关超时及输入长度"
            raise ModelFailure(
                "LLM_RATE_LIMITED" if response.status == 429 else "LLM_HTTP_FAILED",
                f"模型服务返回 HTTP {response.status}：{hint}",
                retryable=response.status in {408, 429} or response.status >= 500,
            )
        if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
            raise ModelFailure("LLM_ENCODING_UNSUPPORTED", "模型响应压缩方式不支持")
        stage = "response_body"
        data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise ModelFailure("LLM_RESPONSE_TOO_LARGE", "模型响应超过安全上限")
        if time.monotonic() >= deadline:
            raise timeout_failure()
        try:
            body = strict_json(data)
            if not isinstance(body, dict) or not isinstance(body.get("choices"), list) or not body["choices"]:
                raise ValueError()
            choice = body["choices"][0]
            if choice.get("finish_reason") == "length":
                raise ModelFailure(
                    "LLM_OUTPUT_TRUNCATED", "模型输出被截断，请增大输出长度或更换模型", retryable=True
                )
            if choice.get("finish_reason") not in {None, "stop"}:
                raise ModelFailure("LLM_OUTPUT_REJECTED", "模型未正常完成文本识别")
            message = choice["message"]
            if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
                raise ModelFailure("LLM_OUTPUT_REJECTED", "不接受模型工具调用或拒绝响应")
            content = message["content"]
            if not isinstance(content, str) or not content.strip():
                raise ValueError()
            usage = body.get("usage") or {}

            def token(name):
                value = usage.get(name) if isinstance(usage, dict) else None
                return value if type(value) is int and 0 <= value <= 100_000_000 else None

            return ModelReply(content, token("prompt_tokens"), token("completion_tokens"))
        except ModelFailure:
            raise
        except (ValueError, TypeError, KeyError, IndexError, UnicodeError, AttributeError, RecursionError):
            raise ModelFailure(
                "LLM_RESPONSE_INVALID", "模型响应不符合 chat completions 约定", retryable=True
            ) from None
    except ModelFailure as error:
        error.diagnostics = diagnostics()
        raise
    except ssl.SSLError:
        if time.monotonic() >= deadline or getattr(connection, "timed_out", threading.Event()).is_set():
            raise timeout_failure() from None
        raise ModelFailure(
            "LLM_TLS_FAILED",
            "模型 TLS 证书或安全连接校验失败，请检查证书及 HTTPS 配置",
            diagnostics=diagnostics(),
        ) from None
    except (TimeoutError, socket.timeout):
        raise timeout_failure() from None
    except (OSError, http.client.HTTPException) as error:
        # Deadline shutdown can surface as IncompleteRead, RemoteDisconnected or OSError.
        if time.monotonic() >= deadline or getattr(connection, "timed_out", threading.Event()).is_set():
            raise timeout_failure() from None
        if isinstance(error, ConnectionRefusedError):
            hint = "模型连接被拒绝，请检查基础地址、端口及模型服务是否可用"
        elif isinstance(error, http.client.RemoteDisconnected):
            hint = "模型服务在返回响应前关闭连接，请检查服务状态、网关超时及输入长度"
        elif isinstance(error, http.client.IncompleteRead):
            hint = "模型响应未接收完整，请稍后重试；若持续失败，请检查网络和网关"
        elif isinstance(error, (ConnectionResetError, BrokenPipeError)):
            hint = "模型连接被中断，请稍后重试；若持续失败，请检查网络、网关超时及输入长度"
        else:
            hint = "模型网络请求失败，请检查服务器到模型服务的网络连接及基础地址"
        raise ModelFailure("LLM_NETWORK_FAILED", hint, retryable=True, diagnostics=diagnostics()) from None
    finally:
        if response is not None:
            response.close()
        connection.finish()
