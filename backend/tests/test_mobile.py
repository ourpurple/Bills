import json

import pytest
from conftest import make_statement
from sqlalchemy import select
from test_llm import FakeModel, account, bill, candidate, config, parse, prepared, publish, result, tx

from app.models import StatementDetail


def device(admin, name="安卓测试设备"):
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    result = admin.post("/api/v1/device/pair", json={"code": code, "name": name})
    assert result.status_code == 200
    return {"Authorization": "Bearer " + result.json()["token"]}, result.json()


def test_mobile_summary_is_bounded(admin):
    statement = make_statement(admin)
    bearer, _ = device(admin)
    result = admin.get(f"/api/v1/app/statements/{statement['id']}/summary", headers=bearer)
    assert result.status_code == 200
    body = result.json()
    assert body["amount"] == "1000.00"
    assert body["remaining"] == "1000.00"
    assert not {
        "transactions",
        "detail_history",
        "revisions",
        "repayments",
        "plans",
        "evidence",
        "source",
    }.intersection(body)


def test_mobile_transaction_pages_pin_version_preserve_duplicates_hide_evidence(admin):
    config(admin)
    account(admin)
    row = candidate(
        admin,
        parse(
            admin,
            prepared(admin),
            FakeModel(result(bill(transaction_status="partial", transactions=[tx()] * 25))),
        ),
    )
    published = publish(admin, row, acknowledge_warnings=True)
    assert published.status_code == 200, published.text
    sid = published.json()["statement"]["id"]
    with admin.app.state.sessions() as db:
        stored = db.scalar(select(StatementDetail).where(StatementDetail.statement_id == sid))
        records = json.loads(stored.transactions_json)
        for record in records:
            record["evidence"] = {"quote": "不得手机返回"}
            record["other_secret"] = "hidden"
        stored.transactions_json = json.dumps(records)
        db.commit()
    bearer, _ = device(admin)
    path = f"/api/v1/app/statements/{sid}/transactions"
    first = admin.get(path + "?version=1&page_size=20", headers=bearer).json()
    second = admin.get(path + "?version=1&page=2&page_size=20", headers=bearer).json()
    assert first["total"] == second["total"] == 25
    assert len(first["items"]) == 20 and len(second["items"]) == 5
    assert first["version"] == 1 and first["transaction_status"] == "partial"
    assert first["items"][0] == first["items"][1]
    assert "evidence" not in first["items"][0] and "other_secret" not in first["items"][0]
    updated = admin.patch(
        f"/api/v1/admin/statements/{sid}",
        json={
            "expected_version": 1,
            "reason": "测试修订",
            "amount": "900.00",
            "minimum": "100.00",
            "due_date": "2026-09-21",
        },
    )
    assert updated.status_code == 200
    assert admin.get(path + "?version=1&page=2", headers=bearer).json()["total"] == 25
    assert admin.get(path + "?version=3", headers=bearer).status_code == 404


def test_mobile_no_detail_not_empty_complete(admin):
    statement = make_statement(admin)
    bearer, _ = device(admin)
    r = admin.get(f"/api/v1/app/statements/{statement['id']}/transactions", headers=bearer).json()
    assert r["items"] == [] and r["transaction_status"] == "not_provided"
    assert r["version"] == 1


def test_mobile_payment_and_plan_retry_pagination_revoke(admin):
    s = make_statement(admin)
    bearer, _ = device(admin)
    for kind in ["repayments", "plans"]:
        payload = {"amount": "50.00", "currency": "CNY", "occurred_at": "2026-09-10T10:00:00+08:00"}
        path = f"/api/v1/app/statements/{s['id']}/{kind}"
        first = admin.post(path, json=payload, headers=bearer | {"Idempotency-Key": kind})
        second = admin.post(path, json=payload, headers=bearer | {"Idempotency-Key": kind})
        assert first.status_code == second.status_code == 201
        assert first.json()["id"] == second.json()["id"]
        listed = admin.get(path, headers=bearer).json()
        assert listed["total"] == 1
        rid = first.json()["id"]
        action = "revoke" if kind == "repayments" else "cancel"
        for _ in range(2):
            assert (
                admin.post(
                    f"/api/v1/app/{kind}/{rid}/{action}", json={"reason": "纠错"}, headers=bearer
                ).status_code
                == 200
            )
        assert admin.get(path, headers=bearer).json()["total"] == 1
    summary = admin.get(f"/api/v1/app/statements/{s['id']}/summary", headers=bearer).json()
    assert summary["paid"] == "0.00" and summary["remaining"] == "1000.00"


def test_mobile_self_disconnect_does_not_revoke_other_device(admin):
    first, details = device(admin, "设备1")
    second, _ = device(admin, "设备2")
    assert admin.get("/api/v1/app/device", headers=first).json()["id"] == details["id"]
    assert admin.post("/api/v1/app/device/disconnect", headers=first).status_code == 200
    assert admin.get("/api/v1/app/device", headers=first).status_code == 401
    assert admin.get("/api/v1/app/device", headers=second).status_code == 200
    assert admin.get("/api/v1/admin/devices").status_code == 200


def test_admin_cannot_disconnect_own_cookie_as_mobile(admin):
    assert admin.get("/api/v1/app/device").status_code == 403
    assert admin.post("/api/v1/app/device/disconnect").status_code == 403
    assert admin.get("/api/v1/auth/me").status_code == 200


@pytest.mark.parametrize("suffix", ["summary", "transactions", "repayments", "plans"])
def test_mobile_reads_require_auth_and_validate_missing(client, suffix):
    assert client.get(f"/api/v1/app/statements/missing/{suffix}").status_code == 401


@pytest.mark.parametrize("suffix", ["transactions", "repayments", "plans"])
def test_mobile_paging_rejects_bad_bounds(admin, suffix):
    s = make_statement(admin)
    bearer, _ = device(admin)
    path = f"/api/v1/app/statements/{s['id']}/{suffix}"
    for query in ["page=0", "page_size=101", "page_size=0"]:
        assert admin.get(path + "?" + query, headers=bearer).status_code == 422
    assert admin.get(f"/api/v1/app/statements/missing/{suffix}", headers=bearer).status_code == 404


def test_mobile_management_routes_forbidden(admin):
    bearer, _ = device(admin)
    for path in [
        "banks",
        "cardholders",
        "account-groups",
        "accounts",
        "devices",
        "mailboxes",
        "model-config",
        "model-runs",
        "candidates",
        "sources",
        "audit-events",
    ]:
        # Some route names differ across stages; test actual protected admin routes.
        if any(getattr(route, "path", "") == "/api/v1/admin/" + path for route in admin.app.routes):
            assert admin.get("/api/v1/admin/" + path, headers=bearer).status_code == 403
