from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import Depends, Header, Request
from sqlalchemy import func, select

from .core import DomainError, utc_naive
from .models import Administrator, AuditEvent, Credential, LoginAttempt

PASSWORDS = PasswordHasher()
SESSION_COOKIE = "bills_session"
CSRF_COOKIE = "bills_csrf"


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def get_db(request: Request):
    db = request.app.state.sessions()
    try:
        # Manual SQLite transactions make reads consistent and serialize writes before
        # validation. Network calls must never be made inside this write transaction.
        db.connection().exec_driver_sql(
            "BEGIN IMMEDIATE" if request.method in {"POST", "PUT", "PATCH", "DELETE"} else "BEGIN"
        )
        # All callers use function scope: commit before sending a success response.
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def check_origin(request: Request):
    if request.headers.get("origin") != request.app.state.settings.public_origin.rstrip("/"):
        raise DomainError("ORIGIN_REJECTED", "请求来源不受信任", 403)


def audit(db, actor: str, action: str, object_id: str, detail: str = "{}"):
    db.add(AuditEvent(actor=actor, action=action, object_id=object_id, detail=detail))


def principal(
    request: Request, db=Depends(get_db, scope="function"), authorization: str | None = Header(default=None)
):
    if authorization:
        if not authorization.startswith("Bearer "):
            raise DomainError("UNAUTHENTICATED", "登录凭证无效", 401)
        token = authorization[7:]
        allowed = {"device"}
    else:
        token = request.cookies.get(SESSION_COOKIE, "")
        allowed = {"admin"}
    credential = (
        db.scalar(select(Credential).where(Credential.token_hash == digest(token))) if token else None
    )
    if (
        not credential
        or credential.kind not in allowed
        or credential.revoked_at
        or credential.expires_at <= utc_naive()
    ):
        raise DomainError("UNAUTHENTICATED", "登录已过期，请重新登录或配对", 401)
    if request.url.path.startswith("/api/v1/admin/") and credential.kind != "admin":
        raise DomainError("FORBIDDEN", "手机凭证不能访问管理接口", 403)
    if credential.kind == "admin" and request.method in {"POST", "PATCH", "PUT", "DELETE"}:
        check_origin(request)
        csrf = request.headers.get("x-csrf-token", "")
        if not csrf or not secrets.compare_digest(digest(csrf), credential.csrf_hash or ""):
            raise DomainError("CSRF_REJECTED", "安全校验失败，请刷新页面", 403)
    return credential


def administrator(actor=Depends(principal, scope="function")):
    if actor.kind != "admin":
        raise DomainError("FORBIDDEN", "手机凭证不能执行后台管理操作", 403)
    return actor


def rate_limit(db, remote: str, action: str, limit: int):
    count = db.scalar(
        select(func.count())
        .select_from(LoginAttempt)
        .where(
            LoginAttempt.remote == remote,
            LoginAttempt.action == action,
            LoginAttempt.created_at > utc_naive() - timedelta(minutes=15),
        )
    )
    if count >= limit:
        raise DomainError("RATE_LIMITED", "尝试过于频繁，请十五分钟后重试", 429)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return PASSWORDS.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def authenticate(db, request: Request, username: str, password: str):
    check_origin(request)
    remote = request.client.host if request.client else "unknown"
    rate_limit(db, remote, "login", 5)
    user = db.scalar(select(Administrator).where(Administrator.username == username))
    # Verify a fixed dummy hash when the name is absent to reduce account enumeration.
    ok = verify_password(password, user.password_hash if user else request.app.state.dummy_password_hash)
    if not user or not ok:
        db.add(LoginAttempt(remote=remote, action="login"))
        audit(db, "anonymous", "login.failed", "", "{}")
        db.commit()  # Failed attempts must survive the error response rollback.
        raise DomainError("LOGIN_FAILED", "用户名或密码不正确", 401)
    if PASSWORDS.check_needs_rehash(user.password_hash):
        user.password_hash = PASSWORDS.hash(password)
    db.query(LoginAttempt).filter(LoginAttempt.remote == remote, LoginAttempt.action == "login").delete()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    credential = Credential(
        token_hash=digest(token),
        csrf_hash=digest(csrf),
        kind="admin",
        name=user.username,
        expires_at=utc_naive() + timedelta(hours=request.app.state.settings.session_hours),
    )
    db.add(credential)
    db.flush()
    audit(db, credential.id, "login.succeeded", user.id)
    return token, csrf, credential
