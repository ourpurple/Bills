from __future__ import annotations

import json
import secrets
from datetime import date, timedelta
from typing import Literal

from fastapi import Depends, FastAPI, Header, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from . import schemas as S
from . import services as service
from .core import DomainError, Settings, new_id, utc_naive
from .database import make_engine, make_session_factory
from .models import (
    Account,
    AccountGroup,
    Administrator,
    AuditEvent,
    Bank,
    Card,
    Cardholder,
    Credential,
    LoginAttempt,
    ModelCandidate,
    Repayment,
    RepaymentPlan,
    Statement,
)
from .security import (
    CSRF_COOKIE,
    PASSWORDS,
    SESSION_COOKIE,
    administrator,
    audit,
    authenticate,
    digest,
    get_db,
    principal,
    rate_limit,
)


def basic(row, names):
    return {name: getattr(row, name) for name in ("id", *names)}


def page(db, query, serializer, number, size):
    total = db.scalar(select(func.count()).select_from(query.order_by(None).subquery()))
    rows = db.scalars(query.offset((number - 1) * size).limit(size)).all()
    return dict(items=[serializer(row) for row in rows], total=total, page=number, page_size=size)


def create_app(settings: Settings | None = None):
    settings = settings or Settings()
    settings.validate_runtime()
    app = FastAPI(
        title="Bills · 个人信用卡账单",
        version="0.4.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None if settings.env == "production" else "/api/openapi.json",
    )
    app.state.settings = settings
    app.state.engine = make_engine(settings)
    app.state.sessions = make_session_factory(app.state.engine)
    app.state.dummy_password_hash = PASSWORDS.hash(secrets.token_urlsafe(24))

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.request_id = new_id()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse(
            status_code=error.status,
            content={
                "error": dict(code=error.code, message=error.message, request_id=request.state.request_id)
            },
        )

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, error):
        return JSONResponse(
            status_code=422,
            content={
                "error": dict(
                    code="VALIDATION_ERROR",
                    message="请检查输入内容",
                    fields=[
                        dict(field=".".join(str(p) for p in item["loc"]), message=item["msg"])
                        for item in error.errors()
                    ],
                    request_id=request.state.request_id,
                )
            },
        )

    @app.exception_handler(IntegrityError)
    async def integrity_error(request, _):
        return JSONResponse(
            status_code=409,
            content={
                "error": dict(
                    code="DATA_CONFLICT",
                    message="记录重复或关联数据冲突",
                    request_id=request.state.request_id,
                )
            },
        )

    @app.exception_handler(OperationalError)
    async def database_error(request, _):
        return JSONResponse(
            status_code=503,
            content={
                "error": dict(
                    code="DATABASE_UNAVAILABLE",
                    message="数据库暂不可用，请检查迁移或稍后重试",
                    request_id=request.state.request_id,
                )
            },
        )

    @app.get("/health/live")
    def live():
        return {"status": "alive", "version": "0.4.0"}

    @app.get("/health/ready")
    def ready(db=Depends(get_db, scope="function")):
        db.execute(select(Administrator.id).limit(1))
        db.execute(text("SELECT version_num FROM alembic_version"))
        return {
            "status": "ready",
            "stage": "P4",
            "mail_import": "worker_required",
            "llm_parser": "worker_required",
            "reminders": "not_implemented",
        }

    @app.post("/api/v1/auth/login")
    def login(data: S.Login, request: Request, response: Response, db=Depends(get_db, scope="function")):
        token, csrf, credential = authenticate(db, request, data.username, data.password)
        options = dict(
            secure=settings.secure_cookies,
            samesite="strict",
            path="/api",
            max_age=settings.session_hours * 3600,
        )
        response.set_cookie(SESSION_COOKIE, token, httponly=True, **options)
        response.set_cookie(CSRF_COOKIE, csrf, httponly=False, **options)
        return {"username": credential.name, "csrf_token": csrf}

    @app.get("/api/v1/auth/me")
    def me(request: Request, actor=Depends(administrator, scope="function")):
        return {"username": actor.name, "csrf_token": request.cookies.get(CSRF_COOKIE)}

    @app.post("/api/v1/auth/logout")
    def logout(
        response: Response,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        actor.revoked_at = utc_naive()
        response.delete_cookie(SESSION_COOKIE, path="/api", secure=settings.secure_cookies, samesite="strict")
        response.delete_cookie(CSRF_COOKIE, path="/api", secure=settings.secure_cookies, samesite="strict")
        audit(db, actor.id, "logout", actor.id)
        return {"ok": True}

    @app.get("/api/v1/admin/banks")
    def banks(
        page_number: int = Query(1, alias="page", ge=1),
        page_size: int = Query(100, ge=1, le=100),
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        return page(
            db,
            select(Bank).order_by(Bank.created_at, Bank.code),
            lambda b: basic(b, ("code", "name", "default_mode", "active")),
            page_number,
            page_size,
        )

    @app.post("/api/v1/admin/banks", status_code=201)
    def create_bank(
        data: S.BankCreate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = Bank(**data.model_dump())
        db.add(row)
        db.flush()
        audit(db, actor.id, "bank.created", row.id)
        return basic(row, ("code", "name", "default_mode", "active"))

    @app.patch("/api/v1/admin/banks/{object_id}")
    def update_bank(
        object_id: str,
        data: S.BankUpdate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, Bank, object_id)
        for key, value in data.model_dump().items():
            setattr(row, key, value)
        audit(db, actor.id, "bank.updated", row.id)
        return basic(row, ("code", "name", "default_mode", "active"))

    @app.get("/api/v1/admin/cardholders")
    def holders(
        page_number: int = Query(1, alias="page", ge=1),
        page_size: int = Query(100, ge=1, le=100),
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        return page(
            db,
            select(Cardholder).order_by(Cardholder.created_at, Cardholder.id),
            lambda r: basic(r, ("name", "active")),
            page_number,
            page_size,
        )

    @app.post("/api/v1/admin/cardholders", status_code=201)
    def create_holder(
        data: S.HolderCreate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = Cardholder(**data.model_dump())
        db.add(row)
        db.flush()
        audit(db, actor.id, "holder.created", row.id)
        return basic(row, ("name", "active"))

    @app.patch("/api/v1/admin/cardholders/{object_id}")
    def update_holder(
        object_id: str,
        data: S.HolderUpdate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, Cardholder, object_id)
        row.name, row.active = data.name, data.active
        audit(db, actor.id, "holder.updated", row.id)
        return basic(row, ("name", "active"))

    @app.get("/api/v1/admin/account-groups")
    def groups(
        page_number: int = Query(1, alias="page", ge=1),
        page_size: int = Query(100, ge=1, le=100),
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        return page(
            db,
            select(AccountGroup).order_by(AccountGroup.created_at, AccountGroup.id),
            lambda r: basic(r, ("name", "bank_id", "cardholder_id", "active")),
            page_number,
            page_size,
        )

    @app.post("/api/v1/admin/account-groups", status_code=201)
    def create_group(
        data: S.GroupCreate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        bank, holder = (
            service.require(db, Bank, data.bank_id),
            service.require(db, Cardholder, data.cardholder_id),
        )
        if not bank.active or not holder.active:
            raise DomainError("INACTIVE_PARENT", "银行或持卡人已停用")
        row = AccountGroup(**data.model_dump())
        db.add(row)
        db.flush()
        audit(db, actor.id, "group.created", row.id)
        return basic(row, ("name", "bank_id", "cardholder_id", "active"))

    @app.patch("/api/v1/admin/account-groups/{object_id}")
    def update_group(
        object_id: str,
        data: S.GroupUpdate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, AccountGroup, object_id)
        row.name, row.active = data.name, data.active
        audit(db, actor.id, "group.updated", row.id)
        return basic(row, ("name", "bank_id", "cardholder_id", "active"))

    @app.get("/api/v1/admin/accounts")
    @app.get("/api/v1/app/accounts")
    def accounts(
        page_number: int = Query(1, alias="page", ge=1),
        page_size: int = Query(100, ge=1, le=100),
        db=Depends(get_db, scope="function"),
        actor=Depends(principal, scope="function"),
    ):
        return page(
            db,
            select(Account).order_by(Account.created_at),
            lambda a: service.account_view(db, a),
            page_number,
            page_size,
        )

    @app.post("/api/v1/admin/accounts", status_code=201)
    def create_account(
        data: S.AccountCreate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        if data.group_id is not None:
            # Keep explicit groups compatible with existing clients and historical accounts.
            group = service.require(db, AccountGroup, data.group_id)
            bank = service.require(db, Bank, group.bank_id)
            holder = service.require(db, Cardholder, group.cardholder_id)
            if not group.active or not bank.active or not holder.active:
                raise DomainError("INACTIVE_PARENT", "分组、银行或持卡人已停用")
        else:
            bank = service.require(db, Bank, data.bank_id)
            holder = service.require(db, Cardholder, data.cardholder_id)
            if not bank.active or not holder.active:
                raise DomainError("INACTIVE_PARENT", "银行或持卡人已停用")
            # BEGIN IMMEDIATE serializes lookup + creation. Never reactivate old groups.
            group = db.scalar(
                select(AccountGroup)
                .where(
                    AccountGroup.bank_id == bank.id,
                    AccountGroup.cardholder_id == holder.id,
                    AccountGroup.active.is_(True),
                )
                .order_by(AccountGroup.created_at, AccountGroup.id)
                .limit(1)
            )
            if group is None:
                group = AccountGroup(bank_id=bank.id, cardholder_id=holder.id, name="默认")
                db.add(group)
                db.flush()
                audit(db, actor.id, "group.created", group.id)
        row = Account(
            group_id=group.id,
            name=data.name,
            mode=data.mode or bank.default_mode,
            statement_day=data.statement_day,
        )
        db.add(row)
        db.flush()
        audit(db, actor.id, "account.created", row.id)
        return service.account_view(db, row)

    @app.patch("/api/v1/admin/accounts/{object_id}")
    def update_account(
        object_id: str,
        data: S.AccountUpdate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, Account, object_id)
        for key, value in data.model_dump().items():
            setattr(row, key, value)
        audit(db, actor.id, "account.updated", row.id)
        return service.account_view(db, row)

    @app.delete("/api/v1/admin/accounts/{object_id}")
    def delete_account(
        object_id: str,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, Account, object_id)
        if db.scalar(select(Statement.id).where(Statement.account_id == row.id).limit(1)):
            raise DomainError("ACCOUNT_HAS_STATEMENTS", "该账户有关联账单，不能删除。请停用账户以保留历史记录。", 409)
        if db.scalar(select(ModelCandidate.id).where(ModelCandidate.account_id == row.id).limit(1)):
            raise DomainError("ACCOUNT_HAS_CANDIDATES", "该账户有关联导入记录，不能删除。请停用账户以保留历史记录。", 409)
        cards = db.scalars(select(Card).where(Card.account_id == row.id)).all()
        for card in cards:
            audit(db, actor.id, "card.deleted", card.id, json.dumps({"account_id": row.id}))
            db.delete(card)
        # Flush children first; SQLite enforces foreign keys immediately.
        db.flush()
        audit(db, actor.id, "account.deleted", row.id, json.dumps({"name": row.name, "card_count": len(cards)}))
        db.delete(row)
        return {"deleted": True, "id": object_id}

    @app.post("/api/v1/admin/cards", status_code=201)
    def create_card(
        data: S.CardCreate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        account = service.require(db, Account, data.account_id)
        if not service.account_enabled(db, account):
            raise DomainError("INACTIVE_ACCOUNT", "账户已停用")
        if account.mode == "independent" and db.scalar(
            select(Card.id).where(Card.account_id == account.id, Card.active.is_(True))
        ):
            raise DomainError("INDEPENDENT_CARD_LIMIT", "独立还款账户只能有一张有效卡，请新建还款账户")
        row = Card(**service.card_values(data))
        db.add(row)
        db.flush()
        audit(db, actor.id, "card.created", row.id)
        return service.card_view(row)

    @app.patch("/api/v1/admin/cards/{object_id}")
    def update_card(
        object_id: str,
        data: S.CardUpdate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, Card, object_id)
        account = service.require(db, Account, row.account_id)
        if (
            data.active
            and account.mode == "independent"
            and db.scalar(
                select(Card.id).where(Card.account_id == account.id, Card.id != row.id, Card.active.is_(True))
            )
        ):
            raise DomainError("INDEPENDENT_CARD_LIMIT", "独立还款账户已有关联有效卡")
        if data.active and not service.account_enabled(db, account):
            raise DomainError("INACTIVE_PARENT", "账户或上级资料已停用")
        for key, value in service.card_values(data).items():
            setattr(row, key, value)
        audit(db, actor.id, "card.updated", row.id)
        return service.card_view(row)

    @app.delete("/api/v1/admin/cards/{object_id}")
    def delete_card(
        object_id: str,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, Card, object_id)
        # Bills belong to accounts; historical transaction tails are stored separately.
        audit(db, actor.id, "card.deleted", row.id, json.dumps({"account_id": row.account_id}))
        db.delete(row)
        return {"deleted": True, "id": object_id}

    @app.get("/api/v1/admin/statements")
    @app.get("/api/v1/app/statements")
    def statements(
        account_id: str | None = None,
        status: str | None = None,
        bank_id: str | None = None,
        cardholder_id: str | None = None,
        keyword: str | None = Query(None, max_length=100),
        payment_state: Literal["unpaid", "partial", "settled", "overdue", "overpaid"] | None = None,
        currency: Literal["CNY", "USD", "EUR", "HKD", "JPY"] | None = None,
        date_field: Literal["statement_date", "due_date"] = "statement_date",
        date_from: date | None = None,
        date_to: date | None = None,
        page_number: int = Query(1, alias="page", ge=1),
        page_size: int = Query(20, ge=1, le=100),
        db=Depends(get_db, scope="function"),
        actor=Depends(principal, scope="function"),
    ):
        query = service.statement_query(
            account_id=account_id,
            bank_id=bank_id,
            cardholder_id=cardholder_id,
            keyword=keyword,
            status=status,
            payment_state=payment_state,
            currency=currency,
            date_field=date_field,
            date_from=date_from,
            date_to=date_to,
        )
        return page(db, query, lambda s: service.statement_view(db, s), page_number, page_size)

    @app.post("/api/v1/admin/statements", status_code=201)
    def create_statement(
        data: S.StatementCreate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        return service.create_statement(db, data, actor)

    @app.get("/api/v1/admin/statements/{object_id}")
    @app.get("/api/v1/app/statements/{object_id}")
    def detail(
        object_id: str, db=Depends(get_db, scope="function"), actor=Depends(principal, scope="function")
    ):
        return service.statement_view(db, service.require(db, Statement, object_id), True)

    @app.patch("/api/v1/admin/statements/{object_id}")
    def revise(
        object_id: str,
        data: S.StatementUpdate,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        return service.revise_statement(db, service.require(db, Statement, object_id), data, actor)

    @app.post("/api/v1/admin/statements/{object_id}/confirm")
    def confirm(
        object_id: str,
        data: S.VersionAction,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, Statement, object_id)
        service.check_version(row, data.expected_version)
        service.check_confirmable(service.current_revision(db, row))
        row.status = "confirmed"
        audit(
            db,
            actor.id,
            "statement.confirmed",
            row.id,
            json.dumps({"reason": data.reason}, ensure_ascii=False),
        )
        return service.statement_view(db, row, True)

    @app.post("/api/v1/admin/statements/{object_id}/void")
    def void(
        object_id: str,
        data: S.VersionAction,
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        row = service.require(db, Statement, object_id)
        service.check_version(row, data.expected_version)
        if db.scalar(
            select(Repayment.id).where(Repayment.statement_id == row.id, Repayment.revoked_at.is_(None))
        ):
            raise DomainError("ACTIVE_REPAYMENTS", "先核对并撤销有效还款记录，再作废账单", 409)
        row.status = "void"
        for plan in db.scalars(
            select(RepaymentPlan).where(
                RepaymentPlan.statement_id == row.id, RepaymentPlan.cancelled_at.is_(None)
            )
        ):
            plan.cancelled_at, plan.cancel_reason = utc_naive(), "关联账单已作废"
        audit(
            db, actor.id, "statement.voided", row.id, json.dumps({"reason": data.reason}, ensure_ascii=False)
        )
        return service.statement_view(db, row, True)

    @app.get("/api/v1/admin/overview")
    @app.get("/api/v1/app/overview")
    def overview(db=Depends(get_db, scope="function"), actor=Depends(principal, scope="function")):
        return service.overview(db)

    @app.post("/api/v1/app/statements/{object_id}/repayments", status_code=201)
    @app.post("/api/v1/admin/statements/{object_id}/repayments", status_code=201)
    def record_repayment(
        object_id: str,
        data: S.Payment,
        idempotency_key: str | None = Header(default=None),
        db=Depends(get_db, scope="function"),
        actor=Depends(principal, scope="function"),
    ):
        return service.add_payment(
            db, service.require(db, Statement, object_id), data, idempotency_key, actor
        )

    @app.post("/api/v1/app/statements/{object_id}/plans", status_code=201)
    @app.post("/api/v1/admin/statements/{object_id}/plans", status_code=201)
    def record_plan(
        object_id: str,
        data: S.Payment,
        idempotency_key: str | None = Header(default=None),
        db=Depends(get_db, scope="function"),
        actor=Depends(principal, scope="function"),
    ):
        return service.add_payment(
            db, service.require(db, Statement, object_id), data, idempotency_key, actor, planned=True
        )

    @app.post("/api/v1/admin/repayments/{object_id}/revoke")
    @app.post("/api/v1/app/repayments/{object_id}/revoke")
    def revoke(
        object_id: str,
        data: S.Revoke,
        db=Depends(get_db, scope="function"),
        actor=Depends(principal, scope="function"),
    ):
        row = service.require(db, Repayment, object_id)
        if not row.revoked_at:
            row.revoked_at, row.revoke_reason = utc_naive(), data.reason
            audit(db, actor.id, "repayment.revoked", row.id)
        return service.repayment_view(row)

    @app.post("/api/v1/admin/plans/{object_id}/cancel")
    @app.post("/api/v1/app/plans/{object_id}/cancel")
    def cancel(
        object_id: str,
        data: S.Revoke,
        db=Depends(get_db, scope="function"),
        actor=Depends(principal, scope="function"),
    ):
        row = service.require(db, RepaymentPlan, object_id)
        if not row.cancelled_at:
            row.cancelled_at, row.cancel_reason = utc_naive(), data.reason
            audit(db, actor.id, "plan.cancelled", row.id)
        return service.plan_view(row)

    @app.get("/api/v1/admin/audit-events")
    def audits(
        page_number: int = Query(1, alias="page", ge=1),
        page_size: int = Query(20, ge=1, le=100),
        db=Depends(get_db, scope="function"),
        actor=Depends(administrator, scope="function"),
    ):
        return page(
            db,
            select(AuditEvent).order_by(AuditEvent.created_at.desc(), AuditEvent.id),
            lambda r: (
                basic(r, ("actor", "action", "object_id", "detail"))
                | {"created_at": service.stamp(r.created_at)}
            ),
            page_number,
            page_size,
        )

    @app.post("/api/v1/admin/devices/pairing-codes")
    def create_pairing(db=Depends(get_db, scope="function"), actor=Depends(administrator, scope="function")):
        token = secrets.token_urlsafe(24)
        row = Credential(
            token_hash=digest(token),
            kind="pairing",
            name="配对码",
            expires_at=utc_naive() + timedelta(minutes=10),
        )
        db.add(row)
        db.flush()
        audit(db, actor.id, "device.pairing_created", row.id)
        return {"code": token, "expires_in": 600}

    @app.post("/api/v1/device/pair")
    def pair_device(data: S.Pair, request: Request, db=Depends(get_db, scope="function")):
        remote = request.client.host if request.client else "unknown"
        rate_limit(db, remote, "pair", 10)
        row = db.scalar(
            select(Credential).where(Credential.token_hash == digest(data.code), Credential.kind == "pairing")
        )
        if not row or row.revoked_at or row.expires_at <= utc_naive():
            db.add(LoginAttempt(remote=remote, action="pair"))
            db.commit()
            raise DomainError("PAIRING_FAILED", "配对码错误、已使用或已过期", 401)
        row.revoked_at = utc_naive()
        token = secrets.token_urlsafe(32)
        device = Credential(
            token_hash=digest(token),
            kind="device",
            name=data.name,
            expires_at=utc_naive() + timedelta(days=settings.device_days),
        )
        db.add(device)
        db.flush()
        audit(db, device.id, "device.paired", device.id)
        return {"token": token, "id": device.id, "expires_at": service.stamp(device.expires_at)}

    @app.get("/api/v1/admin/devices")
    def devices(db=Depends(get_db, scope="function"), actor=Depends(administrator, scope="function")):
        rows = db.scalars(
            select(Credential).where(Credential.kind == "device").order_by(Credential.created_at)
        ).all()
        return {
            "items": [
                dict(
                    id=r.id,
                    name=r.name,
                    expires_at=service.stamp(r.expires_at),
                    revoked_at=service.stamp(r.revoked_at),
                )
                for r in rows
            ]
        }

    @app.post("/api/v1/admin/devices/{object_id}/revoke")
    def revoke_device(
        object_id: str, db=Depends(get_db, scope="function"), actor=Depends(administrator, scope="function")
    ):
        row = service.require(db, Credential, object_id)
        if row.kind != "device":
            raise DomainError("NOT_FOUND", "设备不存在", 404)
        row.revoked_at = utc_naive()
        audit(db, actor.id, "device.revoked", row.id)
        return {"ok": True}

    from .mail_api import router as mail_router

    app.include_router(mail_router)
    from .llm_api import router as llm_router

    app.include_router(llm_router)
    from .mobile_api import router as mobile_router

    app.include_router(mobile_router)
    return app


app = create_app()
