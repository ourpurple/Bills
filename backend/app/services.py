from __future__ import annotations

import hashlib
import json
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, case, func, or_, select

from .core import DomainError, utc_naive
from .mail_crypto import decrypt_secret, encrypt_secret
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


def account_tracking_condition():
    """Cardless legacy accounts keep working; cards opt in only when active and tracked."""
    cards = select(Card.id).where(Card.account_id == Account.id)
    tracked = cards.where(Card.active.is_(True), Card.track_bills.is_(True))
    return or_(~cards.exists(), tracked.exists())


def account_tracks_bills(db, account):
    return bool(db.scalar(select(Account.id).where(Account.id == account.id, account_tracking_condition())))


def account_billing_enabled(db, account):
    return account_enabled(db, account) and account_tracks_bills(db, account)


def require_billing_enabled(db, account):
    if not account_enabled(db, account):
        raise DomainError("INACTIVE_ACCOUNT", "停用账户不能新增或导入账单")
    if not account_tracks_bills(db, account):
        raise DomainError("BILLING_NOT_TRACKED", "此账户没有有效且开启追踪账单的卡片")


def card_values(data, settings, existing=None):
    values = data.model_dump(exclude_unset=True)
    number = values.get("card_number")
    if number and existing is not None and number[-4:] != existing.last_four:
        raise DomainError("CARD_TAIL_MISMATCH", "卡号与原卡片尾号不一致，请核对或新建卡片")
    if existing is None:
        values["last_four"] = data.last_four
    for name in ("card_number", "cvv"):
        if name in values:
            value = values.pop(name)
            if value and not settings.master_key:
                raise DomainError(
                    "MASTER_KEY_REQUIRED", "请先配置 BILLS_MASTER_KEY，卡号和 CVV 不能明文保存", 503
                )
            values[f"{name}_ciphertext"] = encrypt_secret(settings, value) if value else None
    if "credit_limit" in values:
        values["credit_limit_minor"] = minor(values.pop("credit_limit"), "CNY")
    return values


def card_view(card):
    return dict(
        id=card.id,
        account_id=card.account_id,
        name=card.name,
        last_four=card.last_four,
        active=card.active,
        track_bills=card.track_bills,
        has_card_number=bool(card.card_number_ciphertext),
        has_cvv=bool(card.cvv_ciphertext),
        expiry=card.expiry,
        card_type=card.card_type,
        card_network=card.card_network,
        credit_limit=money(card.credit_limit_minor, "CNY"),
        annual_fee_month=card.annual_fee_month,
        annual_fee_day=card.annual_fee_day,
        annual_fee_benefits=card.annual_fee_benefits,
    )


def card_private_view(card, settings):
    return {
        name: decrypt_secret(settings, getattr(card, f"{name}_ciphertext"))
        if getattr(card, f"{name}_ciphertext")
        else None
        for name in ("card_number", "cvv")
    }


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
        billing_enabled=account_billing_enabled(db, account),
        bank_id=bank.id,
        bank_name=bank.name,
        bank_code=bank.code,
        cardholder_id=holder.id,
        cardholder_name=holder.name,
        group_name=group.name,
        cards=[card_view(c) for c in cards],
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


def statement_query(
    *,
    account_id=None,
    tracked_only=True,
    bank_id=None,
    cardholder_id=None,
    keyword=None,
    status=None,
    payment_state=None,
    currency=None,
    date_field="statement_date",
    date_from=None,
    date_to=None,
):
    """Filter before pagination; card matches use EXISTS so merged accounts count once."""
    if date_from and date_to and date_from > date_to:
        raise DomainError("INVALID_DATES", "开始日期不能晚于结束日期")
    query = (
        select(Statement)
        .join(Account, Account.id == Statement.account_id)
        .join(AccountGroup, AccountGroup.id == Account.group_id)
        .join(Bank, Bank.id == AccountGroup.bank_id)
        .join(Cardholder, Cardholder.id == AccountGroup.cardholder_id)
        .join(
            Revision,
            and_(Revision.statement_id == Statement.id, Revision.version == Statement.current_version),
        )
        .order_by(Statement.statement_date.desc(), Statement.id)
    )
    if tracked_only:
        query = query.where(Statement.account_id.in_(select(Account.id).where(account_tracking_condition())))
    for value, column in (
        (account_id, Statement.account_id),
        (bank_id, AccountGroup.bank_id),
        (cardholder_id, AccountGroup.cardholder_id),
        (status, Statement.status),
        (currency, Statement.currency),
    ):
        if value:
            query = query.where(column == value)
    if keyword and keyword.strip():
        term = keyword.strip()
        card_match = (
            select(Card.id)
            .where(
                Card.account_id == Account.id,
                or_(
                    Card.name.contains(term, autoescape=True), Card.last_four.contains(term, autoescape=True)
                ),
            )
            .exists()
        )
        query = query.where(
            or_(
                card_match,
                Bank.name.contains(term, autoescape=True),
                Cardholder.name.contains(term, autoescape=True),
                Account.name.contains(term, autoescape=True),
            )
        )
    date_column = Revision.due_date if date_field == "due_date" else Statement.statement_date
    if date_from:
        query = query.where(date_column >= date_from)
    if date_to:
        query = query.where(date_column <= date_to)
    if payment_state:
        paid = (
            select(func.coalesce(func.sum(Repayment.amount_minor), 0))
            .where(
                Repayment.statement_id == Statement.id,
                Repayment.revoked_at.is_(None),
            )
            .correlate(Statement)
            .scalar_subquery()
        )
        amount = Revision.amount_minor
        nonnegative_amount = case((amount > 0, amount), else_=0)
        today = datetime.now(ZoneInfo("Asia/Shanghai")).date()
        overdue = and_(Revision.due_date.is_not(None), Revision.due_date < today, amount > paid)
        conditions = {
            "overdue": overdue,
            "unpaid": and_(amount > paid, paid == 0, ~overdue),
            "partial": and_(amount > paid, paid > 0, ~overdue),
            "settled": and_(amount.is_not(None), paid == nonnegative_amount),
            "overpaid": and_(amount.is_not(None), paid > nonnegative_amount),
        }
        query = query.where(Statement.status == "confirmed", conditions[payment_state])
    return query


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
    require_billing_enabled(db, account)
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
    tracked_ids = select(Account.id).where(account_tracking_condition())
    confirmed = db.scalars(
        select(Statement)
        .where(Statement.status == "confirmed", Statement.account_id.in_(tracked_ids))
        .order_by(Statement.statement_date.desc(), Statement.id)
    ).all()
    latest, historical, seen = [], [], set()
    totals = {}
    for row in confirmed:
        item = statement_view(db, row)
        key = (row.account_id, row.currency)
        if key not in seen:
            seen.add(key)
            item["needs_update"] = False
            item["expected_statement_date"] = None
            latest.append(item)
            totals[row.currency] = totals.get(row.currency, 0) + minor(
                Decimal(item["remaining"]), row.currency
            )
        elif Decimal(item["remaining"]) > 0:
            historical.append(item)
    latest.sort(key=lambda s: (s["remaining"] == "0.00" or s["remaining"] == "0", s["due_date"] or "9999"))
    warnings = []
    active_accounts = [
        a
        for a in db.scalars(select(Account).where(Account.active.is_(True))).all()
        if account_billing_enabled(db, a)
    ]
    for account in active_accounts:
        expected = expected_statement_date(account, today)
        account_rows = [s for s in latest if s["account"]["id"] == account.id]
        for item in account_rows:
            item["expected_statement_date"] = expected.isoformat() if expected else None
            item["needs_update"] = bool(
                expected and expected < today and date.fromisoformat(item["statement_date"]) < expected
            )
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
            select(func.count())
            .select_from(Statement)
            .where(Statement.status == "draft", Statement.account_id.in_(tracked_ids))
        ),
        due_soon_count=sum(
            1
            for s in latest
            if Decimal(s["remaining"]) > 0
            and s["days_until_due"] is not None
            and 0 <= s["days_until_due"] <= 7
        ),
        overdue_count=sum(1 for s in latest if s["overdue"]),
        disclaimer="仅汇总追踪中账户、币种的最新已确认账单；未追踪历史可在账单管理查看，不代表银行实时负债。",
        as_of=today.isoformat(),
    )
