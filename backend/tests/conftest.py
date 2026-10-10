from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

from app.core import Settings
from app.main import create_app
from app.models import Administrator
from app.security import CSRF_COOKIE, PASSWORDS
from app.seed import seed_banks

ORIGIN = "http://localhost:5173"
PASSWORD = "test-only-password-2026"


@pytest.fixture
def client(tmp_path, monkeypatch):
    url = f"sqlite:///{(tmp_path / 'test.db').as_posix()}"
    monkeypatch.setenv("BILLS_DATABASE_URL", url)
    monkeypatch.setenv("BILLS_MASTER_KEY", Fernet.generate_key().decode("ascii"))
    command.upgrade(Config(str(Path(__file__).resolve().parents[1] / "alembic.ini")), "head")
    app = create_app(
        Settings(database_url=url, public_origin=ORIGIN, env="development", secure_cookies=False)
    )
    with app.state.sessions() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        seed_banks(db)
        db.add(Administrator(username="tester", password_hash=PASSWORDS.hash(PASSWORD)))
        db.commit()
    with TestClient(app) as c:
        yield c
    app.state.engine.dispose()


@pytest.fixture
def admin(client):
    response = client.post(
        "/api/v1/auth/login", headers={"Origin": ORIGIN}, json={"username": "tester", "password": PASSWORD}
    )
    assert response.status_code == 200, response.text
    client.headers.update({"Origin": ORIGIN, "X-CSRF-Token": client.cookies[CSRF_COOKIE]})
    return client


def create(c, path, body):
    r = c.post("/api/v1/admin/" + path, json=body)
    assert r.status_code == 201, r.text
    return r.json()


def make_account(c, code="cmb", **extra):
    bank = next(b for b in c.get("/api/v1/admin/banks").json()["items"] if b["code"] == code)
    holder = create(c, "cardholders", {"name": "测试持卡人"})
    group = create(
        c, "account-groups", {"name": "主账户", "bank_id": bank["id"], "cardholder_id": holder["id"]}
    )
    return create(c, "accounts", {"group_id": group["id"], "name": "日常账户", **extra})


def make_statement(c, account=None, confirm=True, **extra):
    a = account or make_account(c)
    row = create(
        c,
        "statements",
        {
            "account_id": a["id"],
            "statement_date": "2026-09-01",
            "currency": "CNY",
            "amount": "1000.00",
            "minimum": "100.00",
            "due_date": "2026-09-21",
            **extra,
        },
    )
    if confirm:
        r = c.post(
            f"/api/v1/admin/statements/{row['id']}/confirm",
            json={"expected_version": 1, "reason": "测试核对"},
        )
        assert r.status_code == 200, r.text
        row = r.json()
    return row


def payment(c, sid, key="payment-1", **extra):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return c.post(
        f"/api/v1/admin/statements/{sid}/repayments",
        headers=headers,
        json={"amount": "100.00", "currency": "CNY", "occurred_at": "2026-09-10T10:00:00+08:00", **extra},
    )


def detail(c, sid):
    return c.get(f"/api/v1/admin/statements/{sid}").json()
