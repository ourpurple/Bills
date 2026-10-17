from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from .core import new_id, utc_naive


class Base(DeclarativeBase):
    pass


class Identity:
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_naive)


class Administrator(Identity, Base):
    __tablename__ = "administrators"
    username: Mapped[str] = mapped_column(String(80), unique=True)
    password_hash: Mapped[str] = mapped_column(Text)


class Credential(Identity, Base):
    __tablename__ = "credentials"
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    kind: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(80), default="")
    csrf_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)


class LoginAttempt(Identity, Base):
    __tablename__ = "login_attempts"
    remote: Mapped[str] = mapped_column(String(120), index=True)
    action: Mapped[str] = mapped_column(String(16))


class Bank(Identity, Base):
    __tablename__ = "banks"
    __table_args__ = (CheckConstraint("default_mode IN ('merged','independent')"),)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(80))
    default_mode: Mapped[str] = mapped_column(String(16))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Cardholder(Identity, Base):
    __tablename__ = "cardholders"
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class AccountGroup(Identity, Base):
    __tablename__ = "account_groups"
    bank_id: Mapped[str] = mapped_column(ForeignKey("banks.id"), index=True)
    cardholder_id: Mapped[str] = mapped_column(ForeignKey("cardholders.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Account(Identity, Base):
    __tablename__ = "accounts"
    __table_args__ = (CheckConstraint("mode IN ('merged','independent')"),)
    group_id: Mapped[str] = mapped_column(ForeignKey("account_groups.id"), index=True)
    name: Mapped[str] = mapped_column(String(80))
    mode: Mapped[str] = mapped_column(String(16))
    statement_day: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class Card(Identity, Base):
    __tablename__ = "cards"
    __table_args__ = (UniqueConstraint("account_id", "last_four", name="uq_card_account_tail"),)
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    last_four: Mapped[str] = mapped_column(String(4))
    name: Mapped[str] = mapped_column(String(80))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    credit_limit_minor: Mapped[int | None] = mapped_column(Integer)
    annual_fee_month: Mapped[int | None] = mapped_column(Integer)
    annual_fee_day: Mapped[int | None] = mapped_column(Integer)
    annual_fee_benefits: Mapped[str | None] = mapped_column(Text)


class Statement(Identity, Base):
    __tablename__ = "statements"
    __table_args__ = (
        UniqueConstraint("account_id", "statement_date", "currency", name="uq_statement_period"),
        CheckConstraint("status IN ('draft','confirmed','void')"),
    )
    account_id: Mapped[str] = mapped_column(ForeignKey("accounts.id"), index=True)
    statement_date: Mapped[date] = mapped_column(Date, index=True)
    currency: Mapped[str] = mapped_column(String(3))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    current_version: Mapped[int] = mapped_column(Integer, default=1)
    source_kind: Mapped[str] = mapped_column(String(16), default="manual")


class Revision(Identity, Base):
    __tablename__ = "statement_revisions"
    __table_args__ = (UniqueConstraint("statement_id", "version", name="uq_revision_version"),)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    amount_minor: Mapped[int | None] = mapped_column(Integer)
    minimum_minor: Mapped[int | None] = mapped_column(Integer)
    due_date: Mapped[date | None] = mapped_column(Date)
    period_start: Mapped[date | None] = mapped_column(Date)
    period_end: Mapped[date | None] = mapped_column(Date)
    reason: Mapped[str] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(80))


class Repayment(Identity, Base):
    __tablename__ = "repayments"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_repayment_idempotency"),
        CheckConstraint("amount_minor > 0"),
    )
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    paid_at: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(String(500), default="")
    actor_id: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime)
    revoke_reason: Mapped[str | None] = mapped_column(String(500))


class RepaymentPlan(Identity, Base):
    __tablename__ = "repayment_plans"
    __table_args__ = (
        UniqueConstraint("actor_id", "idempotency_key", name="uq_plan_idempotency"),
        CheckConstraint("amount_minor > 0"),
    )
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id"), index=True)
    amount_minor: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    planned_at: Mapped[datetime] = mapped_column(DateTime)
    note: Mapped[str] = mapped_column(String(500), default="")
    actor_id: Mapped[str] = mapped_column(String(80))
    idempotency_key: Mapped[str] = mapped_column(String(100))
    payload_hash: Mapped[str] = mapped_column(String(64))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime)
    cancel_reason: Mapped[str | None] = mapped_column(String(500))


class AuditEvent(Identity, Base):
    __tablename__ = "audit_events"
    actor: Mapped[str] = mapped_column(String(80))
    action: Mapped[str] = mapped_column(String(80))
    object_id: Mapped[str] = mapped_column(String(80))
    detail: Mapped[str] = mapped_column(Text, default="{}")


class Mailbox(Identity, Base):
    __tablename__ = "mailboxes"
    __table_args__ = (
        CheckConstraint("protocol IN ('imap','pop3')"),
        CheckConstraint("tls_mode IN ('ssl','starttls')"),
    )
    name: Mapped[str] = mapped_column(String(80))
    address: Mapped[str] = mapped_column(String(254))
    username: Mapped[str] = mapped_column(String(254))
    protocol: Mapped[str] = mapped_column(String(8), default="imap")
    host: Mapped[str] = mapped_column(String(253))
    port: Mapped[int] = mapped_column(Integer)
    tls_mode: Mapped[str] = mapped_column(String(12), default="ssl")
    secret_ciphertext: Mapped[str] = mapped_column(Text)
    folder: Mapped[str] = mapped_column(String(120), default="INBOX")
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=30)
    initial_days: Mapped[int] = mapped_column(Integer, default=90)
    sender_filters: Mapped[str] = mapped_column(Text, default="[]")
    subject_filters: Mapped[str] = mapped_column(Text, default="[]")
    config_version: Mapped[int] = mapped_column(Integer, default=1)
    sync_epoch: Mapped[int] = mapped_column(Integer, default=1)
    uidvalidity: Mapped[str | None] = mapped_column(String(80))
    last_uid: Mapped[int] = mapped_column(Integer, default=0)
    next_sync_at: Mapped[datetime] = mapped_column(DateTime, default=utc_naive)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime)
    paused_reason: Mapped[str | None] = mapped_column(String(80))
    last_error_code: Mapped[str | None] = mapped_column(String(80))


class PreparedInput(Base):
    __tablename__ = "prepared_inputs"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_naive)
    state: Mapped[str] = mapped_column(String(16))
    text_chars: Mapped[int] = mapped_column(Integer)
    warnings: Mapped[str] = mapped_column(Text, default="[]")
    converter_version: Mapped[str] = mapped_column(String(20), default="generic-v1")


class MailSource(Identity, Base):
    __tablename__ = "mail_sources"
    __table_args__ = (UniqueConstraint("identity_key", name="uq_mail_source_identity"),)
    mailbox_id: Mapped[str | None] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    identity_key: Mapped[str] = mapped_column(String(64))
    protocol: Mapped[str] = mapped_column(String(10))
    folder: Mapped[str] = mapped_column(String(120), default="")
    uidvalidity: Mapped[str] = mapped_column(String(80), default="")
    remote_id: Mapped[str] = mapped_column(String(200), default="")
    sync_epoch: Mapped[int] = mapped_column(Integer, default=0)
    last_scan_task_id: Mapped[str | None] = mapped_column(String(32))
    filename: Mapped[str] = mapped_column(String(180), default="")
    subject: Mapped[str] = mapped_column(String(500), default="")
    sender: Mapped[str] = mapped_column(String(500), default="")
    message_id: Mapped[str] = mapped_column(String(500), default="")
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    media_type: Mapped[str] = mapped_column(String(60), default="message/rfc822")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    raw_hash: Mapped[str | None] = mapped_column(String(64), index=True)
    input_id: Mapped[str | None] = mapped_column(ForeignKey("prepared_inputs.id"), index=True)
    state: Mapped[str] = mapped_column(String(24), default="pending")
    reason: Mapped[str | None] = mapped_column(String(80))


class Task(Identity, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "state IN ('queued','running','retry_wait','succeeded','failed','unsupported','cancelled')"
        ),
    )
    kind: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    active_key: Mapped[str | None] = mapped_column(String(180), unique=True)
    resource_key: Mapped[str | None] = mapped_column(String(80), index=True)
    mailbox_id: Mapped[str | None] = mapped_column(ForeignKey("mailboxes.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("mail_sources.id"), index=True)
    payload: Mapped[str] = mapped_column(Text, default="{}")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[datetime] = mapped_column(DateTime, default=utc_naive, index=True)
    lease_owner: Mapped[str | None] = mapped_column(String(64))
    lease_token: Mapped[str | None] = mapped_column(String(32))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime, index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    logs_cleared_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(String(300))
    progress: Mapped[int] = mapped_column(Integer, default=0)
    result: Mapped[str] = mapped_column(Text, default="{}")


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(DateTime, default=utc_naive)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utc_naive)
    current_task_id: Mapped[str | None] = mapped_column(String(32))


class ModelConfig(Identity, Base):
    __tablename__ = "model_configs"
    version: Mapped[int] = mapped_column(Integer, default=1)
    base_url: Mapped[str] = mapped_column(String(500))
    model: Mapped[str] = mapped_column(String(160))
    key_ciphertext: Mapped[str] = mapped_column(Text)
    privacy_ciphertext: Mapped[str] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_parse: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_publish_profiles: Mapped[str] = mapped_column(Text, default="[]")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60)
    max_tokens: Mapped[int] = mapped_column(Integer, default=8192)
    input_chars: Mapped[int] = mapped_column(Integer, default=120000)
    daily_call_limit: Mapped[int] = mapped_column(Integer, default=50)
    task_call_limit: Mapped[int] = mapped_column(Integer, default=2)
    json_mode: Mapped[bool] = mapped_column(Boolean, default=False)
    input_price: Mapped[str | None] = mapped_column(String(40))
    output_price: Mapped[str | None] = mapped_column(String(40))
    price_currency: Mapped[str] = mapped_column(String(3), default="CNY")


class ModelRun(Identity, Base):
    __tablename__ = "model_runs"
    cache_key: Mapped[str] = mapped_column(String(180), unique=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("mail_sources.id"), index=True)
    input_id: Mapped[str] = mapped_column(ForeignKey("prepared_inputs.id"), index=True)
    config_version: Mapped[int] = mapped_column(Integer)
    task_id: Mapped[str | None] = mapped_column(ForeignKey("tasks.id"))
    state: Mapped[str] = mapped_column(String(24), default="queued")
    prompt_version: Mapped[str] = mapped_column(String(24), default="bill-prompt-v1")
    schema_version: Mapped[str] = mapped_column(String(24), default="bill-v1")
    validator_version: Mapped[str] = mapped_column(String(24), default="bill-check-v1")
    result_json: Mapped[str] = mapped_column(Text, default="{}")
    warnings: Mapped[str] = mapped_column(Text, default="[]")
    error_code: Mapped[str | None] = mapped_column(String(80))


class ModelCall(Identity, Base):
    __tablename__ = "model_calls"
    task_id: Mapped[str] = mapped_column(ForeignKey("tasks.id"), index=True)
    run_id: Mapped[str | None] = mapped_column(ForeignKey("model_runs.id"), index=True)
    config_version: Mapped[int] = mapped_column(Integer)
    model: Mapped[str] = mapped_column(String(160))
    kind: Mapped[str] = mapped_column(String(24))
    state: Mapped[str] = mapped_column(String(24), default="started")
    finished_at: Mapped[datetime | None] = mapped_column(DateTime)
    error_code: Mapped[str | None] = mapped_column(String(80))
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)
    cost_estimate: Mapped[str | None] = mapped_column(String(60))
    price_currency: Mapped[str] = mapped_column(String(3))
    input_price: Mapped[str | None] = mapped_column(String(40))
    output_price: Mapped[str | None] = mapped_column(String(40))
    prompt_version: Mapped[str] = mapped_column(String(24))
    schema_version: Mapped[str] = mapped_column(String(24))
    validator_version: Mapped[str] = mapped_column(String(24))


class ModelCandidate(Identity, Base):
    __tablename__ = "model_candidates"
    __table_args__ = (UniqueConstraint("run_id", "item_index", name="uq_candidate_run_item"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("model_runs.id"), index=True)
    item_index: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(24), default="pending", index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    extraction_json: Mapped[str] = mapped_column(Text)
    review_json: Mapped[str | None] = mapped_column(Text)
    account_id: Mapped[str | None] = mapped_column(ForeignKey("accounts.id"))
    match_state: Mapped[str] = mapped_column(String(24))
    warnings: Mapped[str] = mapped_column(Text, default="[]")
    statement_id: Mapped[str | None] = mapped_column(ForeignKey("statements.id"), index=True)
    reviewed_by: Mapped[str | None] = mapped_column(String(80))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)


class CandidateRevision(Identity, Base):
    __tablename__ = "candidate_revisions"
    __table_args__ = (UniqueConstraint("candidate_id", "version", name="uq_candidate_revision"),)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("model_candidates.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    review_json: Mapped[str] = mapped_column(Text)
    reason: Mapped[str] = mapped_column(String(500))
    actor: Mapped[str] = mapped_column(String(80))


class StatementDetail(Identity, Base):
    __tablename__ = "statement_details"
    __table_args__ = (UniqueConstraint("statement_id", "version", name="uq_statement_detail_version"),)
    statement_id: Mapped[str] = mapped_column(ForeignKey("statements.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    candidate_id: Mapped[str] = mapped_column(ForeignKey("model_candidates.id"))
    status: Mapped[str] = mapped_column(String(24))
    transactions_json: Mapped[str] = mapped_column(Text, default="[]")
