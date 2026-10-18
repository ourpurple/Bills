"""Bill tracking uses both card switches, without destroying historical records."""

from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from conftest import create, make_account, make_statement

from app.llm_services import match_accounts
from app.models import Account
from app.services import account_billing_enabled


def card_for(admin, account, **extra):
    return create(
        admin, "cards", {"account_id": account["id"], "name": "追踪测试卡", "last_four": "1234", **extra}
    )


def saved_account(admin, account):
    return next(a for a in admin.get("/api/v1/admin/accounts").json()["items"] if a["id"] == account["id"])


def test_tracking_defaults_preserves_omitted_fields_and_does_not_affect_annual_fees(admin):
    account = make_account(admin)
    card = card_for(admin, account, track_bills=False)
    path = f"/api/v1/admin/cards/{card['id']}"
    assert card["track_bills"] is False
    assert saved_account(admin, account)["billing_enabled"] is False
    result = admin.patch(path, json={"name": "更名", "active": True})
    assert result.status_code == 200
    assert result.json()["track_bills"] is False
    result = admin.patch(path + "/annual-fee", json={"annual_fee_month": 1, "annual_fee_day": 1})
    assert result.status_code == 200
    assert result.json()["track_bills"] is False
    invalid = admin.patch(path, json={"name": "不应保存", "active": True, "track_bills": None})
    assert invalid.status_code == 422
    assert saved_account(admin, account)["cards"][0]["name"] == "更名"
    assert (
        admin.patch(path, json={"name": "更名", "active": True, "track_bills": True}).json()["track_bills"]
        is True
    )
    assert saved_account(admin, account)["billing_enabled"] is True


@pytest.mark.parametrize("active,track", [(True, True), (True, False), (False, True), (False, False)])
def test_only_active_tracked_cards_participate_in_billing_and_reminders(admin, active, track):
    account = make_account(admin, statement_day=1)
    card = card_for(admin, account)
    assert card["track_bills"] is True
    older = make_statement(admin, account, statement_date="2026-08-01")
    latest = make_statement(admin, account)
    draft = make_statement(admin, account, confirm=False, statement_date="2026-10-01", due_date="2026-10-21")
    result = admin.patch(
        f"/api/v1/admin/cards/{card['id']}",
        json={"name": card["name"], "active": active, "track_bills": track},
    )
    assert result.status_code == 200
    enabled = active and track
    assert saved_account(admin, account)["effective_active"] is True
    assert saved_account(admin, account)["billing_enabled"] is enabled
    view = admin.get("/api/v1/admin/overview").json()
    assert view["account_count"] == int(enabled)
    assert view["pending_count"] == int(enabled)
    assert [s["id"] for s in view["latest_statements"]] == ([latest["id"]] if enabled else [])
    assert [s["id"] for s in view["historical_unresolved"]] == ([older["id"]] if enabled else [])
    if enabled:
        assert view["totals"] == [{"currency": "CNY", "amount": "1000.00"}]
        assert view["warnings"]
    else:
        assert view["totals"] == []
        assert view["warnings"] == []
        assert view["due_soon_count"] == view["overdue_count"] == 0
    # Historical statements remain queryable, with identity and versions intact.
    assert admin.get("/api/v1/admin/statements").json()["total"] == (3 if enabled else 0)
    assert admin.get("/api/v1/app/statements").json()["total"] == (3 if enabled else 0)
    assert admin.get("/api/v1/app/statements", params={"tracked_only": False}).json()["total"] == 3
    archive = admin.get("/api/v1/admin/statements", params={"tracked_only": False, "page_size": 1}).json()
    assert archive["total"] == 3
    assert len(archive["items"]) == 1
    assert admin.get(f"/api/v1/admin/statements/{draft['id']}").json()["version"] == 1
    response = admin.post(
        "/api/v1/admin/statements", json={"account_id": account["id"], "statement_date": "2026-11-01"}
    )
    assert response.status_code == (201 if enabled else 422)
    if not enabled:
        assert response.json()["error"]["code"] == "BILLING_NOT_TRACKED"
        # Reactivation must restore tracking without changing the old records.
        assert (
            admin.patch(
                f"/api/v1/admin/cards/{card['id']}",
                json={"name": card["name"], "active": True, "track_bills": True},
            ).status_code
            == 200
        )
        assert saved_account(admin, account)["billing_enabled"] is True
        restored = admin.get("/api/v1/admin/overview").json()
        assert restored["latest_statements"][0]["id"] == latest["id"]


def test_merged_accounts_use_only_eligible_cards_and_count_each_statement_once(admin):
    account = make_account(admin, mode="merged")
    first = card_for(admin, account, track_bills=False)
    second = card_for(admin, account, name="第二张卡", last_four="5678")
    make_statement(admin, account)
    view = admin.get("/api/v1/admin/overview").json()
    assert view["account_count"] == 1
    assert len(view["latest_statements"]) == 1
    assert view["totals"] == [{"currency": "CNY", "amount": "1000.00"}]
    with admin.app.state.sessions() as db:
        base = {"bank_code": "cmb", "holder_name": "测试持卡人"}
        assert match_accounts(db, {**base, "card_last_four": ["1234"]}) == ("unmatched", [])
        assert match_accounts(db, {**base, "card_last_four": ["5678"]}) == ("unique", [account["id"]])
        assert match_accounts(db, {**base, "card_last_four": ["1234", "5678"]}) == ("unmatched", [])
    assert (
        admin.patch(
            f"/api/v1/admin/cards/{second['id']}",
            json={"name": second["name"], "active": False, "track_bills": True},
        ).status_code
        == 200
    )
    assert saved_account(admin, account)["billing_enabled"] is False
    assert admin.get("/api/v1/admin/overview").json()["totals"] == []
    assert saved_account(admin, account)["cards"][0]["id"] == first["id"]


def test_cardless_legacy_accounts_keep_manual_billing_and_parent_status_is_respected(admin):
    account = make_account(admin)
    assert saved_account(admin, account)["billing_enabled"] is True
    make_statement(admin, account)
    card_for(admin, account)
    assert (
        admin.patch(
            f"/api/v1/admin/accounts/{account['id']}", json={"name": account["name"], "active": False}
        ).status_code
        == 200
    )
    assert saved_account(admin, account)["billing_enabled"] is False
    with admin.app.state.sessions() as db:
        assert account_billing_enabled(db, db.get(Account, account["id"])) is False


def test_migration_defaults_existing_cards_to_tracking_and_keeps_card_data(admin):
    account = make_account(admin)
    card = card_for(admin, account, expiry="09/29", annual_fee_benefits="保留资料")
    make_statement(admin, account)
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    command.downgrade(config, "f61c47a0d235")
    command.upgrade(config, "head")
    restored = saved_account(admin, account)
    assert restored["cards"][0] == card
    assert restored["billing_enabled"] is True
    assert admin.get("/api/v1/admin/statements").json()["total"] == 1
