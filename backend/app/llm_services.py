"""Validate model output, match configured accounts, and publish explicitly.
No function in this module extracts a business field from source text.
"""

from __future__ import annotations

import json
from decimal import Decimal

from pydantic import ValidationError
from sqlalchemy import select

from . import schemas as S
from . import services as service
from .core import DomainError, utc_naive
from .llm_adapter import ModelFailure, strict_json
from .llm_schemas import ModelResult, ReviewFields
from .models import (
    Account,
    AccountGroup,
    Bank,
    CandidateRevision,
    Card,
    Cardholder,
    ModelRun,
    Statement,
    StatementDetail,
)
from .security import audit


def decode_result(content, replacements):
    text = content.strip()
    # Only remove a complete generic Markdown fence. No partial JSON repair/extraction.
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    try:
        result = strict_json(text)

        def restore(value):
            if isinstance(value, str):
                for placeholder, original in replacements:
                    value = value.replace(placeholder, original)
                return value
            if isinstance(value, list):
                return [restore(x) for x in value]
            if isinstance(value, dict):
                return {key: restore(x) for key, x in value.items()}
            return value

        result = restore(result)
        return ModelResult.model_validate_json(json.dumps(result, ensure_ascii=False)).model_dump(mode="json")
    except (ValidationError, ValueError, TypeError, RecursionError, UnicodeError, OverflowError):
        raise ModelFailure(
            "LLM_OUTPUT_INVALID",
            "模型 JSON、字段类型或一致性校验未通过；没有使用本地解析兜底",
            retryable=True,
        ) from None


def match_accounts(db, bill):
    if not bill["bank_code"] or not bill["card_last_four"]:
        return "unmatched", []
    found = []
    conflicting = False
    for account in db.scalars(select(Account)):
        if not service.account_enabled(db, account):
            continue
        group = db.get(AccountGroup, account.group_id)
        bank = db.get(Bank, group.bank_id)
        holder = db.get(Cardholder, group.cardholder_id)
        if bank.code != bill["bank_code"]:
            continue
        tails = set(
            db.scalars(select(Card.last_four).where(Card.account_id == account.id, Card.active.is_(True)))
        )
        if not set(bill["card_last_four"]).issubset(tails):
            continue
        if bill["holder_name"] and bill["holder_name"] != holder.name:
            conflicting = True
            continue
        found.append(account.id)
    return (
        "unique" if len(found) == 1 else "ambiguous" if found else "conflict" if conflicting else "unmatched"
    ), found


def output_warnings(bill, parts):
    warnings = []
    for name in [
        "bank_code",
        "holder_name",
        "card_last_four",
        "statement_date",
        "currency",
        "amount",
        "due_date",
    ]:
        if bill[name] is None or bill[name] == []:
            warnings.append("MISSING_" + name.upper())

    def exists(e):
        return bool(
            e
            and e["quote"].strip()
            and e["part"] <= len(parts)
            and e["quote"] in parts[e["part"] - 1]["text"]
        )

    for name in [
        "bank_code",
        "holder_name",
        "card_last_four",
        "statement_date",
        "currency",
        "amount",
        "minimum",
        "due_date",
        "period_start",
        "period_end",
        "credit_limit",
    ]:
        if bill[name] is not None and bill[name] != [] and not exists(bill["evidence"].get(name)):
            warnings.append("EVIDENCE_" + name.upper())
    if bill["transaction_status"] == "partial":
        warnings.append("DETAIL_PARTIAL")
    for tx in bill["transactions"]:
        if (
            not exists(tx["evidence"])
            or tx["amount"] is None
            or tx["currency"] is None
            or tx["direction"] == "unknown"
            or (tx["transaction_date"] is None and tx["posted_date"] is None)
        ):
            warnings.append("DETAIL_NEEDS_REVIEW")
            break
    if bill["warnings"]:
        warnings.append("MODEL_WARNING")
    return sorted(set(warnings))


def suggested_fields(candidate):
    if candidate.review_json:
        return json.loads(candidate.review_json)
    bill = json.loads(candidate.extraction_json)
    return {
        "account_id": candidate.account_id,
        **{
            name: bill[name]
            for name in [
                "statement_date",
                "currency",
                "amount",
                "minimum",
                "due_date",
                "period_start",
                "period_end",
                "transaction_status",
                "transactions",
            ]
        },
    }


def existing_statement(db, fields):
    if not all(fields.get(k) for k in ["account_id", "statement_date", "currency"]):
        return None
    from datetime import date

    return db.scalar(
        select(Statement).where(
            Statement.account_id == fields["account_id"],
            Statement.statement_date == date.fromisoformat(fields["statement_date"]),
            Statement.currency == fields["currency"],
        )
    )


def current_details(db, statement):
    return db.scalar(
        select(StatementDetail).where(
            StatementDetail.statement_id == statement.id, StatementDetail.version == statement.current_version
        )
    )


def transaction_rows(values):
    # Sequential order preserves identical real transactions. Evidence stays in private candidates.
    rows = []
    for index, tx in enumerate(values, 1):
        if tx["amount"] is not None and tx["currency"]:
            service.minor(Decimal(tx["amount"]), tx["currency"])
        rows.append({"index": index, **{k: v for k, v in tx.items() if k != "evidence"}})
    return rows


def candidate_view(db, candidate, details=False):
    extraction = json.loads(candidate.extraction_json)
    fields = suggested_fields(candidate)
    match, matches = match_accounts(db, extraction)
    warnings = set(json.loads(candidate.warnings))
    if match != "unique" or (fields["account_id"] and fields["account_id"] not in matches):
        warnings.add("ACCOUNT_NEEDS_REVIEW")
    old = existing_statement(db, fields)
    if old:
        warnings.add("EXISTING_STATEMENT")
    result = dict(
        id=candidate.id,
        run_id=candidate.run_id,
        state=candidate.state,
        version=candidate.version,
        account_id=fields["account_id"],
        match_state=match,
        matching_account_ids=matches,
        fields={k: v for k, v in fields.items() if k != "transactions"},
        transaction_count=len(fields["transactions"]),
        bank_code=extraction["bank_code"],
        holder_name=extraction["holder_name"],
        card_last_four=extraction["card_last_four"],
        warnings=sorted(warnings),
        statement_id=candidate.statement_id,
        created_at=service.stamp(candidate.created_at),
        existing_statement={
            "id": old.id,
            "version": old.current_version,
            "status": old.status,
            "amount": service.statement_view(db, old)["amount"],
        }
        if old
        else None,
    )
    if details:
        run = db.get(ModelRun, candidate.run_id)
        result.update(
            extraction=extraction,
            fields=fields,
            source_id=run.source_id,
            input_id=run.input_id,
            revisions=[
                dict(
                    version=r.version,
                    fields=json.loads(r.review_json),
                    reason=r.reason,
                    actor=r.actor,
                    created_at=service.stamp(r.created_at),
                )
                for r in db.scalars(
                    select(CandidateRevision)
                    .where(CandidateRevision.candidate_id == candidate.id)
                    .order_by(CandidateRevision.version.desc())
                )
            ],
        )
    return result


def pending(candidate, expected):
    if candidate.version != expected:
        raise DomainError("CANDIDATE_VERSION_CONFLICT", "候选已被其他操作更新，请刷新", 409)
    if candidate.state != "pending":
        raise DomainError("CANDIDATE_CLOSED", "候选已经处理，不能重复发布或编辑", 409)


def edit_candidate(db, candidate, data, actor):
    pending(candidate, data.expected_version)
    account = service.require(db, Account, data.fields.account_id)
    if not service.account_enabled(db, account):
        raise DomainError("INACTIVE_ACCOUNT", "停用账户不能用于候选发布")
    service.fields(data.fields, data.fields.currency, data.fields.statement_date)
    values = data.fields.model_dump(mode="json")
    transaction_rows(values["transactions"])
    candidate.review_json = json.dumps(values, ensure_ascii=False)
    candidate.account_id = account.id
    candidate.version += 1
    db.add(
        CandidateRevision(
            candidate_id=candidate.id,
            version=candidate.version,
            review_json=candidate.review_json,
            reason=data.reason,
            actor=actor.id,
        )
    )
    audit(db, actor.id, "candidate.edited", candidate.id, json.dumps({"version": candidate.version}))
    db.flush()
    return candidate_view(db, candidate, True)


def same_statement(db, old, fields):
    revision = service.current_revision(db, old)
    incoming = ReviewFields.model_validate_json(json.dumps(fields))
    if service.fields(incoming, old.currency, old.statement_date) != {
        k: getattr(revision, k)
        for k in ["amount_minor", "minimum_minor", "due_date", "period_start", "period_end"]
    }:
        return False
    if fields["transaction_status"] == "not_provided":
        return True  # Absence of a new detail table cannot erase existing details.
    detail = current_details(db, old)
    return bool(
        detail
        and detail.status == fields["transaction_status"]
        and json.loads(detail.transactions_json) == transaction_rows(fields["transactions"])
    )


def publish_candidate(db, candidate, data, actor, automatic=False):
    # Completed response is idempotent, but never re-applies an old candidate after later edits.
    if candidate.state in {"published", "duplicate"} and candidate.statement_id:
        if data.expected_version not in {candidate.version, candidate.version - 1}:
            raise DomainError("CANDIDATE_VERSION_CONFLICT", "候选版本不一致", 409)
        return dict(
            candidate=candidate_view(db, candidate),
            statement=service.statement_view(db, db.get(Statement, candidate.statement_id), True),
            duplicate=True,
        )
    pending(candidate, data.expected_version)
    view = candidate_view(db, candidate)
    if set(view["warnings"]) - {"EXISTING_STATEMENT"} and not data.acknowledge_warnings:
        raise DomainError("CANDIDATE_WARNINGS", "请逐项核对并明确接受警告后发布", 409)
    fields = suggested_fields(candidate)
    try:
        review = ReviewFields.model_validate_json(json.dumps(fields))
    except ValidationError:
        raise DomainError("CANDIDATE_INCOMPLETE", "候选缺少有效账户、账单日期或币种，请先编辑", 422) from None
    account = service.require(db, Account, review.account_id)
    if not service.account_enabled(db, account):
        raise DomainError("INACTIVE_ACCOUNT", "停用账户不能发布账单")
    values = service.fields(review, review.currency, review.statement_date)
    if values["amount_minor"] is None or values["due_date"] is None:
        raise DomainError("CANDIDATE_INCOMPLETE", "本期应还和到期日必须人工确认", 422)
    txs = transaction_rows(fields["transactions"])
    old = existing_statement(db, fields)
    duplicate = False
    if old:
        if old.status == "void":
            raise DomainError("STATEMENT_VOID", "对应账单已作废，不能通过候选自动恢复", 409)
        if data.expected_statement_version is None or old.current_version != data.expected_statement_version:
            raise DomainError("VERSION_CONFLICT", "已有账单已变更或未确认其版本，请刷新核对", 409)
        if data.action == "duplicate":
            if not same_statement(db, old, fields):
                raise DomainError("CANDIDATE_NOT_DUPLICATE", "候选与当前账单不同，不能标记相同", 409)
            duplicate = True
        elif data.action == "revise":
            update = S.StatementUpdate(
                expected_version=old.current_version,
                reason=data.reason,
                **{
                    k: getattr(review, k)
                    for k in ["amount", "minimum", "due_date", "period_start", "period_end"]
                },
            )
            service.revise_statement(db, old, update, actor)
        else:
            raise DomainError("DUPLICATE_STATEMENT", "已有同账户、日期和币种账单，请明确选择修订或相同", 409)
        statement = old
    else:
        if data.action != "create":
            raise DomainError("CANDIDATE_TARGET_CHANGED", "目标账单不存在，请刷新后选择新建", 409)
        created = service.create_statement(
            db,
            S.StatementCreate(
                reason=data.reason,
                **{k: v for k, v in fields.items() if k not in {"transactions", "transaction_status"}},
            ),
            actor,
        )
        statement = db.get(Statement, created["id"])
        statement.source_kind = "model"
    if not duplicate:
        if statement.status == "draft":
            statement.status = "confirmed"
        db.add(
            StatementDetail(
                statement_id=statement.id,
                version=statement.current_version,
                candidate_id=candidate.id,
                status=fields["transaction_status"],
                transactions_json=json.dumps(txs, ensure_ascii=False),
            )
        )
    candidate.state = "duplicate" if duplicate else "published"
    candidate.statement_id = statement.id
    candidate.version += 1
    candidate.reviewed_by = actor.id
    candidate.reviewed_at = utc_naive()
    db.flush()
    audit(
        db,
        actor.id,
        "candidate.auto_published" if automatic else "candidate." + candidate.state,
        candidate.id,
        json.dumps({"statement_id": statement.id, "statement_version": statement.current_version}),
    )
    return dict(
        candidate=candidate_view(db, candidate),
        statement=service.statement_view(db, statement, True),
        duplicate=duplicate,
    )
