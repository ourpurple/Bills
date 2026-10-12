import json
import socket
from datetime import timedelta
from email.message import EmailMessage
from pathlib import Path
from urllib.parse import quote

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from sqlalchemy import func, select

from app.core import DomainError, utc_naive
from app.mail_crypto import decrypt_secret
from app.mail_input import convert_bytes
from app.mail_jobs import Worker, claim, enqueue, owned, schedule, transaction
from app.mail_storage import blob_path, read_blob, write_blob
from app.mail_transport import MailConnection, MailFailure, PinnedIMAP, public_address
from app.models import Mailbox, MailSource, PreparedInput, Statement, Task

SECRET = "test-mail-secret-not-for-production"
LIMITS = dict(chars=250_000, pages=80, attachment=10 * 1024 * 1024)


def message(
    body="测试正文，应还 123.45 元。",
    subject="测试电子账单",
    sender="bank@example.test",
    html=False,
    sent="Thu, 08 Oct 2026 10:00:00 +0800",
):
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"], msg["Date"] = sender, "owner@example.test", subject, sent
    if html:
        msg.set_content(body, subtype="html")
    else:
        msg.set_content(body)
    return msg.as_bytes()


def create_box(admin, **extra):
    data = dict(
        name="测试邮箱",
        address="owner@example.test",
        username="owner@example.test",
        host="imap.example.test",
        port=993,
        protocol="imap",
        tls_mode="ssl",
        folder="INBOX",
        active=True,
        secret=SECRET,
    )
    data.update(extra)
    r = admin.post("/api/v1/admin/mailboxes", json=data)
    assert r.status_code == 201, r.text
    return r.json()


def edit_box(admin, row, **extra):
    fields = (
        "name",
        "address",
        "username",
        "host",
        "port",
        "protocol",
        "tls_mode",
        "folder",
        "active",
        "interval_minutes",
        "initial_days",
        "sender_filters",
        "subject_filters",
    )
    data = {k: row[k] for k in fields}
    data.update(expected_version=row["config_version"], **extra)
    r = admin.patch("/api/v1/admin/mailboxes/" + row["id"], json=data)
    assert r.status_code == 200, r.text
    return r.json()


def upload(admin, raw=None, name="测试.eml"):
    return admin.post(
        "/api/v1/admin/sources/upload",
        content=raw if raw is not None else message(),
        headers={"X-Filename": quote(name), "Content-Type": "application/octet-stream"},
    )


def worker(admin, factory=None):
    return Worker(
        admin.app.state.settings, admin.app.state.sessions, connection_factory=factory or MailConnection
    )


def drain(w, cap=30):
    for _ in range(cap):
        if not w.run_once(automatic=False):
            return
    pytest.fail("worker did not drain bounded queue")


def task(admin, ident):
    return next(t for t in admin.get("/api/v1/admin/tasks?page_size=100").json()["items"] if t["id"] == ident)


class FakeFactory:
    def __init__(self, emails=None):
        self.emails = emails or {"1": message()}
        self.validity = "100"
        self.fail_hosts = {}
        self.fail_once = set()
        self.events = []

    def __call__(self, config, settings):
        parent = self

        class Connection:
            uidvalidity = parent.validity if config["protocol"] == "imap" else ""

            def __enter__(self):
                parent.events.append(("connect", config["id"]))
                if config["host"] in parent.fail_hosts:
                    raise parent.fail_hosts[config["host"]]
                return self

            def __exit__(self, *_):
                pass

            def identities(self, cursor, start, end):
                return [k for k in parent.emails if config["protocol"] == "pop3" or int(k) > cursor]

            def headers(self, uid):
                parent.events.append(("headers", uid))
                return parent.emails[uid].split(b"\n\n", 1)[0], len(parent.emails[uid]), None

            def fetch(self, uid):
                parent.events.append(("fetch", uid))
                if uid in parent.fail_once:
                    parent.fail_once.remove(uid)
                    raise MailFailure("MAIL_CONNECTION_FAILED", "测试连接中断", retryable=True)
                return parent.emails[uid]

        return Connection()


def test_mailbox_secret_no_echo_and_optimistic_version(admin):
    box = create_box(admin)
    assert box["secret_configured"]
    assert SECRET not in json.dumps(box)
    with admin.app.state.sessions() as db:
        row = db.get(Mailbox, box["id"])
        assert row.secret_ciphertext != SECRET
        assert decrypt_secret(admin.app.state.settings, row.secret_ciphertext) == SECRET
    box2 = edit_box(admin, box, name="新名称")
    assert box2["config_version"] == 2 and box2["sync_epoch"] == 1
    data = {
        k: box[k] for k in ("name", "address", "username", "host", "port", "protocol", "tls_mode", "folder")
    }
    assert (
        admin.patch("/api/v1/admin/mailboxes/" + box["id"], json=data | {"expected_version": 1}).status_code
        == 409
    )
    target = edit_box(admin, box2, host="other.example.test")
    assert target["sync_epoch"] == 2 and target["last_uid"] == 0
    assert SECRET not in admin.get("/api/v1/admin/audit-events?page_size=100").text


def test_key_missing_and_wrong_key_no_plaintext_fallback(admin):
    settings = admin.app.state.settings
    settings.master_key = ""
    r = admin.post(
        "/api/v1/admin/mailboxes",
        json=dict(
            name="邮箱", address="a@test.example", username="a", host="mail.example.test", secret=SECRET
        ),
    )
    assert r.status_code == 503
    settings.master_key = Fernet.generate_key().decode()
    box = create_box(admin)
    settings.master_key = Fernet.generate_key().decode()
    ident = admin.post(f"/api/v1/admin/mailboxes/{box['id']}/test").json()["id"]
    drain(worker(admin, FakeFactory()))
    assert task(admin, ident)["error_code"] == "SECRET_DECRYPT_FAILED"
    assert SECRET not in admin.get("/api/v1/admin/tasks").text


@pytest.mark.parametrize(
    "extra",
    [
        {"host": "https://mail.example.test"},
        {"folder": "INBOX\r\nUID SEARCH ALL"},
        {"tls_mode": "none"},
        {"port": 0},
        {"protocol": "pop3", "folder": "Archive"},
        {"subject_filters": [""]},
    ],
)
def test_invalid_mail_config(admin, extra):
    r = admin.post(
        "/api/v1/admin/mailboxes",
        json=dict(
            name="邮箱", address="a@test.example", username="a", host="mail.example.test", secret=SECRET
        )
        | extra,
    )
    assert r.status_code == 422


def test_upload_dedup_convert_safe_preview_and_no_statement(admin):
    raw = message(
        '<script>alert("evil")</script><img src="https://evil.test/pixel"><p>测试正文 123.45</p>', html=True
    )
    r = upload(admin, raw, "../../测试.eml")
    assert r.status_code == 202 and not r.json()["duplicate"]
    sid = r.json()["source"]["id"]
    assert upload(admin, raw).json()["duplicate"]
    assert admin.get(f"/api/v1/admin/sources/{sid}/preview").status_code == 409
    drain(worker(admin))
    preview = admin.get(f"/api/v1/admin/sources/{sid}/preview").json()
    assert preview["llm_parsed"] is False
    text = "".join(p["text"] for p in preview["input"]["parts"])
    assert "123.45" in text and "evil" not in text and "<script" not in text
    assert preview["source"]["filename"] == "测试.eml"
    with admin.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Statement)) == 0
        assert db.scalar(select(func.count()).select_from(MailSource)) == 1


@pytest.mark.parametrize(
    "raw,name,code", [(b"", "empty.eml", 422), (b"not pdf", "bad.pdf", 422), (b"anything", "evil.exe", 422)]
)
def test_invalid_upload(admin, raw, name, code):
    assert upload(admin, raw, name).status_code == code


def test_upload_size_stream_limit(admin):
    admin.app.state.settings.mail_max_bytes = 1024
    assert upload(admin, b"x" * 1025).status_code == 413
    assert admin.get("/api/v1/admin/sources").json()["total"] == 0


def test_device_cannot_access_mail_sources_tasks_or_upload(admin):
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    bearer = {
        "Authorization": "Bearer "
        + admin.post("/api/v1/device/pair", json={"code": code, "name": "测试手机"}).json()["token"]
    }
    for path in ("mailboxes", "sources", "tasks", "import-status"):
        assert admin.get("/api/v1/admin/" + path, headers=bearer).status_code == 403
    assert (
        admin.post(
            "/api/v1/admin/sources/upload", content=b"hello", headers=bearer | {"X-Filename": "a.eml"}
        ).status_code
        == 403
    )


def test_sync_duplicate_source_cursor_content_reuse_across_mailboxes(admin):
    box = create_box(admin)
    other = create_box(admin, name="另一邮箱")
    factory = FakeFactory()
    w = worker(admin, factory)
    a = admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={}).json()
    assert admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={}).json()["id"] == a["id"]
    assert (
        admin.post(
            f"/api/v1/admin/mailboxes/{box['id']}/sync",
            json={"from_date": "2026-01-01", "to_date": "2026-10-09"},
        ).status_code
        == 409
    )
    drain(w)
    admin.post(f"/api/v1/admin/mailboxes/{other['id']}/sync", json={})
    drain(w)
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    rows = admin.get("/api/v1/admin/sources").json()["items"]
    assert len(rows) == 2 and rows[0]["input_id"] == rows[1]["input_id"]
    assert len([e for e in factory.events if e[0] == "fetch"]) == 2
    with admin.app.state.sessions() as db:
        assert db.get(Mailbox, box["id"]).last_uid == 1
        assert db.scalar(select(func.count()).select_from(PreparedInput)) == 1


def test_imap_uidvalidity_reset_preserves_content_dedup(admin):
    box = create_box(admin)
    factory = FakeFactory()
    w = worker(admin, factory)
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    factory.validity = "200"
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    rows = admin.get("/api/v1/admin/sources").json()["items"]
    assert len(rows) == 2 and rows[0]["input_id"] == rows[1]["input_id"]
    with admin.app.state.sessions() as db:
        assert db.get(Mailbox, box["id"]).uidvalidity == "200"


def test_batch_continuation_restart_and_progress(admin):
    box = create_box(admin)
    admin.app.state.settings.sync_batch_size = 1
    factory = FakeFactory({"1": message("one"), "2": message("two"), "3": message("three")})
    job = admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={}).json()
    w = worker(admin, factory)
    assert w.run_once(automatic=False)
    assert task(admin, job["id"])["state"] == "queued"
    drain(worker(admin, factory))
    assert task(admin, job["id"])["progress"] == 3
    assert task(admin, job["id"])["state"] == "succeeded"
    assert admin.get("/api/v1/admin/sources").json()["total"] == 3


def test_fetch_failure_tracked_before_cursor_advance(admin):
    box = create_box(admin)
    factory = FakeFactory({"1": message("failed then retry"), "2": message("next mail")})
    factory.fail_once = {"1"}
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    w = worker(admin, factory)
    assert w.run_once(automatic=False)
    with admin.app.state.sessions() as db:
        assert db.get(Mailbox, box["id"]).last_uid == 2
        first = db.scalar(select(MailSource).where(MailSource.remote_id == "1"))
        assert first.state == "fetch_failed"
        assert (
            db.scalar(select(Task).where(Task.source_id == first.id, Task.kind == "mail_fetch")).state
            == "queued"
        )
    drain(w)
    assert all(s["state"] == "ready" for s in admin.get("/api/v1/admin/sources").json()["items"])


def test_one_auth_failure_does_not_block_another_mailbox(admin):
    bad = create_box(admin, host="bad.example.test")
    good = create_box(admin, name="正常邮箱")
    factory = FakeFactory()
    factory.fail_hosts["bad.example.test"] = MailFailure("MAIL_AUTH_FAILED", "认证失败")
    schedule(admin.app.state.sessions)
    drain(worker(admin, factory))
    boxes = {b["id"]: b for b in admin.get("/api/v1/admin/mailboxes").json()["items"]}
    assert boxes[bad["id"]]["paused_reason"] == "MAIL_AUTH_FAILED"
    assert boxes[good["id"]]["last_success_at"]
    assert admin.post(f"/api/v1/admin/mailboxes/{bad['id']}/sync", json={}).status_code == 409
    factory.fail_hosts.clear()
    admin.post(f"/api/v1/admin/mailboxes/{bad['id']}/test")
    drain(worker(admin, factory))
    assert (
        next(b for b in admin.get("/api/v1/admin/mailboxes").json()["items"] if b["id"] == bad["id"])[
            "paused_reason"
        ]
        is None
    )


def test_pop_uidl_identity_not_sequence_and_explicit_backfill(admin):
    box = create_box(admin, protocol="pop3", port=995)
    factory = FakeFactory(
        {"stable-old": message("old", sent="Wed, 01 Jan 2025 10:00:00 +0800"), "stable-new": message("new")}
    )
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(worker(admin, factory))
    sources = admin.get("/api/v1/admin/sources").json()["items"]
    assert len(sources) == 2 and any(s["reason"] == "OUTSIDE_RANGE" for s in sources)
    factory.emails = dict(reversed(list(factory.emails.items())))
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(worker(admin, factory))
    assert len([e for e in factory.events if e[0] == "fetch"]) == 1
    admin.post(
        f"/api/v1/admin/mailboxes/{box['id']}/sync", json={"from_date": "2025-01-01", "to_date": "2025-12-31"}
    )
    drain(worker(admin, factory))
    assert all(s["state"] == "ready" for s in admin.get("/api/v1/admin/sources").json()["items"])


def test_filters_and_oversize_no_body_fetch(admin):
    box = create_box(admin, subject_filters=["账单"])
    factory = FakeFactory({"1": message(subject="广告"), "2": message("x" * 2500)})
    admin.app.state.settings.mail_max_bytes = 1024
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(worker(admin, factory))
    sources = admin.get("/api/v1/admin/sources").json()["items"]
    assert {s["reason"] for s in sources} == {"FILTERED", "MAIL_TOO_LARGE"}
    assert not any(e[0] == "fetch" for e in factory.events)


def test_lease_recovery_resource_exclusion_and_stale_result_rejection(admin):
    box = create_box(admin)
    sessions, settings = admin.app.state.sessions, admin.app.state.settings
    with transaction(sessions) as db:
        b = db.get(Mailbox, box["id"])
        enqueue(db, "mail_sync", mailbox=b)
        enqueue(db, "mail_test", mailbox=b)
    first = claim(sessions, settings, "worker-one")
    assert claim(sessions, settings, "worker-two") is None
    with transaction(sessions) as db:
        db.get(Task, first.id).lease_until = utc_naive() - timedelta(seconds=1)
    second = claim(sessions, settings, "worker-two")
    assert second and second.lease_token != first.lease_token
    with transaction(sessions) as db:
        with pytest.raises(DomainError, match="旧执行結果|旧执行结果"):
            owned(db, first.id, first.lease_token)


def test_retry_backoff_attempt_limit_and_manual_retry(admin):
    box = create_box(admin)
    factory = FakeFactory()
    factory.fail_hosts[box["host"]] = MailFailure("MAIL_CONNECTION_FAILED", "断连", retryable=True)
    ident = admin.post(f"/api/v1/admin/mailboxes/{box['id']}/test").json()["id"]
    w = worker(admin, factory)
    for attempt in range(1, 4):
        assert w.run_once(automatic=False)
        row = task(admin, ident)
        assert row["attempts"] == attempt and row["state"] == ("retry_wait" if attempt < 3 else "failed")
        with transaction(admin.app.state.sessions) as db:
            db.get(Task, ident).available_at = utc_naive() - timedelta(seconds=1)
    assert not w.run_once(automatic=False)
    r = admin.post(f"/api/v1/admin/tasks/{ident}/retry")
    assert r.status_code == 202 and r.json()["id"] != ident
    factory.fail_hosts.clear()
    drain(w)
    assert task(admin, r.json()["id"])["state"] == "succeeded"


def test_config_change_cancels_pending_and_disabled_blocks_sync(admin):
    box = create_box(admin)
    ident = admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={}).json()["id"]
    edit_box(admin, box, active=False)
    drain(worker(admin, FakeFactory()))
    assert task(admin, ident)["state"] == "cancelled"
    assert admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={}).status_code == 409


def test_storage_integrity_quota_and_no_user_path(admin):
    settings = admin.app.state.settings
    key = write_blob(settings, b"safe")
    assert read_blob(settings, key) == b"safe"
    with pytest.raises(DomainError):
        blob_path(settings, "../evil")
    blob_path(settings, key).write_bytes(b"changed")
    with pytest.raises(DomainError):
        read_blob(settings, key)
    settings.storage_max_bytes = 1
    with pytest.raises(DomainError, match="配额"):
        write_blob(settings, b"new")


@pytest.mark.parametrize(
    "address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1", "::ffff:127.0.0.1", "203.0.113.1"]
)
def test_ssrf_targets_blocked(monkeypatch, address):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *_a, **_k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 993))]
    )
    with pytest.raises(MailFailure, match="公网"):
        public_address("mail.example", 993)


def test_public_dns_pinned_and_mixed_answers_blocked(monkeypatch):
    rows = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("8.8.8.8", 993))]
    monkeypatch.setattr(socket, "getaddrinfo", lambda *_a, **_k: rows)
    assert public_address("mail.example", 993) == "8.8.8.8"
    rows.append((socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 993)))
    with pytest.raises(MailFailure):
        public_address("mail.example", 993)
    obj = object.__new__(PinnedIMAP)
    obj.address = "8.8.8.8"
    obj.host = "mail.example"
    obj.port = 993
    obj.implicit = True
    calls = []

    class Context:
        def wrap_socket(self, sock, server_hostname):
            calls.append(server_hostname)
            return sock

    obj.context = Context()
    monkeypatch.setattr(socket, "create_connection", lambda target, timeout: calls.append(target) or object())
    obj._create_socket(10)
    assert calls == [("8.8.8.8", 993), "mail.example"]


def test_generic_conversion_encoding_alternative_unsupported_and_limits():
    msg = EmailMessage()
    msg["Subject"] = "test"
    msg.set_content("正文", charset="gb18030")
    msg.add_alternative("<p>不要重复</p>", subtype="html")
    result = convert_bytes(msg.as_bytes(), "message/rfc822", LIMITS)
    assert result["state"] == "ready" and len(result["parts"]) == 1 and "正文" in result["parts"][0]["text"]
    msg.add_attachment(b"ZIP DATA", maintype="application", subtype="zip", filename="evil.zip")
    result = convert_bytes(msg.as_bytes(), "message/rfc822", LIMITS)
    assert result["state"] == "partial" and "ATTACHMENT_UNSUPPORTED" in result["warnings"]
    r = convert_bytes(message("x" * 100), "message/rfc822", LIMITS | {"chars": 10})
    assert r["text_chars"] == 10 and "TEXT_TRUNCATED" in r["warnings"]
    r = convert_bytes(b"not an eml", "message/rfc822", LIMITS)
    assert r["state"] == "unsupported"


def test_encrypted_invalid_and_empty_pdf_no_parser_fallback():
    import io

    from pypdf import PdfWriter

    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    writer.encrypt("test-password")
    output = io.BytesIO()
    writer.write(output)
    r = convert_bytes(output.getvalue(), "application/pdf", LIMITS)
    assert r["state"] == "unsupported" and "PDF_ENCRYPTED" in r["warnings"]
    assert convert_bytes(b"%PDF-bad", "application/pdf", LIMITS)["state"] == "unsupported"


def test_p1_to_p2_migration_preserves_existing_data(tmp_path, monkeypatch):
    path = tmp_path / "upgrade.db"
    monkeypatch.setenv("BILLS_DATABASE_URL", "sqlite:///" + path.as_posix())
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.upgrade(config, "1f53dca3f94e")
    import sqlite3

    with sqlite3.connect(path) as db:
        db.execute(
            "INSERT INTO banks (id,created_at,code,name,default_mode,active) VALUES (?,?,?,?,?,?)",
            ("bank-id", "2026-10-09 00:00:00", "test", "existing", "merged", 1),
        )
    command.upgrade(config, "head")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM banks").fetchone()[0] == "existing"
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0
    command.downgrade(config, "1f53dca3f94e")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name FROM banks").fetchone()[0] == "existing"


def test_unknown_mail_date_is_explicit_not_silently_outside_range(admin):
    raw = message().replace(b"Date:", b"X-Original-Date:")
    box = create_box(admin)
    factory = FakeFactory({"1": raw})
    response = admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    assert response.status_code == 202
    drain(worker(admin, factory))
    sources = admin.get("/api/v1/admin/sources").json()["items"]
    assert len(sources) == 1
    assert sources[0]["reason"] == "MAIL_DATE_UNKNOWN" and sources[0]["state"] == "unsupported"


def test_reconvert_saved_source_deduplicates_active_job_and_preserves_history(admin):
    response = upload(admin)
    sid = response.json()["source"]["id"]
    drain(worker(admin))
    old = admin.get(f"/api/v1/admin/sources/{sid}/preview").json()
    url = f"/api/v1/admin/sources/{sid}/reprocess"
    queued = admin.post(url, json={"mode": "reconvert"})
    assert queued.status_code == 202
    assert admin.post(url, json={"mode": "reconvert"}).json()["id"] == queued.json()["id"]
    assert admin.get(f"/api/v1/admin/sources/{sid}/preview").status_code == 409
    assert admin.post(url, json={"mode": "refetch"}).status_code == 409
    drain(worker(admin))
    preview = admin.get(f"/api/v1/admin/sources/{sid}/preview").json()
    assert preview["source"]["state"] == "ready"
    assert preview["input"]["id"] == old["input"]["id"]
    with admin.app.state.sessions() as db:
        assert (
            db.get(PreparedInput, preview["input"]["id"]).converter_version
            == preview["input"]["version"]
            == "generic-v2"
        )
    assert task(admin, queued.json()["id"])["state"] == "succeeded"
    assert admin.get("/api/v1/admin/sources").json()["total"] == 1
    assert admin.get("/api/v1/admin/statements").json()["total"] == 0
    assert admin.post(url, json={"mode": "refetch"}).json()["error"]["code"] == "SOURCE_NOT_REFETCHABLE"
    assert admin.post(url, json={"mode": "unknown"}).status_code == 422


@pytest.mark.parametrize("protocol", ["imap", "pop3"])
def test_forced_refetch_downloads_existing_source_and_reconverts_without_rescan(admin, protocol):
    box = create_box(admin, protocol=protocol)
    factory = FakeFactory()
    w = worker(admin, factory)
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    source = admin.get("/api/v1/admin/sources").json()["items"][0]
    sid, old_input = source["id"], source["input_id"]
    with admin.app.state.sessions() as db:
        old_hash = db.get(MailSource, sid).raw_hash
        cursor = db.get(Mailbox, box["id"]).last_uid
    box = edit_box(admin, box, initial_days=1, subject_filters=["不匹配的筛选条件"])
    factory.emails["1"] = message("重新下载的原文 888.88")
    before = len([e for e in factory.events if e[0] == "fetch"])
    url = f"/api/v1/admin/sources/{sid}/reprocess"
    queued = admin.post(url, json={"mode": "refetch"})
    assert queued.status_code == 202, queued.text
    assert admin.post(url, json={"mode": "refetch"}).json()["id"] == queued.json()["id"]
    assert admin.post(url, json={"mode": "reconvert"}).status_code == 409
    drain(w)
    preview = admin.get(f"/api/v1/admin/sources/{sid}/preview").json()
    assert preview["source"]["state"] == "ready"
    assert "888.88" in preview["input"]["parts"][0]["text"]
    assert preview["input"]["id"] != old_input
    assert len([e for e in factory.events if e[0] == "fetch"]) == before + 1
    assert task(admin, queued.json()["id"])["state"] == "succeeded"
    assert admin.get("/api/v1/admin/sources").json()["total"] == 1
    assert admin.get("/api/v1/admin/statements").json()["total"] == 0
    with admin.app.state.sessions() as db:
        assert db.get(Mailbox, box["id"]).last_uid == cursor
        assert db.get(MailSource, sid).raw_hash != old_hash
        assert db.get(PreparedInput, old_input) is not None
    assert read_blob(admin.app.state.settings, old_hash)
    assert read_blob(admin.app.state.settings, old_input, "input")


def test_refetch_rejects_paused_or_changed_mailbox_and_uidvalidity(admin):
    box = create_box(admin)
    factory = FakeFactory()
    w = worker(admin, factory)
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    source = admin.get("/api/v1/admin/sources").json()["items"][0]
    sid = source["id"]
    url = f"/api/v1/admin/sources/{sid}/reprocess"
    box = edit_box(admin, box, active=False)
    assert admin.post(url, json={"mode": "refetch"}).json()["error"]["code"] == "MAILBOX_PAUSED"
    # Saved-file conversion does not need an active mailbox.
    assert admin.post(url, json={"mode": "reconvert"}).status_code == 202
    drain(w)
    box = edit_box(admin, box, active=True)
    factory.validity = "changed"
    before = len([e for e in factory.events if e[0] == "fetch"])
    queued = admin.post(url, json={"mode": "refetch"}).json()
    drain(w)
    assert task(admin, queued["id"])["error_code"] == "MAIL_UIDVALIDITY_CHANGED"
    assert len([e for e in factory.events if e[0] == "fetch"]) == before
    rows = admin.get("/api/v1/admin/sources").json()["items"]
    assert rows[0]["state"] == "fetch_failed" and rows[0]["raw_available"]
    assert admin.post(url, json={"mode": "reconvert"}).status_code == 202
    drain(w)
    box = edit_box(admin, box, host="new.example.test")
    assert admin.post(url, json={"mode": "refetch"}).json()["error"]["code"] == "MAIL_IDENTITY_CHANGED"


def test_reprocess_config_change_does_not_leave_source_pending(admin):
    box = create_box(admin)
    factory = FakeFactory()
    w = worker(admin, factory)
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    source = admin.get("/api/v1/admin/sources").json()["items"][0]
    queued = admin.post(f"/api/v1/admin/sources/{source['id']}/reprocess", json={"mode": "refetch"}).json()
    before = len(factory.events)
    edit_box(admin, box, name="配置已变更")
    drain(w)
    assert task(admin, queued["id"])["state"] == "cancelled"
    assert len(factory.events) == before
    source = admin.get("/api/v1/admin/sources").json()["items"][0]
    assert source["state"] == "fetch_failed" and source["reason"] == "MAIL_CONFIG_CHANGED"


def test_reprocess_refetch_checks_size_before_downloading(admin):
    box = create_box(admin)
    factory = FakeFactory()
    w = worker(admin, factory)
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    source = admin.get("/api/v1/admin/sources").json()["items"][0]
    before = len([e for e in factory.events if e[0] == "fetch"])
    admin.app.state.settings.mail_max_bytes = 10
    queued = admin.post(f"/api/v1/admin/sources/{source['id']}/reprocess", json={"mode": "refetch"}).json()
    drain(w)
    assert task(admin, queued["id"])["error_code"] == "MAIL_TOO_LARGE"
    assert len([e for e in factory.events if e[0] == "fetch"]) == before


def test_reprocess_blocks_running_model_and_requires_admin(admin):
    response = upload(admin)
    sid = response.json()["source"]["id"]
    drain(worker(admin))
    with transaction(admin.app.state.sessions) as db:
        db.add(Task(kind="llm_parse", source_id=sid, state="running", active_key="test-running-model"))
    url = f"/api/v1/admin/sources/{sid}/reprocess"
    assert admin.post(url, json={"mode": "reconvert"}).json()["error"]["code"] == "SOURCE_BUSY"
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    token = admin.post("/api/v1/device/pair", json={"code": code, "name": "测试手机"}).json()["token"]
    assert (
        admin.post(url, json={"mode": "reconvert"}, headers={"Authorization": "Bearer " + token}).status_code
        == 403
    )


def test_charset_compatibility_preserves_text_with_review_warning():
    msg = EmailMessage()
    msg["Subject"] = "test"
    msg.set_content("正文 € 123.45", charset="gb18030")
    msg.set_param("charset", "gb2312", header="Content-Type")
    result = convert_bytes(msg.as_bytes(), "message/rfc822", LIMITS)
    assert result["state"] == "partial"
    assert "正文 € 123.45" in result["parts"][0]["text"]
    assert result["warnings"] == ["TEXT_CHARSET_FALLBACK"]
    msg.set_content("测试 UTF8 123.45", charset="utf-8")
    msg.set_param("charset", "unknown-codec", header="Content-Type")
    result = convert_bytes(msg.as_bytes(), "message/rfc822", LIMITS)
    assert result["state"] == "partial" and "测试 UTF8" in result["parts"][0]["text"]
    raw = b"Subject: test\r\nContent-Type: text/plain; charset=utf-8\r\n\r\n\xff"
    result = convert_bytes(raw, "message/rfc822", LIMITS)
    assert result["state"] == "unsupported" and "TEXT_DECODE_FAILED" in result["warnings"]


@pytest.mark.parametrize("body", ["", "\xff"])
def test_alternative_html_used_when_plain_body_empty_or_undecodable(body):
    msg = EmailMessage()
    msg["Subject"] = "test"
    if body:
        msg.set_content(b"\xff", maintype="text", subtype="plain", cte="base64")
        msg.set_param("charset", "utf-8", header="Content-Type")
    else:
        msg.set_content("")
    msg.add_alternative("<p>备用正文 123.45</p><script>unsafe</script>", subtype="html")
    result = convert_bytes(msg.as_bytes(), "message/rfc822", LIMITS)
    assert result["state"] == "partial" and len(result["parts"]) == 1
    assert result["parts"][0]["text"] == "备用正文 123.45"
    assert "TEXT_ALTERNATIVE_FALLBACK" in result["warnings"]
