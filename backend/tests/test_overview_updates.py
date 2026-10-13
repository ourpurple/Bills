from datetime import date, datetime, timedelta

import pytest
from conftest import make_account, make_statement

from app import services


@pytest.fixture
def freeze_clock(monkeypatch):
    def freeze(timestamp):
        instant = datetime.fromisoformat(timestamp)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                assert tz.key == "Asia/Shanghai"
                return instant.astimezone(tz)

        monkeypatch.setattr(services, "datetime", Clock)

    return freeze


@pytest.mark.parametrize("amount", ["0.00", "1000.00"])
@pytest.mark.parametrize(
    "today,day,latest_date,expected,needs_update",
    [
        ("2026-10-14", 13, "2026-09-13", "2026-10-13", True),
        ("2026-10-13", 13, "2026-09-13", "2026-10-13", False),
        ("2026-10-12", 13, "2026-09-13", "2026-09-13", False),
        ("2026-10-12", 13, "2026-08-13", "2026-09-13", True),
        ("2026-10-14", 13, "2026-10-13", "2026-10-13", False),
        ("2026-10-14", None, "2026-09-13", None, False),
        ("2026-03-01", 31, "2026-01-31", "2026-02-28", True),
        ("2028-03-01", 31, "2028-01-31", "2028-02-29", True),
        ("2026-03-01", 31, "2026-02-28", "2026-02-28", False),
        ("2026-02-28", 31, "2026-01-31", "2026-02-28", False),
        ("2026-01-01", 31, "2025-11-30", "2025-12-31", True),
    ],
)
def test_overview_marks_old_bills_after_statement_day(
    admin, freeze_clock, today, day, latest_date, expected, needs_update, amount
):
    freeze_clock(today + "T09:00:00+08:00")
    account = make_account(admin, statement_day=day)
    statement = make_statement(
        admin,
        account,
        statement_date=latest_date,
        due_date=(date.fromisoformat(latest_date) + timedelta(days=20)).isoformat(),
        amount=amount,
        minimum="0.00",
    )
    summary = admin.get("/api/v1/admin/overview").json()
    row = summary["latest_statements"][0]
    assert summary["as_of"] == today
    assert row["id"] == statement["id"]
    assert row["needs_update"] is needs_update
    assert row["expected_statement_date"] == expected
    assert row["payment_status"] == ("settled" if amount == "0.00" else "unpaid")
    assert row["remaining"] == amount
    assert summary["totals"] == [{"currency": "CNY", "amount": amount}]


def test_new_bill_must_be_confirmed_to_clear_update_marker(admin, freeze_clock):
    # UTC is still October 13; the business date in Shanghai is already October 14.
    freeze_clock("2026-10-13T16:30:00+00:00")
    account = make_account(admin, statement_day=13)
    old = make_statement(admin, account, statement_date="2026-09-13", amount="0.00", minimum="0.00")
    draft = make_statement(admin, account, confirm=False, statement_date="2026-10-13", due_date="2026-11-01")
    summary = admin.get("/api/v1/admin/overview").json()
    assert summary["as_of"] == "2026-10-14"
    assert summary["latest_statements"][0]["id"] == old["id"]
    assert summary["latest_statements"][0]["needs_update"] is True
    response = admin.post(
        f"/api/v1/admin/statements/{draft['id']}/confirm",
        json={"expected_version": 1, "reason": "新一期账单已核对"},
    )
    assert response.status_code == 200, response.text
    latest = admin.get("/api/v1/admin/overview").json()["latest_statements"]
    assert latest[0]["id"] == draft["id"]
    assert latest[0]["needs_update"] is False


def test_update_marker_is_per_currency_and_skips_inactive_accounts(admin, freeze_clock):
    freeze_clock("2026-10-14T09:00:00+08:00")
    account = make_account(admin, statement_day=13)
    make_statement(admin, account, statement_date="2026-09-13", currency="USD", amount="0", minimum="0")
    make_statement(admin, account, statement_date="2026-10-13", due_date="2026-11-01")
    rows = admin.get("/api/v1/admin/overview").json()["latest_statements"]
    assert {row["currency"]: row["needs_update"] for row in rows} == {"CNY": False, "USD": True}
    assert (
        admin.patch(
            f"/api/v1/admin/accounts/{account['id']}", json={"name": account["name"], "active": False}
        ).status_code
        == 200
    )
    rows = admin.get("/api/v1/admin/overview").json()["latest_statements"]
    assert all(row["needs_update"] is False for row in rows)
    assert all(row["expected_statement_date"] is None for row in rows)
