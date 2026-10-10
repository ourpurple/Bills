"""Durable queue. All network/document work takes place outside SQLite transactions."""

from __future__ import annotations

import json
import threading
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select

from .core import DomainError, new_id, utc_naive
from .mail_crypto import decrypt_secret
from .mail_input import metadata, prepare_isolated
from .mail_storage import content_hash, read_blob, write_blob
from .mail_transport import MailConnection, MailFailure
from .models import Mailbox, MailSource, PreparedInput, Task, WorkerHeartbeat
from .security import audit

ACTIVE = {"queued", "running", "retry_wait"}
TERMINAL = {"succeeded", "failed", "unsupported", "cancelled"}


@contextmanager
def transaction(sessions):
    with sessions() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise


def enqueue(db, kind, *, mailbox=None, source=None, payload=None):
    if kind in {"mail_sync", "mail_test"}:
        key = f"{kind}:{mailbox.id}"
    elif kind in {"mail_fetch", "prepare_input"}:
        key = f"{kind}:{source.id}"
    else:
        raise ValueError("unknown task kind")
    row = db.scalar(select(Task).where(Task.active_key == key))
    if row:
        return row
    args = dict(payload or {})
    if mailbox:
        args.update(config_version=mailbox.config_version, sync_epoch=mailbox.sync_epoch)
    row = Task(
        kind=kind,
        active_key=key,
        resource_key=f"mailbox:{mailbox.id}" if mailbox else None,
        mailbox_id=mailbox.id if mailbox else None,
        source_id=source.id if source else None,
        payload=json.dumps(args),
    )
    db.add(row)
    db.flush()
    return row


def source_identity(mailbox, uidvalidity, uid):
    data = [mailbox["id"], mailbox["sync_epoch"], mailbox["protocol"], mailbox["folder"], uidvalidity, uid]
    return content_hash(json.dumps(data, separators=(",", ":")).encode())


def owned(db, task_id, token):
    row = db.get(Task, task_id)
    if not row or row.state != "running" or row.lease_token != token or row.lease_until <= utc_naive():
        raise DomainError("TASK_LEASE_LOST", "任务租约已失效，旧执行结果被拒绝", 409)
    return row


def guard_mailbox(db, task, allow_inactive=False):
    row = db.get(Mailbox, task.mailbox_id)
    args = json.loads(task.payload)
    if not row or row.config_version != args["config_version"] or row.sync_epoch != args["sync_epoch"]:
        raise DomainError("MAIL_CONFIG_CHANGED", "邮箱配置已更新，请使用新配置重新提交任务", 409)
    if not allow_inactive and (not row.active or row.paused_reason):
        raise DomainError("MAILBOX_PAUSED", "邮箱已停用或因错误暂停", 409)
    return row


def finish_row(row, state, code=None, message=None):
    row.state, row.error_code, row.error_message = state, code, message
    row.lease_owner = row.lease_token = row.lease_until = None
    if state in TERMINAL:
        row.active_key = None
        row.finished_at = utc_naive()


def claim(sessions, settings, worker_id):
    now = utc_naive()
    with transaction(sessions) as db:
        for stale in db.scalars(select(Task).where(Task.state == "running", Task.lease_until <= now)):
            state = "failed" if stale.attempts >= stale.max_attempts else "retry_wait"
            finish_row(stale, state, "TASK_LEASE_EXPIRED", "worker 中断或租约超时；结果可能再次执行")
            stale.available_at = now
            if stale.kind in {"llm_parse", "llm_test"}:
                from .models import ModelCall, ModelRun

                for call in db.scalars(
                    select(ModelCall).where(ModelCall.task_id == stale.id, ModelCall.state == "started")
                ):
                    call.state, call.error_code = "unknown", "TASK_LEASE_EXPIRED"
                run_id = json.loads(stale.payload).get("run_id")
                run = db.get(ModelRun, run_id) if run_id else None
                if run:
                    run.state, run.error_code = state, "TASK_LEASE_EXPIRED"
        busy = list(
            db.scalars(
                select(Task.resource_key).where(Task.state == "running", Task.resource_key.is_not(None))
            )
        )
        query = select(Task).where(Task.state.in_(["queued", "retry_wait"]), Task.available_at <= now)
        if busy:
            query = query.where((Task.resource_key.is_(None)) | Task.resource_key.not_in(busy))
        row = db.scalar(query.order_by(Task.available_at, Task.created_at, Task.id).limit(1))
        if row:
            row.state, row.lease_owner, row.lease_token = "running", worker_id, new_id()
            row.lease_until = now + timedelta(seconds=settings.task_lease_seconds)
            row.attempts += 1
            row.error_code = row.error_message = None
            row.finished_at = None
        heartbeat = db.get(WorkerHeartbeat, worker_id)
        if not heartbeat:
            heartbeat = WorkerHeartbeat(id=worker_id)
            db.add(heartbeat)
        heartbeat.seen_at, heartbeat.current_task_id = now, row.id if row else None
        db.flush()
        return row


def schedule(sessions):
    with transaction(sessions) as db:
        for box in db.scalars(
            select(Mailbox).where(
                Mailbox.active.is_(True), Mailbox.paused_reason.is_(None), Mailbox.next_sync_at <= utc_naive()
            )
        ):
            enqueue(db, "mail_sync", mailbox=box)
            box.next_sync_at = utc_naive() + timedelta(minutes=box.interval_minutes)


def queue_retry(db, row):
    if row.state not in {"failed", "unsupported", "cancelled"}:
        raise DomainError("TASK_NOT_RETRYABLE", "仅失败、不支持或取消的任务可手动重试", 409)
    if row.kind in {"llm_parse", "llm_test"}:
        from .llm_jobs import retry_model

        return retry_model(db, row)
    box = db.get(Mailbox, row.mailbox_id) if row.mailbox_id else None
    if box and row.kind != "mail_test" and (not box.active or box.paused_reason):
        raise DomainError("MAILBOX_PAUSED", "请先修正邮箱配置并测试连接，再重试任务", 409)
    payload = json.loads(row.payload)
    if box:
        if payload.get("sync_epoch") != box.sync_epoch and row.kind == "mail_fetch":
            raise DomainError("MAIL_IDENTITY_CHANGED", "邮箱目标已变更，旧来源不可在新服务器重取", 409)
        payload.update(config_version=box.config_version, sync_epoch=box.sync_epoch)
        if row.kind == "mail_sync":
            payload.pop("cursor", None)
    source = db.get(MailSource, row.source_id) if row.source_id else None
    queued = enqueue(db, row.kind, mailbox=box, source=source, payload=payload)
    if source and row.kind == "prepare_input":
        source.state, source.reason = "pending", None
    return queued


class Worker:
    def __init__(self, settings, sessions, connection_factory=MailConnection, model_client=None):
        self.settings, self.sessions = settings, sessions
        from .llm_adapter import chat

        self.model_client = model_client or chat
        self.connection_factory = connection_factory
        self.id = new_id()

    def heartbeat(self, task):
        with transaction(self.sessions) as db:
            row = owned(db, task.id, task.lease_token)
            row.lease_until = utc_naive() + timedelta(seconds=self.settings.task_lease_seconds)
            beat = db.get(WorkerHeartbeat, self.id)
            beat.seen_at, beat.current_task_id = utc_naive(), task.id

    def snapshot(self, task):
        with transaction(self.sessions) as db:
            current = owned(db, task.id, task.lease_token)
            box = guard_mailbox(db, current, allow_inactive=task.kind == "mail_test")
            fields = (
                "id",
                "name",
                "protocol",
                "host",
                "port",
                "username",
                "folder",
                "tls_mode",
                "initial_days",
                "uidvalidity",
                "last_uid",
                "sync_epoch",
                "config_version",
            )
            config = {k: getattr(box, k) for k in fields}
            config.update(
                sender_filters=json.loads(box.sender_filters), subject_filters=json.loads(box.subject_filters)
            )
            ciphertext = box.secret_ciphertext
        config["secret"] = decrypt_secret(self.settings, ciphertext)
        return config

    def run_once(self, *, automatic=True):
        if automatic:
            schedule(self.sessions)
            from .llm_jobs import schedule_models

            schedule_models(self.sessions)
        task = claim(self.sessions, self.settings, self.id)
        if not task:
            return False
        stopped = threading.Event()

        def renew():
            while not stopped.wait(min(15, self.settings.task_lease_seconds / 3)):
                try:
                    self.heartbeat(task)
                except Exception:
                    return

        thread = threading.Thread(target=renew, daemon=True)
        thread.start()
        try:
            if task.kind in {"llm_parse", "llm_test"}:
                from .llm_jobs import execute

                execute(self, task)
            elif task.kind == "prepare_input":
                self.prepare(task)
            elif task.kind == "mail_test":
                self.test_connection(task)
            elif task.kind == "mail_sync":
                self.sync(task)
            elif task.kind == "mail_fetch":
                self.fetch_one(task)
            else:
                raise DomainError("TASK_KIND_UNSUPPORTED", "任务类型不支持")
        except Exception as error:
            self.fail(task, error)
        finally:
            stopped.set()
            thread.join(timeout=12)
            with transaction(self.sessions) as db:
                beat = db.get(WorkerHeartbeat, self.id)
                if beat:
                    beat.current_task_id, beat.seen_at = None, utc_naive()
        return True

    def fail(self, task, error):
        failure = MailConnection.translate(error)
        if isinstance(error, DomainError):
            failure = MailFailure(error.code, error.message, retryable=error.code in {"STORAGE_QUOTA"})
        with transaction(self.sessions) as db:
            try:
                row = owned(db, task.id, task.lease_token)
            except DomainError:
                return
            cancelled = failure.code in {
                "MAIL_CONFIG_CHANGED",
                "MAILBOX_PAUSED",
                "TASK_LEASE_LOST",
                "LLM_CONFIG_CHANGED",
                "LLM_NOT_CONFIGURED",
            }
            state = (
                "cancelled"
                if cancelled
                else "retry_wait"
                if failure.retryable and row.attempts < row.max_attempts
                else "unsupported"
                if failure.unsupported
                else "failed"
            )
            finish_row(row, state, failure.code, failure.message)
            if state == "retry_wait":
                row.available_at = utc_naive() + timedelta(seconds=min(300, 15 * 2 ** (row.attempts - 1)))
            box = db.get(Mailbox, row.mailbox_id) if row.mailbox_id else None
            if box and not cancelled:
                box.last_error_code = failure.code
                if failure.code in {
                    "MAIL_AUTH_FAILED",
                    "POP3_UIDL_UNSUPPORTED",
                    "POP3_UIDL_INVALID",
                    "SECRET_DECRYPT_FAILED",
                    "MASTER_KEY_REQUIRED",
                    "MAIL_TARGET_BLOCKED",
                    "MAIL_TLS_FAILED",
                    "IMAP_FOLDER_FAILED",
                    "IMAP_FOLDER_UNSUPPORTED",
                    "IMAP_UIDVALIDITY_MISSING",
                }:
                    box.paused_reason = failure.code
            if row.kind == "llm_parse":
                from .models import ModelRun

                run = db.get(ModelRun, json.loads(row.payload)["run_id"])
                if run:
                    run.state, run.error_code = state, failure.code
            if row.source_id and row.kind in {"mail_fetch", "prepare_input"}:
                source = db.get(MailSource, row.source_id)
                if source and not cancelled:
                    source.state, source.reason = (
                        "conversion_failed" if row.kind == "prepare_input" else "fetch_failed",
                        failure.code,
                    )
            audit(db, "worker", "task." + state, row.id, json.dumps({"code": failure.code}))

    def test_connection(self, task):
        config = self.snapshot(task)
        with self.connection_factory(config, self.settings) as connection:
            result = dict(
                protocol=config["protocol"],
                readonly=True,
                uidl_supported=config["protocol"] == "pop3",
                uidvalidity=connection.uidvalidity,
            )
        with transaction(self.sessions) as db:
            row = owned(db, task.id, task.lease_token)
            box = guard_mailbox(db, row, allow_inactive=True)
            box.last_test_at, box.paused_reason, box.last_error_code = utc_naive(), None, None
            row.result = json.dumps(result)
            finish_row(row, "succeeded")
            audit(db, "worker", "mailbox.test_succeeded", box.id)

    def range_for(self, config, payload):
        start = (
            date.fromisoformat(payload["from_date"])
            if payload.get("from_date")
            else date.fromisoformat(payload["range_start"])
            if payload.get("range_start")
            else datetime.now(ZoneInfo("Asia/Shanghai")).date() - timedelta(days=config["initial_days"])
        )
        end = date.fromisoformat(payload["to_date"]) if payload.get("to_date") else None
        return start, end

    def sync(self, task):
        config, args = self.snapshot(task), json.loads(task.payload)
        start, end = self.range_for(config, args)
        backfill = bool(args.get("from_date"))
        started = time.monotonic()
        with self.connection_factory(config, self.settings) as connection:
            if config["protocol"] == "imap" and not backfill:
                with transaction(self.sessions) as db:
                    row = owned(db, task.id, task.lease_token)
                    box = guard_mailbox(db, row)
                    if box.uidvalidity != connection.uidvalidity:
                        box.uidvalidity, box.last_uid = connection.uidvalidity, 0
                    config["last_uid"] = box.last_uid
            if backfill and args.get("scan_uidvalidity") != connection.uidvalidity:
                args["cursor"] = 0
            args["scan_uidvalidity"] = connection.uidvalidity
            args["range_start"] = start.isoformat()
            cursor = args.get("cursor", 0) if backfill else config["last_uid"]
            identities = connection.identities(cursor, start, end)
            if config["protocol"] == "pop3":
                with transaction(self.sessions) as db:
                    known = {
                        s.remote_id: s
                        for s in db.scalars(
                            select(MailSource).where(
                                MailSource.mailbox_id == config["id"],
                                MailSource.sync_epoch == config["sync_epoch"],
                            )
                        )
                    }
                    identities = [
                        uid
                        for uid in identities
                        if uid not in known
                        or (
                            backfill
                            and known[uid].state == "skipped"
                            and known[uid].last_scan_task_id != task.id
                        )
                    ]
            done = 0
            for uid in identities:
                if (
                    done >= self.settings.sync_batch_size
                    or time.monotonic() - started >= self.settings.sync_budget_seconds
                ):
                    break
                self.ingest(task, config, connection, uid, start, end)
                done += 1
                if config["protocol"] == "imap":
                    cursor = int(uid)
            more = done < len(identities)
        with transaction(self.sessions) as db:
            row = owned(db, task.id, task.lease_token)
            box = guard_mailbox(db, row)
            if more:
                payload = args
                if backfill and config["protocol"] == "imap":
                    payload["cursor"] = cursor
                row.payload = json.dumps(payload)
                row.state, row.available_at = "queued", utc_naive()
                row.lease_owner = row.lease_token = row.lease_until = None
                row.attempts = 0  # Retry budget applies independently to each completed batch.
            else:
                box.last_success_at, box.last_error_code = utc_naive(), None
                box.next_sync_at = utc_naive() + timedelta(minutes=box.interval_minutes)
                row.result = json.dumps(
                    dict(
                        scanned=row.progress,
                        backfill=backfill,
                        historical_completeness="not_guaranteed"
                        if config["protocol"] == "pop3"
                        else "server_available_range",
                    )
                )
                finish_row(row, "succeeded")

    def ingest(self, task, config, connection, uid, start, end):
        identity = source_identity(config, connection.uidvalidity, uid)
        with transaction(self.sessions) as db:
            row = owned(db, task.id, task.lease_token)
            guard_mailbox(db, row)
            source = db.scalar(select(MailSource).where(MailSource.identity_key == identity))
            already = bool(source and (source.raw_hash or source.state not in {"skipped", "fetch_failed"}))
        data, info, reason, size, failure = None, {}, None, 0, None
        if not already:
            try:
                header, size, cached = connection.headers(uid)
                info = metadata(header)
                if size > self.settings.mail_max_bytes:
                    reason = "MAIL_TOO_LARGE"
                elif not info["sent_at"]:
                    reason = "MAIL_DATE_UNKNOWN"
                elif info["sent_at"].replace(tzinfo=timezone.utc).astimezone(
                    ZoneInfo("Asia/Shanghai")
                ).date() < start or (
                    end
                    and info["sent_at"]
                    .replace(tzinfo=timezone.utc)
                    .astimezone(ZoneInfo("Asia/Shanghai"))
                    .date()
                    > end
                ):
                    reason = "OUTSIDE_RANGE"
                elif config["sender_filters"] and not any(
                    v.casefold() in info["sender"].casefold() for v in config["sender_filters"]
                ):
                    reason = "FILTERED"
                elif config["subject_filters"] and not any(
                    v.casefold() in info["subject"].casefold() for v in config["subject_filters"]
                ):
                    reason = "FILTERED"
                else:
                    data = cached if cached is not None else connection.fetch(uid)
                    if len(data) > self.settings.mail_max_bytes:
                        raise MailFailure("MAIL_TOO_LARGE", "邮件超过字节数上限")
            except Exception as e:
                failure = MailConnection.translate(e)
                if failure.code == "MAIL_TOO_LARGE":
                    reason, failure = failure.code, None
                elif not failure.retryable:
                    reason = failure.code
        with transaction(self.sessions) as db:
            row = owned(db, task.id, task.lease_token)
            box = guard_mailbox(db, row)
            source = db.scalar(select(MailSource).where(MailSource.identity_key == identity))
            if not source:
                source = MailSource(
                    mailbox_id=box.id,
                    identity_key=identity,
                    protocol=config["protocol"],
                    folder=config["folder"],
                    uidvalidity=connection.uidvalidity,
                    remote_id=uid,
                    sync_epoch=config["sync_epoch"],
                )
                db.add(source)
                db.flush()
            source.last_scan_task_id = task.id
            if not already:
                for key, value in info.items():
                    setattr(source, key, value)
                source.size_bytes = len(data) if data is not None else size
                if data is not None:
                    source.raw_hash = write_blob(self.settings, data)
                    source.state, source.reason = "pending", None
                    enqueue(db, "prepare_input", source=source)
                elif reason:
                    source.state, source.reason = (
                        ("skipped" if reason in {"OUTSIDE_RANGE", "FILTERED"} else "unsupported"),
                        reason,
                    )
                elif failure:
                    source.state, source.reason = "fetch_failed", failure.code
                    if failure.retryable:
                        payload = dict(from_date=start.isoformat(), to_date=end.isoformat() if end else None)
                        enqueue(db, "mail_fetch", mailbox=box, source=source, payload=payload)
            # Source/failure task and cursor are committed together; never skip an untracked failure.
            if config["protocol"] == "imap" and not json.loads(row.payload).get("from_date"):
                box.last_uid = max(box.last_uid, int(uid))
            row.progress += 1
        if failure and not failure.retryable:
            raise failure

    def fetch_one(self, task):
        config, args = self.snapshot(task), json.loads(task.payload)
        start, end = self.range_for(config, args)
        with transaction(self.sessions) as db:
            source = db.get(MailSource, task.source_id)
            uid, expected_validity = source.remote_id, source.uidvalidity
            if source.raw_hash:
                row = owned(db, task.id, task.lease_token)
                finish_row(row, "succeeded")
                return
        with self.connection_factory(config, self.settings) as connection:
            if config["protocol"] == "imap" and connection.uidvalidity != expected_validity:
                raise MailFailure(
                    "MAIL_UIDVALIDITY_CHANGED",
                    "UIDVALIDITY 已变化，旧来源不能按旧 UID 重取",
                    unsupported=True,
                )
            self.ingest(task, config, connection, uid, start, end)
        with transaction(self.sessions) as db:
            row = owned(db, task.id, task.lease_token)
            source = db.get(MailSource, row.source_id)
            if source.state == "fetch_failed":
                raise MailFailure(source.reason, "邮件重取失败", retryable=True)
            finish_row(
                row,
                "succeeded" if source.state in {"pending", "ready", "partial", "skipped"} else "unsupported",
            )

    def prepare(self, task):
        with transaction(self.sessions) as db:
            row = owned(db, task.id, task.lease_token)
            source = db.get(MailSource, row.source_id)
            if not source.raw_hash:
                raise DomainError("SOURCE_UNAVAILABLE", "原始资料不存在", 404)
            raw_hash, media_type = source.raw_hash, source.media_type
        read_blob(self.settings, raw_hash)  # Verify immutable source before handing it to subprocess.
        result = prepare_isolated(self.settings, raw_hash, media_type)
        with transaction(self.sessions) as db:
            row = owned(db, task.id, task.lease_token)
            source = db.get(MailSource, row.source_id)
            content = db.get(PreparedInput, result["id"])
            if not content:
                write_blob(
                    self.settings, json.dumps(result, ensure_ascii=False).encode(), "input", key=result["id"]
                )
                content = PreparedInput(
                    id=result["id"],
                    state=result["state"],
                    warnings=json.dumps(result["warnings"]),
                    text_chars=result["text_chars"],
                )
                db.add(content)
                db.flush()
            source.input_id, source.state = content.id, content.state
            source.reason = (
                "INPUT_PARTIAL"
                if source.state == "partial"
                else "INPUT_UNSUPPORTED"
                if source.state == "unsupported"
                else None
            )
            row.result = json.dumps(
                dict(
                    input_id=content.id,
                    state=content.state,
                    text_chars=content.text_chars,
                    warnings=json.loads(content.warnings),
                    llm_parsed=False,
                )
            )
            finish_row(row, "unsupported" if content.state == "unsupported" else "succeeded")
            audit(db, "worker", "source.input_prepared", source.id, json.dumps({"state": source.state}))
