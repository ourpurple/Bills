"""Read-only IMAP/POP3. TLS verification and DNS pinning are mandatory."""

from __future__ import annotations

import imaplib
import ipaddress
import poplib
import socket
import ssl
import threading
from datetime import date, timedelta

from .core import DomainError


class MailFailure(Exception):
    def __init__(self, code, message, retryable=False, unsupported=False):
        self.code, self.message = code, message
        self.retryable, self.unsupported = retryable, unsupported
        super().__init__(message)


def public_address(host: str, port: int, timeout=20) -> str:
    result = []
    done = threading.Event()

    def resolve():
        try:
            result.extend(socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
        except OSError:
            pass
        finally:
            done.set()

    threading.Thread(target=resolve, daemon=True).start()
    if not done.wait(timeout) or not result:
        raise MailFailure("MAIL_DNS_FAILED", "邮箱主机解析失败或超时", retryable=True)
    rows = result
    addresses = list(dict.fromkeys(row[4][0] for row in rows))
    if not addresses:
        raise MailFailure("MAIL_DNS_FAILED", "邮箱主机没有可用地址", retryable=True)
    # Reject all mixed public/private answers. The validated address is pinned at connect.
    for value in addresses:
        ip = ipaddress.ip_address(value)
        if not ip.is_global or (
            ip.version == 6 and ip.ipv4_mapped is not None and not ip.ipv4_mapped.is_global
        ):
            raise MailFailure("MAIL_TARGET_BLOCKED", "邮箱连接只允许公网服务器，不允许内网、回环或保留地址")
    return addresses[0]


def start_opening_limit(client, sock, timeout):
    # Socket inactivity timeout alone does not stop a server dripping banner bytes.
    if not getattr(client, "opening", False):
        return sock
    client.opening_socket = sock

    def abort():
        try:
            sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass

    client.opening_timer = threading.Timer(timeout, abort)
    client.opening_timer.daemon = True
    client.opening_timer.start()
    return sock


def finish_opening(client, failed=False):
    if client.opening_timer:
        client.opening_timer.cancel()
    client.opening = False
    if failed:
        for value in (getattr(client, "file", None), getattr(client, "opening_socket", None)):
            if value is not None:
                try:
                    value.close()
                except OSError:
                    pass


class PinnedIMAP(imaplib.IMAP4):
    def __init__(self, host, port, *, address, implicit, context, timeout, maximum):
        self.address, self.implicit, self.context, self.maximum = address, implicit, context, maximum
        self.opening, self.opening_timer = True, None
        try:
            super().__init__(host, port, timeout=timeout)
        except Exception:
            finish_opening(self, failed=True)
            raise
        finally:
            finish_opening(self)

    def _create_socket(self, timeout):
        sock = socket.create_connection((self.address, self.port), timeout)
        if self.implicit:
            try:
                sock = self.context.wrap_socket(sock, server_hostname=self.host)
                return start_opening_limit(self, sock, timeout)
            except Exception:
                sock.close()
                raise
        return start_opening_limit(self, sock, timeout)

    def read(self, size):
        if size > self.maximum:
            raise MailFailure("MAIL_TOO_LARGE", "服务器邮件字节数超过上限")
        return super().read(size)


class PinnedPOP(poplib.POP3):
    def __init__(self, host, port, *, address, implicit, context, timeout, maximum):
        self.address, self.implicit, self.context, self.maximum = address, implicit, context, maximum
        self.response_bytes = 0
        self.opening, self.opening_timer = True, None
        try:
            super().__init__(host, port, timeout=timeout)
        except Exception:
            finish_opening(self, failed=True)
            raise
        finally:
            finish_opening(self)

    def _create_socket(self, timeout):
        sock = socket.create_connection((self.address, self.port), timeout)
        if self.implicit:
            try:
                sock = self.context.wrap_socket(sock, server_hostname=self.host)
                return start_opening_limit(self, sock, timeout)
            except Exception:
                sock.close()
                raise
        return start_opening_limit(self, sock, timeout)

    def _getline(self):
        line, size = super()._getline()
        self.response_bytes += size
        if self.response_bytes > self.maximum:
            raise MailFailure("MAIL_TOO_LARGE", "服务器响应超过字节数上限")
        return line, size

    def reset_limit(self, maximum=None):
        self.response_bytes = 0
        if maximum is not None:
            self.maximum = maximum


def imap_date(value):
    months = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    return f"{value.day:02d}-{months[value.month - 1]}-{value.year}"


def safe_imap_folder(folder):
    try:
        folder.encode("ascii")
    except UnicodeError as e:
        raise MailFailure(
            "IMAP_FOLDER_UNSUPPORTED",
            "首版 IMAP 文件夹请使用服务商提供的 ASCII 或 modified UTF-7 名称",
            unsupported=True,
        ) from e
    return '"' + folder.replace("\\", "\\\\").replace('"', '\\"') + '"'


class MailConnection:
    def __init__(self, config: dict, settings):
        self.config, self.settings, self.client = config, settings, None
        self.uidvalidity = ""
        self.uid_map: dict[str, int] = {}
        self.sizes: dict[int, int] = {}
        self.top_supported = True
        self.deadline = None

    def __enter__(self):
        c = self.config
        context = ssl.create_default_context()
        address = public_address(c["host"], c["port"], self.settings.mail_timeout_seconds)
        params = dict(
            address=address,
            implicit=c["tls_mode"] == "ssl",
            context=context,
            timeout=self.settings.mail_timeout_seconds,
            maximum=self.settings.mail_max_bytes,
        )
        try:
            if c["protocol"] == "imap":
                self.client = PinnedIMAP(c["host"], c["port"], **params)
                self.start_deadline()
                if c["tls_mode"] == "starttls":
                    self.client.starttls(ssl_context=context)
                try:
                    self.client.login(c["username"], c["secret"])
                except imaplib.IMAP4.error as e:
                    raise MailFailure(
                        "MAIL_AUTH_FAILED", "邮箱认证失败，请检查密码、授权码或服务商限制"
                    ) from e
                status, _ = self.client.select(safe_imap_folder(c["folder"]), readonly=True)
                if status != "OK":
                    raise MailFailure("IMAP_FOLDER_FAILED", "无法以只读方式打开指定文件夹")
                _, values = self.client.response("UIDVALIDITY")
                if not values or not values[0] or not values[0].isdigit():
                    raise MailFailure(
                        "IMAP_UIDVALIDITY_MISSING", "服务器未提供稳定 UIDVALIDITY", unsupported=True
                    )
                self.uidvalidity = values[0].decode("ascii")
            else:
                self.client = PinnedPOP(c["host"], c["port"], **params)
                self.start_deadline()
                if c["tls_mode"] == "starttls":
                    self.client.stls(context=context)
                try:
                    self.client.user(c["username"])
                    self.client.pass_(c["secret"])
                except poplib.error_proto as e:
                    raise MailFailure(
                        "MAIL_AUTH_FAILED", "邮箱认证失败，请检查密码、授权码或服务商限制"
                    ) from e
                self.client.reset_limit(2 * 1024 * 1024)
                try:
                    _, lines, _ = self.client.uidl()
                except poplib.error_proto as e:
                    raise MailFailure(
                        "POP3_UIDL_UNSUPPORTED",
                        "服务器不支持 UIDL，首版不能自动同步；请改用 IMAP 或手工导入",
                        unsupported=True,
                    ) from e
                for line in lines:
                    fields = line.split()
                    if len(fields) != 2 or not fields[0].isdigit() or len(fields[1]) > 200:
                        raise MailFailure("POP3_UIDL_INVALID", "服务器 UIDL 格式无效", unsupported=True)
                    uid = fields[1].decode("ascii")
                    if uid in self.uid_map:
                        raise MailFailure("POP3_UIDL_INVALID", "服务器 UIDL 不唯一", unsupported=True)
                    self.uid_map[uid] = int(fields[0])
                self.client.reset_limit(2 * 1024 * 1024)
                _, lines, _ = self.client.list()
                for line in lines:
                    seq, size = line.split()
                    self.sizes[int(seq)] = int(size)
                self.client.reset_limit(self.settings.mail_max_bytes)
            return self
        except Exception as e:
            self.close()
            raise self.translate(e) from e

    def start_deadline(self):
        def abort():
            try:
                self.client.sock.shutdown(socket.SHUT_RDWR)
            except (OSError, AttributeError):
                pass

        self.deadline = threading.Timer(self.settings.sync_budget_seconds, abort)
        self.deadline.daemon = True
        self.deadline.start()

    def close(self):
        # Close transport directly: no server reply is needed, so an unresponsive
        # LOGOUT/QUIT cannot block cleanup. Never mark/delete/move server mail.
        try:
            if self.client is not None:
                if self.config["protocol"] == "imap":
                    self.client.shutdown()
                else:
                    self.client.close()
        except Exception:
            pass
        finally:
            if self.deadline is not None:
                self.deadline.cancel()
                self.deadline = None
            self.client = None

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def translate(error):
        if isinstance(error, MailFailure):
            return error
        if isinstance(error, DomainError):
            return MailFailure(error.code, error.message)
        if isinstance(error, (ssl.SSLError, ssl.CertificateError)):
            return MailFailure("MAIL_TLS_FAILED", "TLS 连接或证书校验失败；不允许跳过证书校验")
        if isinstance(error, (OSError, EOFError, imaplib.IMAP4.abort)):
            return MailFailure("MAIL_CONNECTION_FAILED", "邮箱连接中断或超时", retryable=True)
        return MailFailure("MAIL_PROTOCOL_FAILED", "邮箱协议响应无效或操作不支持")

    def identities(self, cursor: int, from_date: date, to_date: date | None):
        if self.config["protocol"] == "pop3":
            return list(self.uid_map)
        args = ["UID", f"{cursor + 1}:*", "SINCE", imap_date(from_date)]
        if to_date:
            args += ["BEFORE", imap_date(to_date + timedelta(days=1))]
        status, values = self.client.uid("SEARCH", None, *args)
        if status != "OK":
            raise MailFailure("IMAP_SEARCH_FAILED", "IMAP 检索失败", retryable=True)
        ids = (values[0] or b"").split()
        if len(ids) > 100_000 or any(not v.isdigit() for v in ids):
            raise MailFailure("MAIL_SCAN_LIMIT", "服务器返回的检索范围过大或 UID 无效，请缩小范围")
        return [str(v) for v in sorted(set(int(v) for v in ids if int(v) > cursor))]

    def headers(self, uid: str):
        if self.config["protocol"] == "imap":
            status, values = self.client.uid("FETCH", uid, "(RFC822.SIZE BODY.PEEK[HEADER])")
            if status != "OK":
                raise MailFailure("MAIL_FETCH_FAILED", "邮件头获取失败", retryable=True)
            for item in values:
                if isinstance(item, tuple):
                    fields = item[0].replace(b"(", b" ").replace(b")", b" ").split()
                    position = fields.index(b"RFC822.SIZE") if b"RFC822.SIZE" in fields else -1
                    if position < 0 or not fields[position + 1].isdigit():
                        raise MailFailure("MAIL_SIZE_MISSING", "服务器未返回邮件大小")
                    return item[1], int(fields[position + 1]), None
            raise MailFailure("MAIL_GONE", "服务器上的邮件已不可获取")
        seq = self.uid_map.get(uid)
        if seq is None:
            raise MailFailure("MAIL_GONE", "邮件可能被其他客户端删除，历史补扫不能保证完整")
        size = self.sizes.get(seq)
        if size is None:
            raise MailFailure("MAIL_SIZE_MISSING", "服务器未返回邮件大小")
        if size > self.settings.mail_max_bytes:
            return b"", size, None
        if self.top_supported:
            try:
                self.client.reset_limit(min(self.settings.mail_max_bytes, 256 * 1024))
                _, lines, _ = self.client.top(seq, 0)
                return b"\r\n".join(lines) + b"\r\n", size, None
            except poplib.error_proto:
                self.top_supported = False
        raw = self.fetch(uid)
        return raw.split(b"\r\n\r\n", 1)[0] + b"\r\n", size, raw

    def fetch(self, uid: str) -> bytes:
        if self.config["protocol"] == "imap":
            status, values = self.client.uid("FETCH", uid, "(BODY.PEEK[])")
            if status != "OK":
                raise MailFailure("MAIL_FETCH_FAILED", "邮件正文获取失败", retryable=True)
            for item in values:
                if isinstance(item, tuple):
                    data = item[1]
                    if len(data) > self.settings.mail_max_bytes:
                        raise MailFailure("MAIL_TOO_LARGE", "邮件超过字节数上限")
                    return data
            raise MailFailure("MAIL_GONE", "服务器上的邮件已不可获取")
        seq = self.uid_map.get(uid)
        if seq is None:
            raise MailFailure("MAIL_GONE", "邮件可能被其他客户端删除，历史补扫不能保证完整")
        if self.sizes.get(seq, self.settings.mail_max_bytes + 1) > self.settings.mail_max_bytes:
            raise MailFailure("MAIL_TOO_LARGE", "邮件超过字节数上限")
        self.client.reset_limit(self.settings.mail_max_bytes)
        _, lines, _ = self.client.retr(seq)
        raw = b"\r\n".join(lines) + b"\r\n"
        if len(raw) > self.settings.mail_max_bytes:
            raise MailFailure("MAIL_TOO_LARGE", "邮件超过字节数上限")
        return raw
