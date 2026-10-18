from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from conftest import create, make_account
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select

from app.models import AuditEvent, Card
from app.schemas import CardCreate, CardUpdate

# Synthetic card data only; never use real card credentials in fixtures.
PRIVATE = {"card_number": "4111111111114321", "cvv": "012"}
PUBLIC = {"expiry": "09/29", "card_type": "credit", "card_network": "visa"}


def new_card(admin, **extra):
    account = make_account(admin)
    return create(admin, "cards", {"account_id": account["id"], "name": "测试卡", **extra})


def test_private_card_fields_round_trip_preserve_and_clear(admin):
    card = new_card(admin, **PRIVATE, **PUBLIC)
    assert card["last_four"] == "4321"
    assert card["has_card_number"] is True
    assert card["has_cvv"] is True
    for name, value in PUBLIC.items():
        assert card[name] == value
    path = f"/api/v1/admin/cards/{card['id']}"
    assert admin.post(path + "/private").json() == PRIVATE
    assert admin.post(path + "/private").headers["cache-control"] == "no-store"
    # Lists, statement account projections, create and update responses never expose secrets.
    assert not ({"card_number", "cvv", "card_number_ciphertext", "cvv_ciphertext"} & card.keys())
    assert PRIVATE["card_number"] not in admin.get("/api/v1/admin/accounts").text
    with admin.app.state.sessions() as db:
        row = db.get(Card, card["id"])
        assert row.card_number_ciphertext != PRIVATE["card_number"]
        assert row.cvv_ciphertext != PRIVATE["cvv"]
        assert PRIVATE["card_number"] not in row.card_number_ciphertext
        events = db.scalars(select(AuditEvent)).all()
        assert all(PRIVATE["card_number"] not in event.detail for event in events)
    result = admin.patch(path, json={"name": "新名称", "active": False})
    assert result.status_code == 200
    assert admin.post(path + "/private").json() == PRIVATE
    assert result.json()["expiry"] == PUBLIC["expiry"]
    # CVV retains leading zeroes, and explicit clearing affects only the named field.
    result = admin.patch(
        path,
        json={"name": "新名称", "active": True, "cvv": "0012", "card_type": "debit", "card_network": "amex"},
    )
    assert result.status_code == 200
    assert admin.post(path + "/private").json() == {**PRIVATE, "cvv": "0012"}
    result = admin.patch(path, json={"name": "新名称", "active": True, "card_number": None})
    assert result.status_code == 200
    assert result.json()["has_card_number"] is False
    assert result.json()["has_cvv"] is True
    assert result.json()["last_four"] == "4321"
    result = admin.patch(path, json={"name": "新名称", "active": True, **dict.fromkeys([*PRIVATE, *PUBLIC])})
    assert result.status_code == 200
    assert result.json()["has_cvv"] is False
    for name in PUBLIC:
        assert result.json()[name] is None
    assert admin.post(path + "/private").json() == dict.fromkeys(PRIVATE)


def test_number_normalization_and_tail_mismatch_are_atomic(admin):
    card = new_card(admin, card_number="4111 1111-1111 4321")
    assert card["last_four"] == "4321"
    path = f"/api/v1/admin/cards/{card['id']}"
    response = admin.patch(
        path, json={"name": "不应保存", "active": False, "card_number": "4111111111111234"}
    )
    assert response.status_code == 422
    assert PRIVATE["card_number"] not in response.text
    accounts = admin.get("/api/v1/admin/accounts").json()["items"]
    saved = next(a for a in accounts if a["id"] == card["account_id"])["cards"][0]
    assert saved == card
    assert admin.post(path + "/private").json()["card_number"] == PRIVATE["card_number"]


def test_private_read_requires_admin_and_existing_card(admin):
    card = new_card(admin, **PRIVATE)
    path = f"/api/v1/admin/cards/{card['id']}/private"
    assert admin.get(path).status_code == 405
    assert admin.post(path, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    paired = admin.post("/api/v1/device/pair", json={"code": code, "name": "测试手机"}).json()
    assert admin.post(path, headers={"Authorization": "Bearer " + paired["token"]}).status_code == 403
    assert admin.post("/api/v1/admin/cards/nonexistent/private").status_code == 404
    admin.cookies.clear()
    assert admin.post(path).status_code == 401


def test_missing_key_never_falls_back_to_plaintext(admin):
    settings = admin.app.state.settings
    settings.master_key = ""
    card = new_card(admin, last_four="4321", **PUBLIC)
    path = f"/api/v1/admin/cards/{card['id']}"
    assert admin.post(path + "/private").json() == dict.fromkeys(PRIVATE)
    response = admin.patch(path, json={"name": "不应保存", "active": True, **PRIVATE})
    assert response.status_code == 503
    assert PRIVATE["card_number"] not in response.text
    with admin.app.state.sessions() as db:
        row = db.get(Card, card["id"])
        assert row.name == "测试卡"
        assert row.card_number_ciphertext is None
        assert row.cvv_ciphertext is None


def test_wrong_key_does_not_expose_or_erase_credentials(admin):
    card = new_card(admin, **PRIVATE)
    key = admin.app.state.settings.master_key
    admin.app.state.settings.master_key = Fernet.generate_key().decode("ascii")
    response = admin.post(f"/api/v1/admin/cards/{card['id']}/private")
    assert response.status_code == 503
    assert PRIVATE["card_number"] not in response.text
    admin.app.state.settings.master_key = key
    assert admin.post(f"/api/v1/admin/cards/{card['id']}/private").json() == PRIVATE


def test_migration_preserves_existing_cards(admin):
    card = new_card(admin, last_four="4321")
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "e50b36f9c124")
    command.upgrade(config, "head")
    accounts = admin.get("/api/v1/admin/accounts").json()["items"]
    saved = next(a for a in accounts if a["id"] == card["account_id"])["cards"][0]
    assert saved == card


@pytest.mark.parametrize(
    "data",
    [
        {"card_number": "1" * 11},
        {"card_number": "1" * 20},
        {"card_number": "abcd123456789012"},
        {"card_number": "４" * 16},
        {"expiry": "00/29"},
        {"expiry": "13/29"},
        {"expiry": "9/29"},
        {"expiry": "09/2029"},
        {"cvv": "12"},
        {"cvv": "12345"},
        {"cvv": "abc"},
        {"card_type": "other"},
        {"card_network": "other"},
    ],
)
@pytest.mark.parametrize(
    "schema,required",
    [
        (CardCreate, {"account_id": "account", "name": "卡", "last_four": "4321"}),
        (CardUpdate, {"name": "卡", "active": True}),
    ],
)
def test_invalid_identity_fields(schema, required, data):
    with pytest.raises(ValidationError):
        schema(**required, **data)


def test_create_requires_tail_or_number_and_rejects_mismatch():
    with pytest.raises(ValidationError):
        CardCreate(account_id="account", name="卡")
    with pytest.raises(ValidationError):
        CardCreate(account_id="account", name="卡", last_four="1234", **PRIVATE)
    data = CardCreate(account_id="account", name="卡", **PRIVATE)
    assert PRIVATE["card_number"] not in repr(data)
    assert "cvv=" not in repr(data)


def test_concurrent_secret_reveals_and_updates_do_not_upgrade_read_transactions(admin):
    card = new_card(admin, **PRIVATE)
    path = f"/api/v1/admin/cards/{card['id']}"
    cookies, headers = dict(admin.cookies), dict(admin.headers)

    def submit(index):
        with TestClient(admin.app) as client:
            client.cookies.update(cookies)
            client.headers.update(headers)
            if index % 3 == 0:
                return client.post(path + "/private")
            if index % 3 == 1:
                return client.patch(path, json={"name": f"卡{index}", "active": True})
            return client.get("/api/v1/admin/accounts")

    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(submit, range(12)))
    assert all(response.status_code == 200 for response in responses), [r.text for r in responses]
    assert all(responses[index].json() == PRIVATE for index in range(0, 12, 3))
    with admin.app.state.sessions() as db:
        events = db.scalars(select(AuditEvent).where(AuditEvent.action == "card.private_viewed")).all()
        assert len(events) == 4
        assert all(event.detail == "{}" for event in events)
