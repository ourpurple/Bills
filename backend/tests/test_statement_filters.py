from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from conftest import create, make_account, make_statement, payment


def filtered(admin, **params):
    response = admin.get("/api/v1/admin/statements", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def ids(result):
    return {row["id"] for row in result["items"]}


def test_identity_search_combined_filters_and_pagination(admin):
    merged = make_account(admin, "cmb")
    other = make_account(admin, "abc")
    for name, tail in [("筛选卡经典白", "9999"), ("筛选卡普卡", "9166")]:
        create(admin, "cards", {"name": name, "account_id": merged["id"], "last_four": tail})
    first = make_statement(admin, merged, statement_date="2026-08-01", due_date="2026-08-21")
    second = make_statement(admin, merged)
    usd = make_statement(admin, merged, currency="USD")
    make_statement(admin, other)
    result = filtered(admin, keyword="筛选卡", page_size=1)
    assert result["total"] == 3 and len(result["items"]) == 1
    assert ids(filtered(admin, keyword=" 9166 ")) == {first["id"], second["id"], usd["id"]}
    assert ids(filtered(admin, keyword="经典白")) == {first["id"], second["id"], usd["id"]}
    assert filtered(admin, keyword="%")["total"] == 0
    assert filtered(admin, keyword="_")["total"] == 0
    assert filtered(admin, keyword="招商银行")["total"] == 3
    assert filtered(admin, keyword="测试持卡人")["total"] == 4
    assert filtered(admin, keyword="日常账户")["total"] == 4
    assert ids(
        filtered(
            admin,
            bank_id=merged["bank_id"],
            cardholder_id=merged["cardholder_id"],
            currency="USD",
            account_id=merged["id"],
        )
    ) == {usd["id"]}
    assert filtered(admin, bank_id=merged["bank_id"], cardholder_id=other["cardholder_id"])["total"] == 0
    # Historical statements stay discoverable even if their card/account has been disabled.
    card = admin.get("/api/v1/admin/accounts").json()["items"]
    card = next(a for a in card if a["id"] == merged["id"])["cards"][0]
    assert (
        admin.patch(
            f"/api/v1/admin/cards/{card['id']}", json={"name": card["name"], "active": False}
        ).status_code
        == 200
    )
    assert (
        admin.patch(
            f"/api/v1/admin/accounts/{merged['id']}", json={"name": merged["name"], "active": False}
        ).status_code
        == 200
    )
    assert filtered(admin, keyword=card["last_four"])["total"] == 3


def test_dates_use_current_revision_inclusive_bounds_and_unknown_dates(admin):
    account = make_account(admin)
    row = make_statement(admin, account)
    unknown = make_statement(admin, account, confirm=False, statement_date="2026-08-01", due_date=None)
    assert ids(filtered(admin, date_from="2026-09-01", date_to="2026-09-01")) == {row["id"]}
    assert ids(filtered(admin, date_field="due_date", date_from="2026-09-21", date_to="2026-09-21")) == {
        row["id"]
    }
    response = admin.patch(
        f"/api/v1/admin/statements/{row['id']}",
        json={
            "expected_version": 1,
            "reason": "修改到期日",
            "due_date": "2026-10-01",
            "amount": "0",
        },
    )
    assert response.status_code == 200, response.text
    assert filtered(admin, date_field="due_date", date_to="2026-09-30")["total"] == 0
    assert ids(filtered(admin, date_field="due_date", date_from="2026-10-01")) == {row["id"]}
    assert ids(filtered(admin, status="draft")) == {unknown["id"]}
    assert filtered(admin)["total"] == 2


def test_payment_state_matches_visible_state_excludes_void_draft_and_revoked_payments(admin):
    account = make_account(admin)
    today = datetime.now(ZoneInfo("Asia/Shanghai")).date()

    def statement(offset, amount="1000", **extra):
        day = today + timedelta(days=offset)
        return make_statement(
            admin,
            account,
            statement_date=day.isoformat(),
            due_date=day.isoformat(),
            amount=amount,
            minimum="0",
            **extra,
        )

    unpaid = statement(0)
    partial = statement(1)
    settled = statement(2)
    zero = statement(3, "0")
    credit = statement(4, "-10")
    overpaid = statement(5, "10")
    overdue = statement(-1)
    draft = statement(6, confirm=False)
    void = statement(7)
    for row, amount in [(partial, "100"), (settled, "1000"), (overpaid, "20"), (overdue, "100")]:
        response = payment(
            admin,
            row["id"],
            key=row["id"],
            amount=amount,
            occurred_at=(today - timedelta(days=1)).isoformat() + "T10:00:00+08:00",
            confirm_exception=True,
        )
        assert response.status_code == 201, response.text
    assert (
        admin.post(
            f"/api/v1/admin/statements/{void['id']}/void", json={"expected_version": 1, "reason": "测试"}
        ).status_code
        == 200
    )
    for state, expected in {
        "unpaid": {unpaid["id"]},
        "partial": {partial["id"]},
        "settled": {settled["id"], zero["id"], credit["id"]},
        "overpaid": {overpaid["id"]},
        "overdue": {overdue["id"]},
    }.items():
        assert ids(filtered(admin, payment_state=state)) == expected
        assert filtered(admin, status="draft", payment_state=state)["total"] == 0
    assert ids(filtered(admin, status="draft")) == {draft["id"]}
    assert ids(filtered(admin, status="void")) == {void["id"]}
    # Revoking a payment returns a future bill to unpaid; plans never reduce the balance.
    paid_row = admin.get(f"/api/v1/admin/statements/{partial['id']}").json()["repayments"][0]
    assert (
        admin.post(
            f"/api/v1/admin/repayments/{paid_row['id']}/revoke", json={"reason": "测试撤销"}
        ).status_code
        == 200
    )
    assert ids(filtered(admin, payment_state="unpaid")) == {unpaid["id"], partial["id"]}
    response = admin.post(
        f"/api/v1/admin/statements/{unpaid['id']}/plans",
        headers={"Idempotency-Key": "plan-filter"},
        json={"amount": "1000", "currency": "CNY", "occurred_at": today.isoformat() + "T10:00:00+08:00"},
    )
    assert response.status_code == 201, response.text
    assert unpaid["id"] in ids(filtered(admin, payment_state="unpaid"))


@pytest.mark.parametrize(
    "params",
    [
        {"date_from": "invalid"},
        {"date_field": "invalid"},
        {"payment_state": "invalid"},
        {"currency": "invalid"},
        {"keyword": "a" * 101},
    ],
)
def test_invalid_filter_values(admin, params):
    assert admin.get("/api/v1/admin/statements", params=params).status_code == 422


def test_reversed_date_range(admin):
    response = admin.get(
        "/api/v1/admin/statements", params={"date_from": "2026-10-01", "date_to": "2026-09-01"}
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_DATES"
