from __future__ import annotations

import asyncio
import json
from datetime import timedelta
from urllib.parse import unquote

from fastapi import APIRouter, Depends, Header, Query, Request
from sqlalchemy import func, select

from .core import DomainError, utc_naive
from .mail_crypto import encrypt_secret
from .mail_input import metadata
from .mail_jobs import ACTIVE, enqueue, queue_reprocess, queue_retry
from .mail_schemas import MailboxConfig, ScanRange, SourceReprocess
from .mail_storage import content_hash, read_blob, write_blob
from .models import Mailbox, MailSource, Task, WorkerHeartbeat
from .security import administrator, audit, get_db
from .services import require, stamp

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(administrator, scope="function")])


def paginated(db, query, serializer, number, size):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.scalars(query.offset((number - 1) * size).limit(size))
    return dict(items=[serializer(r) for r in rows], total=total, page=number, page_size=size)


def mailbox_json(row):
    fields = (
        "id",
        "name",
        "address",
        "username",
        "protocol",
        "host",
        "port",
        "tls_mode",
        "folder",
        "active",
        "interval_minutes",
        "initial_days",
        "config_version",
        "sync_epoch",
        "paused_reason",
        "last_error_code",
        "last_uid",
        "uidvalidity",
    )
    data = {k: getattr(row, k) for k in fields}
    data.update(
        secret_configured=bool(row.secret_ciphertext),
        sender_filters=json.loads(row.sender_filters),
        subject_filters=json.loads(row.subject_filters),
    )
    for k in ("next_sync_at", "last_success_at", "last_test_at", "created_at"):
        data[k] = stamp(getattr(row, k))
    return data


def task_json(row):
    fields = (
        "id",
        "kind",
        "state",
        "mailbox_id",
        "source_id",
        "attempts",
        "max_attempts",
        "error_code",
        "error_message",
        "progress",
    )
    data = {k: getattr(row, k) for k in fields}
    for k in ("created_at", "available_at", "finished_at", "lease_until"):
        data[k] = stamp(getattr(row, k))
    data["result"] = json.loads(row.result)
    # No secrets, task payloads, private body, or worker internals are returned.
    return data


def source_json(row):
    fields = (
        "id",
        "mailbox_id",
        "protocol",
        "filename",
        "subject",
        "sender",
        "message_id",
        "size_bytes",
        "media_type",
        "state",
        "reason",
        "input_id",
    )
    data = {k: getattr(row, k) for k in fields}
    data.update(
        sent_at=stamp(row.sent_at),
        created_at=stamp(row.created_at),
        raw_available=bool(row.raw_hash),
        llm_parsed=False,
    )
    return data


def apply_config(row, data, settings):
    fields = data.model_dump(exclude={"secret", "expected_version", "sender_filters", "subject_filters"})
    for key, value in fields.items():
        setattr(row, key, value)
    row.sender_filters, row.subject_filters = (
        json.dumps(data.sender_filters),
        json.dumps(data.subject_filters),
    )
    if data.secret is not None:
        row.secret_ciphertext = encrypt_secret(settings, data.secret)
    row.paused_reason = row.last_error_code = None
    row.next_sync_at = utc_naive()


@router.get("/mailboxes")
def mailboxes(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db=Depends(get_db, scope="function")
):
    return paginated(
        db, select(Mailbox).order_by(Mailbox.created_at, Mailbox.id), mailbox_json, page, page_size
    )


@router.post("/mailboxes", status_code=201)
def create_mailbox(
    data: MailboxConfig,
    request: Request,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    if data.secret is None:
        raise DomainError("MAIL_SECRET_REQUIRED", "新邮箱必须填写密码或授权码")
    row = Mailbox()
    apply_config(row, data, request.app.state.settings)
    db.add(row)
    db.flush()
    audit(db, actor.id, "mailbox.created", row.id)
    return mailbox_json(row)


@router.patch("/mailboxes/{object_id}")
def edit_mailbox(
    object_id: str,
    data: MailboxConfig,
    request: Request,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    row = require(db, Mailbox, object_id)
    if data.expected_version != row.config_version:
        raise DomainError("VERSION_CONFLICT", "邮箱配置已被修改，请刷新后重试", 409)
    changed_target = any(
        getattr(row, k) != getattr(data, k) for k in ("protocol", "host", "port", "username", "folder")
    )
    if changed_target:
        row.sync_epoch += 1
        row.uidvalidity, row.last_uid = None, 0
        row.last_success_at = row.last_test_at = None
    apply_config(row, data, request.app.state.settings)
    row.config_version += 1
    audit(db, actor.id, "mailbox.updated", row.id, json.dumps({"target_changed": changed_target}))
    return mailbox_json(row)


@router.post("/mailboxes/{object_id}/test", status_code=202)
def test_mailbox(
    object_id: str, db=Depends(get_db, scope="function"), actor=Depends(administrator, scope="function")
):
    row = require(db, Mailbox, object_id)
    task = enqueue(db, "mail_test", mailbox=row)
    audit(db, actor.id, "mailbox.test_queued", row.id)
    return task_json(task)


@router.post("/mailboxes/{object_id}/sync", status_code=202)
def sync_mailbox(
    object_id: str,
    data: ScanRange,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    row = require(db, Mailbox, object_id)
    if not row.active or row.paused_reason:
        raise DomainError("MAILBOX_PAUSED", "请先启用邮箱并修正连接错误", 409)
    existing = db.scalar(select(Task).where(Task.active_key == f"mail_sync:{row.id}"))
    payload = data.model_dump(mode="json", exclude_none=True)
    if existing:
        old = json.loads(existing.payload)
        if {k: old.get(k) for k in ("from_date", "to_date")} != {
            k: payload.get(k) for k in ("from_date", "to_date")
        }:
            raise DomainError("MAIL_SYNC_BUSY", "已有其他范围的同步任务，请等待结束后补扫", 409)
        task = existing
    else:
        task = enqueue(db, "mail_sync", mailbox=row, payload=payload)
    audit(db, actor.id, "mailbox.sync_queued", row.id)
    return task_json(task)


@router.get("/tasks")
def tasks(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: str | None = None,
    mailbox_id: str | None = None,
    db=Depends(get_db, scope="function"),
):
    query = select(Task)
    if state:
        query = query.where(Task.state == state)
    if mailbox_id:
        query = query.where(Task.mailbox_id == mailbox_id)
    return paginated(db, query.order_by(Task.created_at.desc(), Task.id), task_json, page, page_size)


@router.post("/tasks/{object_id}/retry", status_code=202)
def retry_task(
    object_id: str, db=Depends(get_db, scope="function"), actor=Depends(administrator, scope="function")
):
    row = require(db, Task, object_id)
    queued = queue_retry(db, row)
    audit(db, actor.id, "task.retry_queued", row.id)
    return task_json(queued)


@router.get("/import-status")
def import_status(request: Request, db=Depends(get_db, scope="function")):
    workers = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.seen_at.desc())).all()
    now = utc_naive()
    states = dict(db.execute(select(Task.state, func.count(Task.id)).group_by(Task.state)).all())
    return dict(
        stage="P3",
        master_key_configured=bool(request.app.state.settings.master_key),
        worker_online=any(
            w.seen_at >= now - timedelta(seconds=max(45, request.app.state.settings.worker_poll_seconds * 3))
            for w in workers
        ),
        last_heartbeat=stamp(workers[0].seen_at) if workers else None,
        tasks=states,
        ready_sources=db.scalar(
            select(func.count()).select_from(MailSource).where(MailSource.state.in_(["ready", "partial"]))
        ),
        llm_parser="available",
        reminders="not_implemented",
    )


@router.get("/sources")
def sources(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: str | None = None,
    mailbox_id: str | None = None,
    db=Depends(get_db, scope="function"),
):
    query = select(MailSource)
    if state:
        query = query.where(MailSource.state == state)
    if mailbox_id:
        query = query.where(MailSource.mailbox_id == mailbox_id)
    return paginated(
        db, query.order_by(MailSource.created_at.desc(), MailSource.id), source_json, page, page_size
    )


@router.post("/sources/upload", status_code=202)
async def upload_source(
    request: Request,
    x_filename: str = Header(..., max_length=1500),
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    name = unquote(x_filename).replace("\\", "/").split("/")[-1][:180]
    if not name or any(ord(c) < 32 or ord(c) == 127 for c in name):
        raise DomainError("INVALID_FILENAME", "文件名无效")
    extension = name.rsplit(".", 1)[-1].lower()
    if extension not in {"eml", "pdf"}:
        raise DomainError("UPLOAD_UNSUPPORTED", "仅支持 .eml 邮件或文本型 PDF")
    settings = request.app.state.settings
    maximum = settings.mail_max_bytes if extension == "eml" else settings.attachment_max_bytes
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError as e:
        raise DomainError("UPLOAD_LENGTH_INVALID", "上传长度无效") from e
    if length < 0 or length > maximum:
        raise DomainError("UPLOAD_TOO_LARGE", "文件超过大小上限", 413)
    # Authentication/CSRF finish before slow body reception; no DB write lock held.
    db.commit()
    raw = bytearray()
    try:
        async with asyncio.timeout(20):
            async for chunk in request.stream():
                if len(raw) + len(chunk) > maximum:
                    raise DomainError("UPLOAD_TOO_LARGE", "文件超过大小上限", 413)
                raw.extend(chunk)
    except TimeoutError as e:
        raise DomainError("UPLOAD_TIMEOUT", "上传超时，请重试", 408) from e
    if not raw:
        raise DomainError("UPLOAD_EMPTY", "不能上传空文件")
    if extension == "pdf" and not raw.startswith(b"%PDF-"):
        raise DomainError("UPLOAD_INVALID_PDF", "文件不是 PDF")
    raw = bytes(raw)
    key = content_hash(raw)
    identity = content_hash(("upload:" + extension + ":" + key).encode())
    db.connection().exec_driver_sql("BEGIN IMMEDIATE")
    source = db.scalar(select(MailSource).where(MailSource.identity_key == identity))
    duplicate = source is not None
    if not source:
        if db.scalar(select(func.count()).select_from(Task).where(Task.state.in_(ACTIVE))) >= 1000:
            raise DomainError("QUEUE_FULL", "待执行任务较多，请等待 worker 处理", 429)
        source = MailSource(
            identity_key=identity,
            protocol="upload",
            filename=name,
            media_type="application/pdf" if extension == "pdf" else "message/rfc822",
            raw_hash=write_blob(settings, raw),
            size_bytes=len(raw),
        )
        if extension == "eml":
            for k, v in metadata(raw).items():
                setattr(source, k, v)
        db.add(source)
        db.flush()
        task = enqueue(db, "prepare_input", source=source)
    else:
        task = db.scalar(
            select(Task).where(Task.source_id == source.id).order_by(Task.created_at.desc()).limit(1)
        )
    audit(db, actor.id, "source.uploaded", source.id)
    return dict(
        source=source_json(source),
        task=task_json(task) if task else None,
        duplicate=duplicate,
    )


@router.post("/sources/{object_id}/reprocess", status_code=202)
def reprocess_source(
    object_id: str,
    body: SourceReprocess,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    source = require(db, MailSource, object_id)
    task = queue_reprocess(db, source, body.mode)
    audit(db, actor.id, "source." + body.mode + "_queued", source.id)
    return task_json(task)


@router.get("/sources/{object_id}/preview")
def preview_source(object_id: str, request: Request, db=Depends(get_db, scope="function")):
    source = require(db, MailSource, object_id)
    if not source.input_id:
        raise DomainError("INPUT_NOT_READY", "资料尚未转换，请查看任务状态", 409)
    content = json.loads(read_blob(request.app.state.settings, source.input_id, "input"))
    from .models import ModelRun

    parsed = db.scalars(
        select(ModelRun).where(ModelRun.input_id == source.input_id, ModelRun.state == "succeeded")
    ).all()
    return dict(
        source=source_json(source),
        input=content,
        llm_parsed=any(json.loads(run.result_json).get("is_bill", False) for run in parsed),
        model_run_ids=[r.id for r in parsed],
    )
