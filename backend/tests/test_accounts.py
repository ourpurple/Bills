from concurrent.futures import ThreadPoolExecutor

import pytest
from conftest import create, make_statement, payment
from fastapi.testclient import TestClient


def ownership(admin, code="cmb"):
    bank = next(b for b in admin.get("/api/v1/admin/banks").json()["items"] if b["code"] == code)
    holder = create(admin, "cardholders", {"name": "自动分组测试"})
    return bank, holder, {"bank_id": bank["id"], "cardholder_id": holder["id"]}


def groups(admin):
    return admin.get("/api/v1/admin/account-groups").json()["items"]


def test_create_account_automatically_creates_and_reuses_group(admin):
    bank, holder, ids = ownership(admin)
    first = create(admin, "accounts", {"name": "合并账户", **ids})
    second = create(admin, "accounts", {"name": "独立账户", "mode": "independent", **ids})
    assert first["id"] != second["id"]
    assert first["group_id"] == second["group_id"]
    assert first["bank_id"] == bank["id"]
    assert first["cardholder_id"] == holder["id"]
    assert first["mode"] == bank["default_mode"]
    assert second["mode"] == "independent"
    assert first["effective_active"] is True
    assert len(groups(admin)) == 1
    assert groups(admin)[0]["name"] == "默认"
    events = admin.get("/api/v1/admin/audit-events").json()["items"]
    assert sum(e["action"] == "group.created" for e in events) == 1


def test_automatic_group_reuses_existing_group_without_changing_history(admin):
    _, _, ids = ownership(admin)
    existing = create(admin, "account-groups", {"name": "全", **ids})
    old = create(admin, "accounts", {"name": "历史账户", "group_id": existing["id"]})
    statement = make_statement(admin, old)
    create(admin, "account-groups", {"name": "特殊产品", **ids})
    new = create(admin, "accounts", {"name": "新账户", **ids})
    assert new["group_id"] == existing["id"]
    assert len(groups(admin)) == 2
    assert groups(admin)[0]["name"] == "全"
    after = admin.get(f"/api/v1/admin/statements/{statement['id']}").json()
    assert after["account"]["id"] == old["id"]
    assert after["amount"] == statement["amount"]


def test_automatic_group_does_not_reactivate_disabled_group(admin):
    _, _, ids = ownership(admin)
    group = create(admin, "account-groups", {"name": "旧组", **ids})
    old = create(admin, "accounts", {"name": "旧账户", "group_id": group["id"]})
    assert (
        admin.patch(
            f"/api/v1/admin/account-groups/{group['id']}", json={"name": "旧组", "active": False}
        ).status_code
        == 200
    )
    new = create(admin, "accounts", {"name": "新账户", **ids})
    assert new["group_id"] != group["id"]
    assert new["effective_active"] is True
    assert next(g for g in groups(admin) if g["id"] == group["id"])["active"] is False
    accounts = admin.get("/api/v1/admin/accounts").json()["items"]
    assert next(a for a in accounts if a["id"] == old["id"])["effective_active"] is False


def test_automatic_groups_are_separate_by_bank_and_cardholder(admin):
    _, _, ids = ownership(admin)
    _, _, other = ownership(admin, "icbc")
    accounts = [
        create(admin, "accounts", {"name": "账户", **ids}),
        create(admin, "accounts", {"name": "其他持卡人", **ids, "cardholder_id": other["cardholder_id"]}),
        create(admin, "accounts", {"name": "其他银行", **ids, "bank_id": other["bank_id"]}),
    ]
    assert len({a["group_id"] for a in accounts}) == 3


@pytest.mark.parametrize("parent", ["bank", "holder"])
def test_automatic_group_rejects_inactive_parent_without_creating_group(admin, parent):
    bank, holder, ids = ownership(admin)
    if parent == "bank":
        result = admin.patch(
            f"/api/v1/admin/banks/{bank['id']}",
            json={
                "name": bank["name"],
                "default_mode": bank["default_mode"],
                "active": False,
            },
        )
    else:
        result = admin.patch(
            f"/api/v1/admin/cardholders/{holder['id']}",
            json={
                "name": holder["name"],
                "active": False,
            },
        )
    assert result.status_code == 200
    response = admin.post("/api/v1/admin/accounts", json={"name": "账户", **ids})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INACTIVE_PARENT"
    assert groups(admin) == []


@pytest.mark.parametrize("field", ["bank_id", "cardholder_id"])
def test_automatic_group_rejects_missing_parent_without_creating_group(admin, field):
    _, _, ids = ownership(admin)
    response = admin.post("/api/v1/admin/accounts", json={"name": "账户", **ids, field: "missing"})
    assert response.status_code == 404
    assert groups(admin) == []


@pytest.mark.parametrize(
    "extra",
    [
        {},
        {"bank_id": "bank"},
        {"cardholder_id": "holder"},
        {"group_id": "group", "bank_id": "bank"},
        {"group_id": "group", "cardholder_id": "holder"},
        {"group_id": "group", "bank_id": "bank", "cardholder_id": "holder"},
        {"bank_id": "", "cardholder_id": "holder"},
    ],
)
def test_account_creation_requires_one_complete_ownership_method(admin, extra):
    response = admin.post("/api/v1/admin/accounts", json={"name": "账户", **extra})
    assert response.status_code == 422
    assert groups(admin) == []


def test_concurrent_account_creation_reuses_one_automatic_group(admin):
    _, _, ids = ownership(admin)
    headers = dict(admin.headers)
    headers["Cookie"] = "; ".join(f"{key}={value}" for key, value in admin.cookies.items())

    def submit(index):
        with TestClient(admin.app) as client:
            return client.post(
                "/api/v1/admin/accounts", headers=headers, json={"name": f"账户{index}", **ids}
            )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(submit, range(2)))
    assert all(r.status_code == 201 for r in results), [r.text for r in results]
    assert len({r.json()["group_id"] for r in results}) == 1
    assert len(groups(admin)) == 1


@pytest.mark.parametrize("mode", ["merged", "independent"])
def test_delete_card_preserves_account_and_historical_statement(admin, mode):
    _, _, ids = ownership(admin)
    account = create(admin, "accounts", {"name": "删除测试", "mode": mode, **ids})
    card = create(admin, "cards", {"name": "测试卡", "last_four": "1234", "account_id": account["id"]})
    statement = make_statement(admin, account)
    assert payment(admin, statement["id"]).status_code == 201
    before = admin.get(f"/api/v1/admin/statements/{statement['id']}").json()
    response = admin.delete(f"/api/v1/admin/cards/{card['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "id": card["id"]}
    after = admin.get(f"/api/v1/admin/statements/{statement['id']}").json()
    assert after["account"]["id"] == account["id"]
    assert after["account"]["cards"] == []
    for field in ("amount", "paid", "remaining", "repayments", "revisions"):
        assert after[field] == before[field]
    # Removal releases both the independent-card limit and the tail uniqueness constraint.
    replacement = create(admin, "cards", {"name": "新卡", "last_four": "1234", "account_id": account["id"]})
    assert replacement["id"] != card["id"]
    events = admin.get("/api/v1/admin/audit-events").json()["items"]
    assert any(e["action"] == "card.deleted" and e["object_id"] == card["id"] for e in events)


@pytest.mark.parametrize("with_cards", [False, True])
def test_delete_account_removes_children_but_keeps_other_accounts_and_ownership(admin, with_cards):
    _, _, ids = ownership(admin)
    account = create(admin, "accounts", {"name": "待删除账户", **ids})
    other = create(admin, "accounts", {"name": "保留账户", **ids})
    cards = []
    if with_cards:
        for tail in ("1234", "5678"):
            cards.append(create(admin, "cards", {"name": "附属卡", "last_four": tail, "account_id": account["id"]}))
        admin.patch(f"/api/v1/admin/accounts/{account['id']}", json={"name": account["name"], "active": False})
    response = admin.delete(f"/api/v1/admin/accounts/{account['id']}")
    assert response.status_code == 200, response.text
    assert response.json() == {"deleted": True, "id": account["id"]}
    rows = admin.get("/api/v1/admin/accounts").json()["items"]
    assert [a["id"] for a in rows] == [other["id"]]
    assert len(groups(admin)) == 1
    assert admin.get("/api/v1/admin/cardholders").json()["total"] == 1
    for card in cards:
        assert admin.delete(f"/api/v1/admin/cards/{card['id']}").status_code == 404
    events = admin.get("/api/v1/admin/audit-events").json()["items"]
    assert sum(e["action"] == "account.deleted" for e in events) == 1
    assert sum(e["action"] == "card.deleted" for e in events) == len(cards)


@pytest.mark.parametrize("status", ["draft", "confirmed", "void"])
def test_delete_account_with_any_historical_statement_is_rejected_atomically(admin, status):
    _, _, ids = ownership(admin)
    account = create(admin, "accounts", {"name": "历史账户", **ids})
    card = create(admin, "cards", {"name": "历史卡", "last_four": "1234", "account_id": account["id"]})
    statement = make_statement(admin, account, confirm=status != "draft")
    if status == "void":
        response = admin.post(
            f"/api/v1/admin/statements/{statement['id']}/void",
            json={"expected_version": statement["version"], "reason": "测试作废"},
        )
        assert response.status_code == 200, response.text
    response = admin.delete(f"/api/v1/admin/accounts/{account['id']}")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ACCOUNT_HAS_STATEMENTS"
    rows = admin.get("/api/v1/admin/accounts").json()["items"]
    assert rows[0]["cards"][0]["id"] == card["id"]
    assert admin.get(f"/api/v1/admin/statements/{statement['id']}").json()["status"] == status
    events = admin.get("/api/v1/admin/audit-events").json()["items"]
    assert not any(e["action"].endswith(".deleted") for e in events)


@pytest.mark.parametrize("resource", ["accounts", "cards"])
def test_delete_requires_login(admin, resource):
    with TestClient(admin.app) as anonymous:
        assert anonymous.delete(f"/api/v1/admin/{resource}/missing").status_code == 401


@pytest.mark.parametrize("resource", ["accounts", "cards"])
def test_delete_requires_csrf_origin_and_admin_role(admin, resource):
    path = f"/api/v1/admin/{resource}/missing"
    assert admin.delete(path, headers={"X-CSRF-Token": "bad"}).status_code == 403
    assert admin.delete(path, headers={"Origin": "https://evil.invalid"}).status_code == 403
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    paired = admin.post("/api/v1/device/pair", json={"code": code, "name": "测试手机"}).json()
    assert admin.delete(path, headers={"Authorization": f"Bearer {paired['token']}"}).status_code == 403
    assert admin.delete(path).status_code == 404
