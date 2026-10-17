from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from conftest import create, make_account
from pydantic import ValidationError

from app.schemas import CardCreate, CardUpdate

DETAILS = {
    "credit_limit": "50000.50",
    "annual_fee_month": 9,
    "annual_fee_day": 18,
    "annual_fee_benefits": "首年免年费\n每年刷满 6 笔免次年年费",
}
EMPTY = dict.fromkeys(DETAILS)


def saved_card(admin, account_id):
    accounts = admin.get("/api/v1/admin/accounts").json()["items"]
    return next(a for a in accounts if a["id"] == account_id)["cards"][0]


def test_card_details_create_edit_preserve_and_clear(admin):
    account = make_account(admin)
    card = create(
        admin,
        "cards",
        {
            "account_id": account["id"],
            "last_four": "1234",
            "name": "白金卡",
            **DETAILS,
        },
    )
    for key, value in DETAILS.items():
        assert card[key] == saved_card(admin, account["id"])[key] == value
    path = f"/api/v1/admin/cards/{card['id']}"
    # Older callers that only edit name/status must not erase these fields.
    response = admin.patch(path, json={"name": "新名称", "active": False})
    assert response.status_code == 200, response.text
    for key, value in DETAILS.items():
        assert response.json()[key] == value
    changed = {**DETAILS, "credit_limit": "0.00", "annual_fee_month": 2, "annual_fee_day": 29}
    response = admin.patch(path, json={"name": "新名称", "active": True, **changed})
    assert response.status_code == 200, response.text
    for key, value in changed.items():
        assert saved_card(admin, account["id"])[key] == value
    response = admin.patch(path, json={"name": "新名称", "active": True, **EMPTY})
    assert response.status_code == 200, response.text
    for key in EMPTY:
        assert response.json()[key] is None
        assert saved_card(admin, account["id"])[key] is None


def test_optional_card_details_and_upgrade_preserve_existing_cards(admin):
    account = make_account(admin)
    card = create(admin, "cards", {"account_id": account["id"], "last_four": "1234", "name": "旧卡"})
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "d49a25e8b013")
    command.upgrade(config, "head")
    restored = saved_card(admin, account["id"])
    assert restored["id"] == card["id"]
    assert restored["name"] == "旧卡"
    for key in EMPTY:
        assert restored[key] is None


@pytest.mark.parametrize(
    "details",
    [
        {"credit_limit": "-0.01"},
        {"credit_limit": "1.001"},
        {"credit_limit": "1000000000000"},
        {"credit_limit": "NaN"},
        {"credit_limit": "Infinity"},
        {"annual_fee_month": 0, "annual_fee_day": 1},
        {"annual_fee_month": 13, "annual_fee_day": 1},
        {"annual_fee_month": 1, "annual_fee_day": 0},
        {"annual_fee_month": 1, "annual_fee_day": 32},
        {"annual_fee_month": 4, "annual_fee_day": 31},
        {"annual_fee_month": 2, "annual_fee_day": 30},
        {"annual_fee_month": 1},
        {"annual_fee_day": 1},
        {"annual_fee_month": None},
        {"annual_fee_month": 1, "annual_fee_day": None},
        {"annual_fee_benefits": "字" * 2001},
    ],
)
@pytest.mark.parametrize(
    "schema,required",
    [
        (CardCreate, {"account_id": "account", "name": "测试卡", "last_four": "1234"}),
        (CardUpdate, {"name": "测试卡", "active": True}),
    ],
)
def test_invalid_card_details_are_rejected(schema, required, details):
    with pytest.raises(ValidationError):
        schema(**required, **details)


def test_invalid_edit_does_not_change_saved_details(admin):
    account = make_account(admin)
    card = create(
        admin,
        "cards",
        {
            "account_id": account["id"],
            "last_four": "1234",
            "name": "白金卡",
            **DETAILS,
        },
    )
    response = admin.patch(
        f"/api/v1/admin/cards/{card['id']}",
        json={
            "name": "不应保存",
            "active": True,
            "annual_fee_month": 2,
            "annual_fee_day": 30,
        },
    )
    assert response.status_code == 422
    assert saved_card(admin, account["id"]) == card
