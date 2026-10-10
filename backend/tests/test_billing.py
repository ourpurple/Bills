from concurrent.futures import ThreadPoolExecutor
from datetime import date
from types import SimpleNamespace

import pytest
from conftest import create, detail, make_account, make_statement, payment
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.models import Bank, Revision
from app.seed import seed_banks
from app.services import expected_statement_date


def test_seed_modes_and_preserve_edits(admin):
    banks = admin.get("/api/v1/admin/banks").json()["items"]
    assert len(banks) == 13
    assert sum(b["default_mode"] == "merged" for b in banks) == 5
    a = make_account(admin)
    b = next(b for b in banks if b["code"] == "cmb")
    assert (
        admin.patch(
            "/api/v1/admin/banks/" + b["id"],
            json={"name": b["name"], "active": True, "default_mode": "independent"},
        ).status_code
        == 200
    )
    with admin.app.state.sessions() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        seed_banks(db)
        assert db.scalar(select(func.count()).select_from(Bank)) == 13
        assert db.get(Bank, b["id"]).default_mode == "independent"
        db.commit()
    assert admin.get("/api/v1/admin/accounts").json()["items"][0]["mode"] == "merged"
    second = create(admin, "accounts", {"name": "新账户", "group_id": a["group_id"]})
    assert second["mode"] == "independent"
    assert (
        admin.patch(
            "/api/v1/admin/accounts/" + a["id"], json={"name": "a", "active": True, "mode": "independent"}
        ).status_code
        == 422
    )


def test_merged_multiple_cards_count_once(admin):
    a = make_account(admin)
    for tail in ["1234", "5678", "9012"]:
        create(admin, "cards", {"name": "卡片", "account_id": a["id"], "last_four": tail})
    make_statement(admin, a)
    summary = admin.get("/api/v1/admin/overview").json()
    assert summary["totals"] == [{"currency": "CNY", "amount": "1000.00"}]
    assert len(summary["latest_statements"][0]["account"]["cards"]) == 3


def test_independent_card_limit_reactivation_and_tail_scope(admin):
    a = make_account(admin, "abc")
    card = create(admin, "cards", {"name": "一号", "account_id": a["id"], "last_four": "1234"})
    assert (
        admin.post(
            "/api/v1/admin/cards", json={"name": "二号", "account_id": a["id"], "last_four": "5678"}
        ).status_code
        == 422
    )
    assert (
        admin.patch("/api/v1/admin/cards/" + card["id"], json={"name": "一号", "active": False}).status_code
        == 200
    )
    create(admin, "cards", {"name": "二号", "account_id": a["id"], "last_four": "5678"})
    assert (
        admin.patch("/api/v1/admin/cards/" + card["id"], json={"name": "一号", "active": True}).status_code
        == 422
    )
    other = make_account(admin, "abc")
    create(admin, "cards", {"name": "同尾号", "account_id": other["id"], "last_four": "1234"})


def test_duplicate_card_and_statement(admin):
    a = make_account(admin)
    data = {"name": "一号", "account_id": a["id"], "last_four": "1234"}
    create(admin, "cards", data)
    assert admin.post("/api/v1/admin/cards", json=data).status_code == 409
    make_statement(admin, a)
    assert (
        admin.post(
            "/api/v1/admin/statements",
            json={"account_id": a["id"], "statement_date": "2026-09-01", "amount": "1000", "currency": "CNY"},
        ).status_code
        == 409
    )


def test_unknown_zero_and_negative_distinction(admin):
    a = make_account(admin)
    s = make_statement(admin, a, confirm=False, amount=None, minimum=None, due_date=None)
    assert s["remaining"] is None
    assert (
        admin.post(
            f"/api/v1/admin/statements/{s['id']}/confirm", json={"expected_version": 1, "reason": "未知"}
        ).status_code
        == 422
    )
    revised = admin.patch(
        f"/api/v1/admin/statements/{s['id']}",
        json={"expected_version": 1, "reason": "零账单", "amount": "0", "due_date": "2026-09-21"},
    )
    assert revised.status_code == 200
    assert (
        admin.post(
            f"/api/v1/admin/statements/{s['id']}/confirm", json={"expected_version": 2, "reason": "核对"}
        ).json()["payment_status"]
        == "settled"
    )
    neg = make_statement(
        admin, a, statement_date="2026-08-01", due_date="2026-08-21", amount="-10", minimum="0"
    )
    assert neg["amount"] == "-10.00" and neg["remaining"] == "0.00"


@pytest.mark.parametrize(
    "extra",
    [
        {"amount": "1.001"},
        {"amount": "NaN"},
        {"amount": "Infinity"},
        {"minimum": "-1"},
        {"minimum": "1001"},
        {"due_date": "2026-08-31"},
        {"period_end": "2026-09-02"},
        {"period_start": "2026-09-02"},
        {"period_start": "2026-08-31", "period_end": "2026-08-01"},
        {"currency": "JPY", "amount": "1.01"},
    ],
)
def test_invalid_statement_fields(admin, extra):
    a = make_account(admin)
    r = admin.post(
        "/api/v1/admin/statements",
        json={
            "account_id": a["id"],
            "statement_date": "2026-09-01",
            "amount": "1000",
            "minimum": "100",
            "due_date": "2026-09-21",
            **extra,
        },
    )
    assert r.status_code == 422, r.text


def test_revision_conflict_preserves_repayments(admin):
    s = make_statement(admin)
    assert payment(admin, s["id"]).status_code == 201
    r = admin.patch(
        f"/api/v1/admin/statements/{s['id']}",
        json={
            "expected_version": 1,
            "reason": "金额修正",
            "amount": "1200",
            "minimum": "100",
            "due_date": "2026-09-21",
        },
    )
    assert r.status_code == 200
    assert r.json()["remaining"] == "1100.00" and len(r.json()["revisions"]) == 2
    assert (
        admin.patch(
            f"/api/v1/admin/statements/{s['id']}",
            json={"expected_version": 1, "reason": "旧版本", "amount": "2"},
        ).status_code
        == 409
    )
    assert (
        admin.patch(
            f"/api/v1/admin/statements/{s['id']}", json={"expected_version": 2, "reason": "不能变未知"}
        ).status_code
        == 422
    )
    with admin.app.state.sessions() as db:
        assert (
            db.scalar(
                select(Revision).where(Revision.statement_id == s["id"], Revision.version == 1)
            ).amount_minor
            == 100000
        )


def test_repayment_idempotency_precision_and_currency(admin):
    s = make_statement(admin)
    assert payment(admin, s["id"], key=None).status_code == 422
    first = payment(admin, s["id"]).json()
    assert payment(admin, s["id"], amount="100").json()["id"] == first["id"]
    assert payment(admin, s["id"], amount="101").status_code == 409
    assert payment(admin, s["id"], key="currency", currency="USD").status_code == 422
    assert payment(admin, s["id"], key="timezone", occurred_at="2026-09-10T10:00:00").status_code == 422
    assert detail(admin, s["id"])["remaining"] == "900.00"
    assert detail(admin, s["id"])["payment_status"] == "partial"


def test_overpayment_early_future_and_revoke(admin):
    s = make_statement(admin)
    assert payment(admin, s["id"], amount="1100").json()["error"]["code"] == "CONFIRM_EXCEPTION"
    r = payment(admin, s["id"], amount="1100", confirm_exception=True)
    assert r.status_code == 201
    assert detail(admin, s["id"])["excess"] == "100.00"
    assert (
        admin.post(
            "/api/v1/admin/repayments/" + r.json()["id"] + "/revoke", json={"reason": "记录错误"}
        ).status_code
        == 200
    )
    assert detail(admin, s["id"])["remaining"] == "1000.00"
    assert (
        payment(admin, s["id"], key="early", occurred_at="2026-08-01T00:00:00Z").json()["error"]["code"]
        == "CONFIRM_EXCEPTION"
    )
    assert (
        payment(admin, s["id"], key="future", occurred_at="2099-01-01T00:00:00Z").json()["error"]["code"]
        == "FUTURE_REPAYMENT"
    )


def test_plan_not_payment_and_void_rules(admin):
    s = make_statement(admin)
    plan = admin.post(
        f"/api/v1/admin/statements/{s['id']}/plans",
        headers={"Idempotency-Key": "plan"},
        json={"amount": "200", "currency": "CNY", "occurred_at": "2099-01-01T00:00:00Z"},
    )
    assert plan.status_code == 201
    assert detail(admin, s["id"])["remaining"] == "1000.00"
    p = payment(admin, s["id"]).json()
    action = {"expected_version": 1, "reason": "作废测试"}
    assert admin.post(f"/api/v1/admin/statements/{s['id']}/void", json=action).status_code == 409
    admin.post(f"/api/v1/admin/repayments/{p['id']}/revoke", json={"reason": "撤销测试"})
    r = admin.post(f"/api/v1/admin/statements/{s['id']}/void", json=action)
    assert r.json()["plans"][0]["cancelled_at"]
    assert payment(admin, s["id"], key="after-void").status_code == 422
    assert (
        admin.patch(
            f"/api/v1/admin/statements/{s['id']}",
            json={"expected_version": 1, "reason": "after-void", "amount": "1"},
        ).status_code
        == 409
    )


def test_overview_latest_only_separate_currencies_and_missing(admin):
    a = make_account(admin, statement_day=1)
    old = make_statement(admin, a, statement_date="2026-08-01", due_date="2026-08-21")
    make_statement(admin, a)
    make_statement(admin, a, currency="USD", amount="50", minimum="5")
    make_statement(
        admin, a, confirm=False, statement_date="2026-10-01", due_date="2026-10-21", amount=None, minimum=None
    )
    summary = admin.get("/api/v1/admin/overview").json()
    assert summary["totals"] == [
        {"currency": "CNY", "amount": "1000.00"},
        {"currency": "USD", "amount": "50.00"},
    ]
    assert summary["historical_unresolved"][0]["id"] == old["id"]
    assert summary["pending_count"] == 1
    assert {w["kind"] for w in summary["warnings"]} == {"pending", "missing"}


def test_inactive_account_blocks_new_bills(admin):
    a = make_account(admin)
    assert (
        admin.patch(
            "/api/v1/admin/accounts/" + a["id"], json={"name": a["name"], "active": False}
        ).status_code
        == 200
    )
    assert (
        admin.post(
            "/api/v1/admin/statements", json={"account_id": a["id"], "statement_date": "2026-09-01"}
        ).status_code
        == 422
    )


def test_sqlite_concurrent_idempotency(admin):
    s = make_statement(admin)
    cookies = dict(admin.cookies)
    headers = dict(admin.headers)

    def submit(_):
        with TestClient(admin.app) as c:
            c.cookies.update(cookies)
            c.headers.update(headers)
            return payment(c, s["id"], key="concurrent")

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert [r.status_code for r in results] == [201, 201]
    assert results[0].json()["id"] == results[1].json()["id"]
    assert detail(admin, s["id"])["paid"] == "100.00"


@pytest.mark.parametrize(
    "today,expected",
    [
        (date(2026, 2, 28), date(2026, 2, 28)),
        (date(2026, 2, 1), date(2026, 1, 31)),
        (date(2028, 2, 29), date(2028, 2, 29)),
    ],
)
def test_month_end_expected_dates(today, expected):
    assert expected_statement_date(SimpleNamespace(statement_day=31), today) == expected


def test_repayment_idempotency_survives_new_admin_session(admin):
    from conftest import ORIGIN, PASSWORD

    from app.security import CSRF_COOKIE

    s = make_statement(admin)
    first = payment(admin, s["id"], key="relogin").json()
    admin.post("/api/v1/auth/logout")
    r = admin.post(
        "/api/v1/auth/login", headers={"Origin": ORIGIN}, json={"username": "tester", "password": PASSWORD}
    )
    assert r.status_code == 200
    admin.headers["X-CSRF-Token"] = admin.cookies[CSRF_COOKIE]
    assert payment(admin, s["id"], key="relogin").json()["id"] == first["id"]
    assert detail(admin, s["id"])["paid"] == "100.00"


def test_inactive_parent_and_no_debt_hidden(admin):
    a = make_account(admin)
    make_statement(admin, a)
    assert (
        admin.patch(
            "/api/v1/admin/cardholders/" + a["cardholder_id"], json={"name": "停用用户", "active": False}
        ).status_code
        == 200
    )
    assert (
        admin.post(
            "/api/v1/admin/cards", json={"name": "不能新增", "account_id": a["id"], "last_four": "4321"}
        ).status_code
        == 422
    )
    r = admin.get("/api/v1/admin/overview").json()
    assert r["account_count"] == 0
    assert r["totals"] == [{"currency": "CNY", "amount": "1000.00"}]
    assert admin.get("/api/v1/admin/accounts").json()["items"][0]["effective_active"] is False


def test_commit_failure_never_returns_false_success(admin, monkeypatch):
    from sqlalchemy.exc import OperationalError

    factory = admin.app.state.sessions
    db = factory()

    def fail():
        raise OperationalError("COMMIT", {}, RuntimeError("simulated storage failure"))

    monkeypatch.setattr(db, "commit", fail)
    monkeypatch.setattr(admin.app.state, "sessions", lambda: db)
    r = admin.post("/api/v1/admin/cardholders", json={"name": "rollback"})
    assert r.status_code == 503
    assert "simulated storage" not in r.text
    monkeypatch.setattr(admin.app.state, "sessions", factory)
    assert admin.get("/api/v1/admin/cardholders").json()["total"] == 0
