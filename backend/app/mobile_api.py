"""Small mobile reads: no source text, evidence or unbounded detail history."""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select

from . import services
from .core import DomainError, utc_naive
from .models import Repayment, RepaymentPlan, Statement, StatementDetail
from .security import audit, get_db, principal

router = APIRouter(prefix="/api/v1/app", tags=["mobile"])


@router.get("/device")
def device(actor=Depends(principal, scope="function")):
    if actor.kind != "device":
        raise DomainError("DEVICE_REQUIRED", "此接口仅用于已配对设备", 403)
    return {"id": actor.id, "name": actor.name, "expires_at": services.stamp(actor.expires_at)}


@router.post("/device/disconnect")
def disconnect(db=Depends(get_db, scope="function"), actor=Depends(principal, scope="function")):
    if actor.kind != "device":
        raise DomainError("DEVICE_REQUIRED", "此接口仅用于已配对设备", 403)
    actor.revoked_at = utc_naive()
    audit(db, actor.id, "device.disconnected", actor.id)
    return {"ok": True}


@router.get("/statements/{object_id}/summary")
def summary(object_id: str, db=Depends(get_db, scope="function"), actor=Depends(principal, scope="function")):
    return services.statement_view(db, services.require(db, Statement, object_id))


@router.get("/statements/{object_id}/transactions")
def transactions(
    object_id: str,
    version: int | None = Query(None, ge=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(get_db, scope="function"),
    actor=Depends(principal, scope="function"),
):
    statement = services.require(db, Statement, object_id)
    selected = version or statement.current_version
    if selected > statement.current_version:
        raise DomainError("NOT_FOUND", "账单版本不存在", 404)
    row = db.scalar(
        select(StatementDetail).where(
            StatementDetail.statement_id == object_id, StatementDetail.version == selected
        )
    )
    records = json.loads(row.transactions_json) if row else []
    # Preserve duplicate transactions and order. Never expose model/source evidence.
    names = {
        "transaction_date",
        "posted_date",
        "description",
        "amount",
        "direction",
        "currency",
        "card_last_four",
    }
    items = [
        {k: v for k, v in record.items() if k in names}
        for record in records[(page - 1) * page_size : page * page_size]
    ]
    return {
        "items": items,
        "total": len(records),
        "page": page,
        "page_size": page_size,
        "version": selected,
        "currency": statement.currency,
        "transaction_status": row.status if row else "not_provided",
    }


def record_page(db, cls, object_id, page, page_size, serializer, order):
    services.require(db, Statement, object_id)
    query = select(cls).where(cls.statement_id == object_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(order, cls.id).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [serializer(row) for row in rows], "total": total, "page": page, "page_size": page_size}


@router.get("/statements/{object_id}/repayments")
def repayments(
    object_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(get_db, scope="function"),
    actor=Depends(principal, scope="function"),
):
    return record_page(
        db, Repayment, object_id, page, page_size, services.repayment_view, Repayment.paid_at.desc()
    )


@router.get("/statements/{object_id}/plans")
def plans(
    object_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    db=Depends(get_db, scope="function"),
    actor=Depends(principal, scope="function"),
):
    return record_page(
        db, RepaymentPlan, object_id, page, page_size, services.plan_view, RepaymentPlan.planned_at
    )
