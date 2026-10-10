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
    pass


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


class PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, host, port, address, deadline):
        super().__init__(
            host, port, timeout=max(0.01, deadline - time.monotonic()), context=ssl.create_default_context()
        )
        self.address, self.deadline = address, deadline
        self.timer = None
        self.deadline_socket = None

    def connect(self):
        self.sock = socket.create_connection(
            (self.address, self.port), timeout=max(0.01, self.deadline - time.monotonic())
        )

        self.deadline_socket = self.sock

        def abort():
            sock = self.deadline_socket
            if sock:
                try:
                    sock.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

        self.timer = threading.Timer(max(0.01, self.deadline - time.monotonic()), abort)
        self.timer.daemon = True
        self.timer.start()
        try:
            self.sock = self._context.wrap_socket(
                self.sock, server_hostname=self.host, do_handshake_on_connect=False
            )
            self.deadline_socket = self.sock
            self.sock.settimeout(max(0.01, self.deadline - time.monotonic()))
            self.sock.do_handshake()
            if time.monotonic() >= self.deadline:
                abort()
                raise TimeoutError()
        except Exception:
            self.finish()
            raise

    def finish(self):
        """Keep the deadline alive after getresponse transfers socket ownership."""
        if self.timer:
            self.timer.cancel()
        self.close()
        if self.deadline_socket:
            self.deadline_socket.close()
            self.deadline_socket = None


def chat(config, messages) -> ModelReply:
    """Bounded response and absolute DNS + request deadline. Never expose provider errors."""
    deadline = time.monotonic() + config["timeout_seconds"]
    u = urlsplit(config["base_url"])
    try:
        address = public_address(u.hostname, u.port or 443, timeout=max(0.01, deadline - time.monotonic()))
    except MailFailure as error:
        code = "LLM_TARGET_BLOCKED" if error.code == "MAIL_TARGET_BLOCKED" else "LLM_DNS_FAILED"
        raise ModelFailure(
            code, "模型服务器须为公网地址，DNS 解析或地址检查失败", retryable=error.retryable
        ) from None
    payload = dict(model=config["model"], messages=messages, stream=False, max_tokens=config["max_tokens"])
    if config["json_mode"]:
        payload["response_format"] = {"type": "json_object"}
    connection = PinnedHTTPS(u.hostname, u.port or 443, address, deadline)
    response = None
    try:
        connection.request(
            "POST",
            u.path.rstrip("/") + "/chat/completions",
            body=json.dumps(payload, ensure_ascii=False).encode(),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Connection": "close",
                "Authorization": "Bearer " + config["api_key"],
            },
        )
        response = connection.getresponse()
        if response.status != 200:
            if response.status in {401, 403}:
                raise ModelFailure("LLM_AUTH_FAILED", "模型凭证或权限被拒绝，请检查配置")
            if 300 <= response.status < 400:
                raise ModelFailure("LLM_REDIRECT_BLOCKED", "模型重定向已拒绝，请填写最终 HTTPS 基础地址")
            raise ModelFailure(
                "LLM_RATE_LIMITED" if response.status == 429 else "LLM_HTTP_FAILED",
                "模型服务返回非成功状态",
                retryable=response.status == 429 or response.status >= 500,
            )
        if response.getheader("Content-Encoding", "identity").lower() not in {"identity", ""}:
            raise ModelFailure("LLM_ENCODING_UNSUPPORTED", "模型响应压缩方式不支持")
        data = response.read(MAX_RESPONSE + 1)
        if len(data) > MAX_RESPONSE:
            raise ModelFailure("LLM_RESPONSE_TOO_LARGE", "模型响应超过安全上限")
        if time.monotonic() >= deadline:
            raise ModelFailure("LLM_TIMEOUT", "模型请求超时，是否计费需向服务商确认", retryable=True)
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
    except ModelFailure:
        raise
    except ssl.SSLError:
        raise ModelFailure("LLM_TLS_FAILED", "模型 TLS 证书或安全连接校验失败") from None
    except (TimeoutError, socket.timeout):
        raise ModelFailure("LLM_TIMEOUT", "模型请求超时，是否计费需向服务商确认", retryable=True) from None
    except (OSError, http.client.HTTPException):
        raise ModelFailure(
            "LLM_NETWORK_FAILED", "模型网络请求失败，不输出供应商私密诊断", retryable=True
        ) from None
    finally:
        if response is not None:
            response.close()
        connection.finish()
