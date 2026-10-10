from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .core import DomainError, utc_naive
from .models import (
    Account,
    AccountGroup,
    Administrator,
    Bank,
    Card,
    Cardholder,
    Repayment,
    RepaymentPlan,
    Revision,
    Statement,
    StatementDetail,
)
from .security import audit

CURRENCY_SCALE = {"CNY": 2, "USD": 2, "EUR": 2, "HKD": 2, "JPY": 0}


def require(db, cls, object_id):
    obj = db.get(cls, object_id)
    if obj is None:
        raise DomainError("NOT_FOUND", "记录不存在", 404)
    return obj


def minor(value: Decimal | None, currency: str):
    if value is None:
        return None
    scale = CURRENCY_SCALE[currency]
    scaled = value * (10**scale)
    if scaled != scaled.to_integral_value():
        raise DomainError("MONEY_PRECISION", f"{currency} 金额最多 {scale} 位小数")
    return int(scaled)


def money(value: int | None, currency: str):
    if value is None:
        return None
    scale = CURRENCY_SCALE[currency]
    return f"{Decimal(value) / (10**scale):.{scale}f}"


def stamp(value):
    return value.replace(tzinfo=timezone.utc).isoformat() if value else None


def fields(data, currency, statement_date):
    if data.due_date and data.due_date < statement_date:
        raise DomainError("INVALID_DATES", "到期日不能早于账单日")
    if data.period_start and data.period_start > statement_date:
        raise DomainError("INVALID_DATES", "账期起始不能晚于账单日")
    if data.period_end and data.period_end > statement_date:
        raise DomainError("INVALID_DATES", "账期结束不能晚于账单日")
    return dict(
        amount_minor=minor(data.amount, currency),
        minimum_minor=minor(data.minimum, currency),
        due_date=data.due_date,
        period_start=data.period_start,
        period_end=data.period_end,
    )


def current_revision(db, statement):
    revision = db.scalar(
        select(Revision).where(
            Revision.statement_id == statement.id, Revision.version == statement.current_version
        )
    )
    if revision is None:
        raise DomainError("INCONSISTENT_DATA", "账单版本缺失，请联系管理员", 500)
    return revision


def check_confirmable(revision):
    if revision.amount_minor is None or revision.due_date is None:
        raise DomainError("INCOMPLETE_STATEMENT", "应还金额和到期日未确认，不能发布账单")


def check_version(statement, expected):
    if statement.current_version != expected:
        raise DomainError("VERSION_CONFLICT", "账单已被其他操作修改，请刷新后重试", 409)
    if statement.status == "void":
        raise DomainError("STATEMENT_VOID", "已作废账单不能继续修改", 409)


def account_enabled(db, account):
    group = require(db, AccountGroup, account.group_id)
    return bool(
        account.active
        and group.active
        and require(db, Bank, group.bank_id).active
        and require(db, Cardholder, group.cardholder_id).active
    )


def account_view(db, account):
    group = require(db, AccountGroup, account.group_id)
    bank = require(db, Bank, group.bank_id)
    holder = require(db, Cardholder, group.cardholder_id)
    cards = db.scalars(select(Card).where(Card.account_id == account.id).order_by(Card.created_at)).all()
    return dict(
        id=account.id,
        group_id=group.id,
        name=account.name,
        mode=account.mode,
        statement_day=account.statement_day,
        active=account.active,
        effective_active=account_enabled(db, account),
        bank_id=bank.id,
        bank_name=bank.name,
        bank_code=bank.code,
        cardholder_id=holder.id,
        cardholder_name=holder.name,
        group_name=group.name,
        cards=[dict(id=c.id, name=c.name, last_four=c.last_four, active=c.active) for c in cards],
    )


def repayment_view(row):
    return dict(
        id=row.id,
        statement_id=row.statement_id,
        amount=money(row.amount_minor, row.currency),
        currency=row.currency,
        occurred_at=stamp(row.paid_at),
        note=row.note,
        revoked_at=stamp(row.revoked_at),
        revoke_reason=row.revoke_reason,
        created_at=stamp(row.created_at),
    )


def plan_view(row):
    return dict(
        id=row.id,
        statement_id=row.statement_id,
        amount=money(row.amount_minor, row.currency),
        currency=row.currency,
        occurred_at=stamp(row.planned_at),
        note=row.note,
        cancelled_at=stamp(row.cancelled_at),
        cancel_reason=row.cancel_reason,
        created_at=stamp(row.created_at),
    )


def statement_view(db, statement, details=False):
    revision = current_revision(db, statement)
    paid = db.scalar(
        select(func.coalesce(func.sum(Repayment.amount_minor), 0)).where(
            Repayment.statement_id == statement.id, Repayment.revoked_at.is_(None)
        )
    )
    remaining = None if revision.amount_minor is None else max(revision.amount_minor - paid, 0)
    excess = None if revision.amount_minor is None else max(paid - max(revision.amount_minor, 0), 0)
    payment_status = (
        "unknown"
        if remaining is None
        else ("overpaid" if excess else "settled" if remaining == 0 else "partial" if paid else "unpaid")
    )
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    days = (revision.due_date - today).days if revision.due_date else None
    result = dict(
        id=statement.id,
        account=account_view(db, require(db, Account, statement.account_id)),
        statement_date=statement.statement_date.isoformat(),
        currency=statement.currency,
        status=statement.status,
        version=statement.current_version,
        source_kind=statement.source_kind,
        amount=money(revision.amount_minor, statement.currency),
        minimum=money(revision.minimum_minor, statement.currency),
        due_date=revision.due_date.isoformat() if revision.due_date else None,
        period_start=revision.period_start.isoformat() if revision.period_start else None,
        period_end=revision.period_end.isoformat() if revision.period_end else None,
        paid=money(paid, statement.currency),
        remaining=money(remaining, statement.currency),
        excess=money(excess, statement.currency),
        payment_status=payment_status,
        days_until_due=days,
        overdue=statement.status == "confirmed"
        and remaining is not None
        and remaining > 0
        and days is not None
        and days < 0,
        created_at=stamp(statement.created_at),
        transaction_status="not_provided",
    )
    detail_row = db.scalar(
        select(StatementDetail).where(
            StatementDetail.statement_id == statement.id, StatementDetail.version == statement.current_version
        )
    )
    result["transaction_status"] = detail_row.status if detail_row else "not_provided"
    if details:
        result["transactions"] = json.loads(detail_row.transactions_json) if detail_row else []
        result["detail_history"] = [
            dict(version=d.version, status=d.status, transactions=json.loads(d.transactions_json))
            for d in db.scalars(
                select(StatementDetail)
                .where(StatementDetail.statement_id == statement.id)
                .order_by(StatementDetail.version.desc())
            )
        ]
        result["repayments"] = [
            repayment_view(r)
            for r in db.scalars(
                select(Repayment)
                .where(Repayment.statement_id == statement.id)
                .order_by(Repayment.paid_at.desc())
            ).all()
        ]
        result["plans"] = [
            plan_view(r)
            for r in db.scalars(
                select(RepaymentPlan)
                .where(RepaymentPlan.statement_id == statement.id)
                .order_by(RepaymentPlan.planned_at)
            ).all()
        ]
        result["revisions"] = [
            dict(
                version=r.version,
                amount=money(r.amount_minor, statement.currency),
                minimum=money(r.minimum_minor, statement.currency),
                due_date=r.due_date.isoformat() if r.due_date else None,
                reason=r.reason,
                created_at=stamp(r.created_at),
            )
            for r in db.scalars(
                select(Revision)
                .where(Revision.statement_id == statement.id)
                .order_by(Revision.version.desc())
            ).all()
        ]
    return result


def create_statement(db, data, actor):
    account = require(db, Account, data.account_id)
    group = require(db, AccountGroup, account.group_id)
    bank = require(db, Bank, group.bank_id)
    holder = require(db, Cardholder, group.cardholder_id)
    if not account.active or not group.active or not bank.active or not holder.active:
        raise DomainError("INACTIVE_ACCOUNT", "停用账户不能新增账单")
    existing = db.scalar(
        select(Statement).where(
            Statement.account_id == account.id,
            Statement.statement_date == data.statement_date,
            Statement.currency == data.currency,
        )
    )
    if existing:
        raise DomainError("DUPLICATE_STATEMENT", "该账户、账单日和币种已有账单，请核对或修订", 409)
    values = fields(data, data.currency, data.statement_date)
    row = Statement(account_id=account.id, statement_date=data.statement_date, currency=data.currency)
    db.add(row)
    db.flush()
    db.add(Revision(statement_id=row.id, version=1, reason=data.reason, actor=actor.id, **values))
    db.flush()
    audit(db, actor.id, "statement.created", row.id)
    return statement_view(db, row, True)


def revise_statement(db, row, data, actor):
    check_version(row, data.expected_version)
    values = fields(data, row.currency, row.statement_date)
    revision = Revision(
        statement_id=row.id, version=row.current_version + 1, reason=data.reason, actor=actor.id, **values
    )
    if row.status == "confirmed":
        check_confirmable(revision)
    db.add(revision)
    row.current_version += 1
    db.flush()
    audit(
        db,
        actor.id,
        "statement.revised",
        row.id,
        json.dumps({"version": row.current_version}, ensure_ascii=False),
    )
    return statement_view(db, row, True)


def add_payment(db, row, data, key, actor, planned=False):
    cls = RepaymentPlan if planned else Repayment
    if not key or len(key) > 100:
        raise DomainError("IDEMPOTENCY_REQUIRED", "需要长度不超过 100 的幂等标识")
    payload = data.model_dump(mode="json") | {
        "statement_id": row.id,
        "kind": "plan" if planned else "repayment",
    }
    payload["amount"] = minor(data.amount, data.currency)
    payload["occurred_at"] = data.occurred_at.astimezone(timezone.utc).isoformat()
    payload_hash = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    owner_id = (
        db.scalar(select(Administrator.id).where(Administrator.username == actor.name))
        if actor.kind == "admin"
        else actor.id
    )
    existing = db.scalar(select(cls).where(cls.actor_id == owner_id, cls.idempotency_key == key))
    if existing:
        if existing.payload_hash != payload_hash:
            raise DomainError("IDEMPOTENCY_CONFLICT", "同一个提交标识对应不同内容", 409)
        return plan_view(existing) if planned else repayment_view(existing)
    if row.status != "confirmed":
        raise DomainError("NOT_CONFIRMED", "只有已确认账单可以记录还款或计划")
    if data.currency != row.currency:
        raise DomainError("CURRENCY_MISMATCH", "还款币种必须与账单一致")
    amount = minor(data.amount, row.currency)
    occurred = data.occurred_at.astimezone(timezone.utc).replace(tzinfo=None)
    snapshot = statement_view(db, row)
    local_date = data.occurred_at.astimezone(ZoneInfo("Asia/Shanghai")).date()
    exceptional = (
        amount > minor(Decimal(snapshot["remaining"]), row.currency) or local_date < row.statement_date
    )
    if exceptional and not data.confirm_exception:
        raise DomainError("CONFIRM_EXCEPTION", "金额超过记录待还或时间早于出账，请核对后明确确认")
    if not planned and occurred > utc_naive() + timedelta(minutes=5):
        raise DomainError("FUTURE_REPAYMENT", "未来时间请使用计划还款，而不是已还款标记")
    kwargs = dict(
        statement_id=row.id,
        amount_minor=amount,
        currency=row.currency,
        note=data.note,
        actor_id=owner_id,
        idempotency_key=key,
        payload_hash=payload_hash,
    )
    kwargs["planned_at" if planned else "paid_at"] = occurred
    payment = cls(**kwargs)
    db.add(payment)
    db.flush()
    audit(db, actor.id, "plan.created" if planned else "repayment.recorded", payment.id)
    return plan_view(payment) if planned else repayment_view(payment)


def expected_statement_date(account, today):
    if not account.statement_day:
        return None
    current = today.replace(day=min(account.statement_day, monthrange(today.year, today.month)[1]))
    if current > today:
        previous = today.replace(day=1) - timedelta(days=1)
        return previous.replace(day=min(account.statement_day, previous.day))
    return current


def overview(db):
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
    confirmed = db.scalars(
        select(Statement)
        .where(Statement.status == "confirmed")
        .order_by(Statement.statement_date.desc(), Statement.id)
    ).all()
    latest, historical, seen = [], [], set()
    totals = {}
    for row in confirmed:
        item = statement_view(db, row)
        key = (row.account_id, row.currency)
        if key not in seen:
            seen.add(key)
            latest.append(item)
            totals[row.currency] = totals.get(row.currency, 0) + minor(
                Decimal(item["remaining"]), row.currency
            )
        elif Decimal(item["remaining"]) > 0:
            historical.append(item)
    latest.sort(key=lambda s: (s["remaining"] == "0.00" or s["remaining"] == "0", s["due_date"] or "9999"))
    warnings = []
    active_accounts = [
        a for a in db.scalars(select(Account).where(Account.active.is_(True))).all() if account_enabled(db, a)
    ]
    for account in active_accounts:
        expected = expected_statement_date(account, today)
        account_rows = [s for s in latest if s["account"]["id"] == account.id]
        pending = db.scalar(
            select(func.count())
            .select_from(Statement)
            .where(Statement.account_id == account.id, Statement.status == "draft")
        )
        if pending:
            warnings.append(
                dict(account_id=account.id, account_name=account.name, kind="pending", message="有账单待确认")
            )
        if not account_rows or (
            expected and max(date.fromisoformat(s["statement_date"]) for s in account_rows) < expected
        ):
            warnings.append(
                dict(
                    account_id=account.id,
                    account_name=account.name,
                    kind="missing",
                    message="本期金额未知，请核对账单是否已收到",
                )
            )
    return dict(
        totals=[dict(currency=c, amount=money(v, c)) for c, v in sorted(totals.items())],
        latest_statements=latest,
        historical_unresolved=historical,
        warnings=warnings,
        account_count=len(active_accounts),
        pending_count=db.scalar(
            select(func.count()).select_from(Statement).where(Statement.status == "draft")
        ),
        due_soon_count=sum(
            1
            for s in latest
            if Decimal(s["remaining"]) > 0
            and s["days_until_due"] is not None
            and 0 <= s["days_until_due"] <= 7
        ),
        overdue_count=sum(1 for s in latest if s["overdue"]),
        disclaimer="仅汇总每个账户、币种的最新已确认账单；不代表银行实时负债。",
        as_of=today.isoformat(),
    )
