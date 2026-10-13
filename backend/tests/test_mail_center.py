import pytest
from sqlalchemy import func, select
from test_llm import FakeModel, config
from test_mail import FakeFactory, create_box, drain, message, upload, worker

from app.llm_jobs import schedule_models
from app.mail_classification import suspected_credit_card_bill
from app.mail_jobs import transaction
from app.models import Mailbox, MailSource, PreparedInput


@pytest.mark.parametrize(
    "subject,sender,expected",
    [
        ("广发信用卡 2026年10月电子账单", "广发银行 <creditcard@cgbchina.com.cn>", True),
        ("中国农业银行金穗信用卡电子对账单", "中国农业银行", True),
        ("招商银行信用卡电子账单", "cccsvc@message.cmbchina.com", True),
        ("交通银行白金信用卡2026年09月电子账单", "pccc@bankcomm.com", True),
        ("华夏信用卡-电子账单2026年09月", "华夏信用卡", True),
        ("Your monthly statement", "Credit Card Service <card@example.test>", True),
        ("Your e-statement", "Bank Service <bank@example.test>", True),
        ("金鹏俱乐部9月电子对账单", "金鹏俱乐部 <ffpmarketing@example.test>", False),
        ("【广发卡 09月还款提醒】", "广发银行 <creditcard@cgbchina.com.cn>", False),
        ("信用卡账单分期优惠", "招商银行信用卡", False),
        ("储蓄卡电子对账单", "招商银行", False),
        ("本月电话账单", "billing@example.test", False),
        ("信用卡申请成功", "广发银行", False),
        ("限时优惠", "creditcard@cgbchina.com.cn", False),
        ("", "", False),
    ],
)
def test_header_screening(subject, sender, expected):
    assert suspected_credit_card_bill(subject, sender) is expected


@pytest.mark.parametrize("protocol", ["imap", "pop3"])
def test_sync_only_fetches_suspected_bills_and_manual_restore_converts(admin, protocol):
    box = create_box(admin, protocol=protocol, port=993 if protocol == "imap" else 995)
    factory = FakeFactory(
        {
            "1": message(subject="招商银行信用卡电子账单"),
            "2": message(subject="【广发卡 09月还款提醒】", sender="广发银行"),
            "3": message(subject="金鹏俱乐部9月电子对账单", sender="金鹏俱乐部"),
            "4": message(subject="普通通知", sent=""),
        }
    )
    w = worker(admin, factory)
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    rows = {r["subject"]: r for r in admin.get("/api/v1/admin/sources").json()["items"]}
    assert rows["招商银行信用卡电子账单"]["state"] == "ready"
    assert rows["招商银行信用卡电子账单"]["suspected_bill"] is True
    for subject in ["【广发卡 09月还款提醒】", "金鹏俱乐部9月电子对账单", "普通通知"]:
        row = rows[subject]
        assert row["state"] == "skipped" and row["reason"] == "NOT_CREDIT_CARD_BILL"
        assert row["ignored"] and not row["raw_available"] and not row["input_id"]
    assert [e for e in factory.events if e[0] == "fetch"] == [("fetch", "1")]
    ignored = rows["【广发卡 09月还款提醒】"]
    response = admin.post(f"/api/v1/admin/sources/{ignored['id']}/restore")
    assert response.status_code == 200 and response.json()["state"] == "pending"
    drain(w)
    preview = admin.get(f"/api/v1/admin/sources/{ignored['id']}/preview").json()
    assert preview["source"]["state"] == "ready" and preview["input"]["parts"]
    assert ("fetch", "2") in factory.events
    with admin.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(MailSource)) == 4
        if protocol == "imap":
            assert db.get(Mailbox, box["id"]).last_uid == 4


def test_manual_ignore_survives_backfill_and_preserves_input(admin):
    box = create_box(admin)
    factory = FakeFactory({"1": message(subject="普通通知")})
    w = worker(admin, factory)
    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(w)
    source = admin.get("/api/v1/admin/sources").json()["items"][0]
    path = f"/api/v1/admin/sources/{source['id']}"
    assert admin.post(path + "/ignore").status_code == 200
    admin.post(
        f"/api/v1/admin/mailboxes/{box['id']}/sync", json={"from_date": "2026-10-01", "to_date": "2026-10-09"}
    )
    drain(w)
    assert not any(e[0] == "fetch" for e in factory.events)
    assert admin.get("/api/v1/admin/sources").json()["items"][0]["reason"] == "MANUALLY_IGNORED"
    assert admin.post(path + "/restore").status_code == 200
    drain(w)
    input_id = admin.get(path + "/preview").json()["source"]["input_id"]
    assert admin.post(path + "/ignore").json()["input_id"] == input_id
    assert admin.post(path + "/restore").json()["input_id"] == input_id


def test_auto_model_screening_covers_converted_backlog_and_manual_parse(admin):
    config(admin, auto_parse=True)
    ids = []
    for subject in ["招商银行信用卡电子账单", "金鹏俱乐部9月电子对账单", "广发卡还款提醒"]:
        response = upload(
            admin,
            message(
                body=subject,
                subject=subject,
                sender="club@example.test" if "金鹏" in subject else "bank@example.test",
            ),
        )
        ids.append(response.json()["source"]["id"])
    w = worker(admin)
    drain(w)
    schedule_models(admin.app.state.sessions)
    runs = admin.get("/api/v1/admin/model-runs").json()["items"]
    assert len(runs) == 1 and runs[0]["source_id"] == ids[0]
    ignored_path = f"/api/v1/admin/sources/{ids[1]}"
    assert admin.post(ignored_path + "/ignore").status_code == 200
    assert admin.post(ignored_path + "/parse", json={"force": False}).status_code == 202
    # Manual recognition is allowed without turning automatic handling back on.
    assert admin.get(ignored_path + "/preview").json()["source"]["ignored"] is True
    assert admin.post(ignored_path + "/restore").status_code == 409
    w.model_client = FakeModel()
    drain(w)
    assert admin.get(ignored_path + "/preview").json()["source"]["ignored"] is True
    runs_before = admin.get("/api/v1/admin/model-runs").json()["total"]
    candidates_before = admin.get("/api/v1/admin/candidates").json()["total"]
    assert runs_before == 2 and candidates_before == 2
    assert admin.post(ignored_path + "/restore").status_code == 200
    assert admin.post(ignored_path + "/ignore").status_code == 200
    assert admin.get("/api/v1/admin/model-runs").json()["total"] == runs_before
    assert admin.get("/api/v1/admin/candidates").json()["total"] == candidates_before


def test_ignore_rejects_active_task_and_search_treats_wildcards_literally(admin):
    source = upload(admin, message(subject="信用卡电子账单 100%")).json()["source"]
    path = f"/api/v1/admin/sources/{source['id']}"
    assert admin.post(path + "/ignore").status_code == 409
    drain(worker(admin))
    upload(admin, message(subject="另一份信用卡账单"))
    rows = admin.get("/api/v1/admin/sources", params={"keyword": "%"}).json()
    assert rows["total"] == 1 and rows["items"][0]["id"] == source["id"]
    assert admin.post(path + "/restore").status_code == 409
    assert admin.post("/api/v1/admin/sources/missing/ignore").status_code == 404


def test_old_queued_conversion_is_screened_before_decoder(admin, monkeypatch):
    raw = message(subject="普通通知")
    source = upload(admin, raw).json()["source"]
    with transaction(admin.app.state.sessions) as db:
        db.get(MailSource, source["id"]).protocol = "imap"

    def forbidden(*args):
        pytest.fail("non-statement must not enter text converter")

    monkeypatch.setattr("app.mail_jobs.prepare_isolated", forbidden)
    drain(worker(admin))
    row = admin.get("/api/v1/admin/sources").json()["items"][0]
    assert row["reason"] == "NOT_CREDIT_CARD_BILL" and row["state"] == "skipped"
    with admin.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(PreparedInput)) == 0


def test_ignored_unsupported_input_does_not_offer_model_parse(admin):
    config(admin)
    source = upload(admin, message(body="")).json()["source"]
    drain(worker(admin))
    path = f"/api/v1/admin/sources/{source['id']}"
    row = admin.post(path + "/ignore").json()
    assert row["input_id"] and row["ignored"] and not row["can_parse"]
    assert admin.post(path + "/parse", json={"force": False}).status_code == 409


def test_pop3_oversized_headerless_mail_keeps_size_safety_reason(admin):
    box = create_box(admin, protocol="pop3", port=995)
    base = FakeFactory()

    def factory(config, settings):
        connection = base(config, settings)
        # Real POP3 transport cannot request TOP/RETR beyond its byte cap.
        connection.headers = lambda uid: (b"", settings.mail_max_bytes + 1, None)
        return connection

    admin.post(f"/api/v1/admin/mailboxes/{box['id']}/sync", json={})
    drain(worker(admin, factory))
    source = admin.get("/api/v1/admin/sources").json()["items"][0]
    assert source["state"] == "unsupported" and source["reason"] == "MAIL_TOO_LARGE"
    assert not source["input_id"] and not source["raw_available"]
    assert not any(event[0] == "fetch" for event in base.events)
