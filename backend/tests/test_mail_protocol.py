"""Protocol adapter contracts without connecting to a personal mail server."""

import imaplib
import io
import poplib
import ssl
import subprocess
from datetime import date
from email.message import EmailMessage
from types import SimpleNamespace

import pytest
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from app.core import DomainError
from app.mail_input import convert_bytes, prepare_isolated
from app.mail_storage import write_blob
from app.mail_transport import MailConnection, MailFailure, PinnedIMAP, PinnedPOP

LIMITS = dict(chars=250_000, pages=80, attachment=10 * 1024 * 1024)


class IMAP:
    def __init__(self, host, port, **kwargs):
        assert kwargs["context"].verify_mode == ssl.CERT_REQUIRED
        assert kwargs["context"].check_hostname
        self.calls = []
        self.validity = b"12"
        self.closed = False

    def starttls(self, **kwargs):
        self.calls.append("starttls")

    def login(self, username, password):
        self.calls.append("login")

    def select(self, folder, readonly):
        assert readonly is True
        self.calls.append(("select", folder))
        return "OK", [b"2"]

    def response(self, name):
        assert name == "UIDVALIDITY"
        return name, [self.validity]

    def uid(self, command, *args):
        self.calls.append((command, args))
        if command == "SEARCH":
            return "OK", [b"3 2 3 1"]
        if args[1] == "(RFC822.SIZE BODY.PEEK[HEADER])":
            return "OK", [(b"2 (UID 3 RFC822.SIZE 40 BODY[HEADER] {16}", b"Subject: test\r\n")]
        assert args[1] == "(BODY.PEEK[])"
        return "OK", [(b"2 (BODY[] {40}", b"Subject: test\r\n\r\nbody")]

    def shutdown(self):
        self.closed = True


class POP:
    def __init__(self, host, port, **kwargs):
        self.calls = []
        self.closed = False
        self.top_enabled = True
        self.uidl_enabled = True

    def stls(self, **kwargs):
        assert kwargs["context"].check_hostname
        self.calls.append("stls")

    def user(self, username):
        self.calls.append("user")

    def pass_(self, password):
        self.calls.append("pass")

    def reset_limit(self, maximum=None):
        self.calls.append(("limit", maximum))

    def uidl(self):
        if not self.uidl_enabled:
            raise poplib.error_proto("unsupported")
        return b"+OK", [b"9 stable-nine", b"2 stable-two"], 0

    def list(self):
        return b"+OK", [b"9 80", b"2 40"], 0

    def top(self, seq, lines):
        self.calls.append(("top", seq, lines))
        if not self.top_enabled:
            raise poplib.error_proto("unsupported")
        return b"+OK", [b"Subject: test", b""], 0

    def retr(self, seq):
        self.calls.append(("retr", seq))
        return b"+OK", [b"Subject: test", b"", b"body"], 0

    def close(self):
        self.closed = True


def connection(monkeypatch, protocol="imap", tls="ssl", **kwargs):
    client = (IMAP if protocol == "imap" else POP)(
        "mail.example.test", 993, context=ssl.create_default_context()
    )
    for k, value in kwargs.items():
        setattr(client, k, value)
    monkeypatch.setattr("app.mail_transport.public_address", lambda *_: "8.8.8.8")

    def factory(*args, **kw):
        assert kw["context"].verify_mode == ssl.CERT_REQUIRED
        assert kw["context"].check_hostname
        assert kw["implicit"] == (tls == "ssl")
        assert kw["address"] == "8.8.8.8"
        return client

    monkeypatch.setattr(
        "app.mail_transport." + ("PinnedIMAP" if protocol == "imap" else "PinnedPOP"), factory
    )
    settings = SimpleNamespace(mail_max_bytes=1024, mail_timeout_seconds=20, sync_budget_seconds=120)
    cfg = dict(
        protocol=protocol,
        host="mail.example.test",
        port=993,
        tls_mode=tls,
        username="test",
        secret="synthetic-secret",
        folder="INBOX",
    )
    return MailConnection(cfg, settings), client


@pytest.mark.parametrize("tls", ["ssl", "starttls"])
def test_imap_readonly_peek_and_inclusive_date_boundary(monkeypatch, tls):
    conn, client = connection(monkeypatch, tls=tls)
    with conn:
        assert conn.uidvalidity == "12"
        assert conn.identities(2, date(2026, 10, 1), date(2026, 10, 9)) == ["3"]
        search = next(call[1] for call in client.calls if isinstance(call, tuple) and call[0] == "SEARCH")
        assert "10-Oct-2026" in search  # BEFORE is exclusive.
        headers, size, raw = conn.headers("3")
        assert size == 40 and raw is None and b"Subject" in headers
        assert b"body" in conn.fetch("3")
        if tls == "starttls":
            assert client.calls[:2] == ["starttls", "login"]
    assert client.closed and conn.deadline is None


def test_imap_missing_validity_is_explicit_and_closes(monkeypatch):
    conn, client = connection(monkeypatch, validity=None)
    with pytest.raises(MailFailure, match="UIDVALIDITY") as error:
        conn.__enter__()
    assert error.value.unsupported and client.closed


def test_imap_size_limit_and_transport_errors(monkeypatch):
    conn, _ = connection(monkeypatch)
    conn.settings.mail_max_bytes = 3
    with conn, pytest.raises(MailFailure) as error:
        conn.fetch("3")
    assert error.value.code == "MAIL_TOO_LARGE"
    assert MailConnection.translate(ssl.SSLError("private-detail")).code == "MAIL_TLS_FAILED"
    assert "private-detail" not in MailConnection.translate(ssl.SSLError("private-detail")).message


@pytest.mark.parametrize("tls", ["ssl", "starttls"])
def test_pop_uidl_top_readonly_and_no_delete(monkeypatch, tls):
    conn, client = connection(monkeypatch, "pop3", tls)
    with conn:
        assert set(conn.identities(0, date(2026, 10, 1), None)) == {"stable-nine", "stable-two"}
        header, size, raw = conn.headers("stable-nine")
        assert size == 80 and raw is None and b"Subject" in header
        assert ("top", 9, 0) in client.calls
        assert b"body" in conn.fetch("stable-two")
        assert ("retr", 2) in client.calls
        if tls == "starttls":
            assert client.calls[:3] == ["stls", "user", "pass"]
    # Stub has no DELE or other mutation command; an unexpected call fails the test.
    assert client.closed


def test_pop_uidl_unsupported_does_not_fallback_to_sequence(monkeypatch):
    conn, client = connection(monkeypatch, "pop3", uidl_enabled=False)
    with pytest.raises(MailFailure) as error:
        conn.__enter__()
    assert error.value.code == "POP3_UIDL_UNSUPPORTED" and error.value.unsupported
    assert client.closed


def test_pop_top_fallback_and_skip_oversized_without_retr(monkeypatch):
    conn, client = connection(monkeypatch, "pop3", top_enabled=False)
    with conn:
        header, _, raw = conn.headers("stable-two")
        assert raw is not None and b"body" not in header
        assert ("retr", 2) in client.calls
        conn.sizes[9] = 2048
        assert conn.headers("stable-nine") == (b"", 2048, None)
        assert ("retr", 9) not in client.calls


def test_literal_and_pop_response_limits(monkeypatch):
    imap = object.__new__(PinnedIMAP)
    imap.maximum = 4
    with pytest.raises(MailFailure):
        imap.read(5)
    pop = object.__new__(PinnedPOP)
    pop.maximum, pop.response_bytes = 4, 0
    monkeypatch.setattr(poplib.POP3, "_getline", lambda *_: (b"hello", 5))
    with pytest.raises(MailFailure):
        pop._getline()


def pdf_bytes(text=True, pages=1):
    writer = PdfWriter()
    for _ in range(pages):
        page = writer.add_blank_page(width=200, height=200)
        if text:
            font = DictionaryObject(
                {
                    NameObject("/Type"): NameObject("/Font"),
                    NameObject("/Subtype"): NameObject("/Type1"),
                    NameObject("/BaseFont"): NameObject("/Helvetica"),
                }
            )
            page[NameObject("/Resources")] = DictionaryObject(
                {NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})}
            )
            stream = DecodedStreamObject()
            stream.set_data(b"BT /F1 12 Tf 20 100 Td (Synthetic document 123.45) Tj ET")
            page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def test_pdf_text_scan_and_page_limit():
    result = convert_bytes(pdf_bytes(), "application/pdf", LIMITS)
    assert result["state"] == "ready" and "123.45" in result["parts"][0]["text"]
    assert convert_bytes(pdf_bytes(False), "application/pdf", LIMITS)["state"] == "unsupported"
    result = convert_bytes(pdf_bytes(pages=2), "application/pdf", {**LIMITS, "pages": 1})
    assert result["warnings"] == ["PDF_PAGE_LIMIT"]


def test_canonical_artifact_survives_uncommitted_blob_and_different_filename(admin):
    results = []
    for name in ["original.txt", "forwarded.txt"]:
        message = EmailMessage()
        message["From"] = "test@example.test"
        message.set_content("synthetic body")
        message.add_attachment("same attachment", filename=name)
        result = convert_bytes(message.as_bytes(), "message/rfc822", LIMITS)
        results.append(result)
        encoded = __import__("json").dumps(result, ensure_ascii=False).encode()
        write_blob(admin.app.state.settings, encoded, "input", key=result["id"])
    assert results[0] == results[1]


def test_conversion_timeout_safe_error(admin, monkeypatch):
    settings = admin.app.state.settings
    raw_hash = write_blob(settings, b"Subject: synthetic\r\n\r\ntest")

    def timeout(*args, **kwargs):
        assert kwargs["stderr"] == subprocess.DEVNULL
        raise subprocess.TimeoutExpired("private-body", 30)

    monkeypatch.setattr("app.mail_input.subprocess.run", timeout)
    with pytest.raises(DomainError) as error:
        prepare_isolated(settings, raw_hash, "message/rfc822")
    assert error.value.code == "INPUT_TIMEOUT" and "private-body" not in error.value.message


def test_upload_requires_csrf_before_storage(admin):
    from sqlalchemy import func, select

    from app.models import MailSource

    admin.headers.pop("X-CSRF-Token")
    response = admin.post(
        "/api/v1/admin/sources/upload",
        content=b"Subject: test\r\n\r\nbody",
        headers={"X-Filename": "test.eml"},
    )
    assert response.status_code == 403
    with admin.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(MailSource)) == 0


@pytest.mark.parametrize("client_type", [PinnedIMAP, PinnedPOP])
def test_dripping_initial_banner_has_total_deadline(monkeypatch, client_type):
    import socket
    import threading
    import time

    receiving, sending = socket.socketpair()
    stop = threading.Event()

    def drip():
        while not stop.is_set():
            try:
                sending.sendall(b"x")
            except OSError:
                break
            stop.wait(0.01)

    thread = threading.Thread(target=drip, daemon=True)
    thread.start()
    monkeypatch.setattr(socket, "create_connection", lambda *_: receiving)
    start = time.monotonic()
    try:
        with pytest.raises((OSError, imaplib.IMAP4.abort, poplib.error_proto)):
            client_type(
                "mail.example.test",
                143,
                address="8.8.8.8",
                implicit=False,
                context=ssl.create_default_context(),
                timeout=0.15,
                maximum=1024,
            )
        assert time.monotonic() - start < 2
    finally:
        stop.set()
        receiving.close()
        sending.close()
        thread.join(timeout=2)
