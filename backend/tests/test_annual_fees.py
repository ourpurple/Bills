import pytest
from conftest import create, make_account
from pydantic import ValidationError

from app.schemas import AnnualFeeUpdate


@pytest.mark.parametrize("card_active", [True, False])
def test_annual_fee_edit_preserves_card_and_works_under_inactive_parent(admin, card_active):
    account = make_account(admin)
    card = create(
        admin,
        "cards",
        {
            "account_id": account["id"],
            "name": "环球通",
            "last_four": "3536",
            "credit_limit": "80000.50",
            "annual_fee_month": 10,
            "annual_fee_day": 17,
            "annual_fee_benefits": "原优惠信息",
        },
    )
    if not card_active:
        response = admin.patch(
            f"/api/v1/admin/cards/{card['id']}", json={"name": card["name"], "active": False}
        )
        assert response.status_code == 200
        card = response.json()
    assert (
        admin.patch(
            f"/api/v1/admin/accounts/{account['id']}", json={"name": account["name"], "active": False}
        ).status_code
        == 200
    )
    path = f"/api/v1/admin/cards/{card['id']}/annual-fee"
    changes = {
        "annual_fee_month": 2,
        "annual_fee_day": 29,
        "annual_fee_benefits": "刷满 6 笔免次年年费\n以银行规定为准",
    }
    response = admin.patch(path, json=changes)
    assert response.status_code == 200, response.text
    assert response.json() == {**card, **changes}
    # Partial benefit edits preserve the schedule, and schedule clearing preserves benefits.
    response = admin.patch(path, json={"annual_fee_benefits": "仅修改优惠"})
    assert response.status_code == 200
    assert response.json()["annual_fee_month"] == 2
    response = admin.patch(path, json={"annual_fee_month": None, "annual_fee_day": None})
    assert response.status_code == 200
    saved_account = next(
        a for a in admin.get("/api/v1/admin/accounts").json()["items"] if a["id"] == account["id"]
    )
    assert saved_account["effective_active"] is False
    assert saved_account["cards"][0] == {
        **card,
        "annual_fee_month": None,
        "annual_fee_day": None,
        "annual_fee_benefits": "仅修改优惠",
    }
    events = admin.get("/api/v1/admin/audit-events").json()["items"]
    assert any(e["action"] == "card.annual_fee_updated" for e in events)


@pytest.mark.parametrize(
    "body",
    [
        {"annual_fee_month": 0, "annual_fee_day": 1},
        {"annual_fee_month": 13, "annual_fee_day": 1},
        {"annual_fee_month": 2, "annual_fee_day": 30},
        {"annual_fee_month": 4, "annual_fee_day": 31},
        {"annual_fee_month": 1},
        {"annual_fee_day": 1},
        {"annual_fee_month": None},
        {"annual_fee_month": 1, "annual_fee_day": None},
        {"annual_fee_benefits": "字" * 2001},
        {"active": True},
        {"name": "不能改名"},
        {"credit_limit": "0"},
    ],
)
def test_annual_fee_schema_rejects_invalid_and_unrelated_fields(body):
    with pytest.raises(ValidationError):
        AnnualFeeUpdate(**body)


def test_annual_fee_endpoint_rejects_invalid_updates_and_unauthorized_requests(admin):
    account = make_account(admin)
    card = create(admin, "cards", {"account_id": account["id"], "name": "白金卡", "last_four": "1234"})
    path = f"/api/v1/admin/cards/{card['id']}/annual-fee"
    assert admin.patch(path, json={"annual_fee_month": 2, "annual_fee_day": 30}).status_code == 422
    assert admin.patch(path, json={"active": False}).status_code == 422
    assert (
        admin.patch(path, json={"annual_fee_benefits": "无权限"}, headers={"X-CSRF-Token": "bad"}).status_code
        == 403
    )
    assert (
        admin.patch(
            "/api/v1/admin/cards/nonexistent/annual-fee", json={"annual_fee_benefits": "无卡片"}
        ).status_code
        == 404
    )
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    device = admin.post("/api/v1/device/pair", json={"code": code, "name": "测试手机"}).json()
    assert (
        admin.patch(
            path,
            json={"annual_fee_benefits": "无权限"},
            headers={"Authorization": "Bearer " + device["token"]},
        ).status_code
        == 403
    )
    saved = next(a for a in admin.get("/api/v1/admin/accounts").json()["items"] if a["id"] == account["id"])[
        "cards"
    ][0]
    assert saved == card
    admin.cookies.clear()
    assert admin.patch(path, json={"annual_fee_benefits": "未登录"}).status_code == 401
