from datetime import timedelta

from conftest import ORIGIN, PASSWORD, make_statement
from sqlalchemy import select

from app.core import Settings, utc_naive
from app.models import Credential
from app.security import CSRF_COOKIE, SESSION_COOKIE, digest


def test_health_and_no_public_access(client):
    assert client.get("/health/ready").json()["mail_import"] == "worker_required"
    assert client.get("/api/v1/admin/banks").status_code == 401
    assert client.get("/api/v1/app/overview").status_code == 401


def test_cookie_security_and_logout(client):
    r = client.post(
        "/api/v1/auth/login", headers={"Origin": ORIGIN}, json={"username": "tester", "password": PASSWORD}
    )
    assert r.status_code == 200
    cookies = r.headers.get_list("set-cookie")
    assert any("HttpOnly" in v and SESSION_COOKIE in v for v in cookies)
    assert all("SameSite=strict" in v for v in cookies)
    assert client.get("/api/v1/auth/me").json()["username"] == "tester"
    token = client.cookies[SESSION_COOKIE]
    r = client.post(
        "/api/v1/auth/logout", headers={"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]}
    )
    assert r.status_code == 200
    client.cookies.set(SESSION_COOKIE, token, path="/api")
    assert client.get("/api/v1/auth/me").status_code == 401


def test_csrf_origin_and_forbidden_fields(admin):
    assert (
        admin.post(
            "/api/v1/admin/cardholders", headers={"X-CSRF-Token": "bad"}, json={"name": "a"}
        ).status_code
        == 403
    )
    assert (
        admin.post(
            "/api/v1/admin/cardholders", headers={"Origin": "https://evil.invalid"}, json={"name": "a"}
        ).status_code
        == 403
    )
    r = admin.post("/api/v1/admin/cardholders", json={"name": "a", "password": "hidden-secret"})
    assert r.status_code == 422
    assert "hidden-secret" not in r.text
    assert r.headers["x-request-id"] == r.json()["error"]["request_id"]


def test_login_rate_limit_is_durable(client):
    for _ in range(5):
        r = client.post(
            "/api/v1/auth/login", headers={"Origin": ORIGIN}, json={"username": "tester", "password": "wrong"}
        )
        assert r.status_code == 401
    assert (
        client.post(
            "/api/v1/auth/login",
            headers={"Origin": ORIGIN},
            json={"username": "tester", "password": PASSWORD},
        ).status_code
        == 429
    )


def test_login_origin_required(client):
    assert (
        client.post("/api/v1/auth/login", json={"username": "tester", "password": PASSWORD}).status_code
        == 403
    )


def test_device_pair_read_pay_and_cannot_manage(admin):
    s = make_statement(admin)
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    paired = admin.post("/api/v1/device/pair", json={"code": code, "name": "测试手机"}).json()
    assert admin.post("/api/v1/device/pair", json={"code": code, "name": "第二次"}).status_code == 401
    bearer = {"Authorization": "Bearer " + paired["token"]}
    assert admin.get("/api/v1/app/overview", headers=bearer).status_code == 200
    for path in ["accounts", "overview", "statements", "statements/" + s["id"], "devices", "audit-events"]:
        r = admin.get("/api/v1/admin/" + path, headers=bearer)
        assert r.status_code == 403
        assert "x-request-id" in r.headers
    assert admin.post("/api/v1/admin/cardholders", headers=bearer, json={"name": "evil"}).status_code == 403
    assert (
        admin.patch(
            f"/api/v1/admin/statements/{s['id']}",
            headers=bearer,
            json={"expected_version": 1, "reason": "evil", "amount": "0"},
        ).status_code
        == 403
    )
    r = admin.post(
        f"/api/v1/app/statements/{s['id']}/repayments",
        headers=bearer | {"Idempotency-Key": "device-1"},
        json={"amount": "50", "currency": "CNY", "occurred_at": "2026-09-10T10:00:00+08:00"},
    )
    assert r.status_code == 201
    assert admin.post(f"/api/v1/admin/devices/{paired['id']}/revoke").status_code == 200
    assert admin.get("/api/v1/app/accounts", headers=bearer).status_code == 401


def test_expired_session_and_pairing(admin):
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    with admin.app.state.sessions() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        db.scalar(select(Credential).where(Credential.token_hash == digest(code))).expires_at = (
            utc_naive() - timedelta(seconds=1)
        )
        db.commit()
    assert admin.post("/api/v1/device/pair", json={"code": code, "name": "phone"}).status_code == 401
    with admin.app.state.sessions() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        row = db.scalar(
            select(Credential).where(Credential.token_hash == digest(admin.cookies[SESSION_COOKIE]))
        )
        row.expires_at = utc_naive() - timedelta(seconds=1)
        db.commit()
    assert admin.get("/api/v1/auth/me").status_code == 401


def test_production_requires_https():
    import pytest

    with pytest.raises(ValueError):
        Settings(env="production", secure_cookies=False).validate_runtime()
