"""No excluded email may leave the worker for a model call."""

import pytest
from conftest import make_statement
from test_llm import TEXT, FakeModel, account, bill, candidate, config, parse, prepared, publish, result
from test_mail import drain, message, upload, worker

from app.llm_adapter import ModelFailure
from app.llm_jobs import schedule_models
from app.mail_jobs import transaction
from app.models import MailSource


def assert_blocked(response, code):
    assert response.status_code == 409, response.text
    assert response.json()["error"]["code"] == code


@pytest.mark.parametrize("saved_kind", ["published", "duplicate", "partial"])
def test_saved_mail_cannot_parse_even_forced_or_after_config_change(admin, saved_kind):
    config(admin)
    a = account(admin)
    source = prepared(admin)
    output = (
        result(bill(), bill(currency="USD", amount="5.00", minimum="1.00"))
        if saved_kind == "partial"
        else result(bill())
    )
    run = parse(admin, source, FakeModel(output))
    row = candidate(admin, run)
    if saved_kind == "duplicate":
        make_statement(admin, a)
        response = publish(
            admin, row, action="duplicate", expected_statement_version=1, acknowledge_warnings=True
        )
    else:
        response = publish(admin, row, acknowledge_warnings=True)
    assert response.status_code == 200, response.text
    path = f"/api/v1/admin/sources/{source['id']}"
    config(admin)
    for force in (False, True):
        assert_blocked(admin.post(path + "/parse", json={"force": force}), "SOURCE_ALREADY_SAVED")
    assert admin.get(path + "/preview").json()["source"]["can_parse"] is False
    schedule_models(admin.app.state.sessions)
    assert admin.get("/api/v1/admin/model-runs").json()["total"] == 1


@pytest.mark.parametrize("excluded", ["ignored", "saved"])
@pytest.mark.parametrize("when", ["queued", "before_call"])
def test_worker_rechecks_eligibility_without_call_or_budget_charge(admin, monkeypatch, excluded, when):
    config(admin)
    account(admin)
    source = prepared(admin)
    first = candidate(admin, parse(admin, source, FakeModel())) if excluded == "saved" else None
    before = admin.get("/api/v1/admin/model-calls").json()["total"]
    response = admin.post(f"/api/v1/admin/sources/{source['id']}/parse", json={"force": True})
    assert response.status_code == 202, response.text
    queued = response.json()

    def exclude():
        if excluded == "saved":
            response = publish(admin, first)
            assert response.status_code == 200, response.text
        else:
            # Simulate an old task or a persisted state change after enqueueing.
            with transaction(admin.app.state.sessions) as db:
                row = db.get(MailSource, source["id"])
                row.state, row.reason = "skipped", "MANUALLY_IGNORED"

    if when == "queued":
        exclude()
    else:
        from app.llm_jobs import read_blob

        def change_during_prompt(*args, **kwargs):
            blob = read_blob(*args, **kwargs)
            exclude()
            return blob

        monkeypatch.setattr("app.llm_jobs.read_blob", change_during_prompt)
    fake = FakeModel()
    w = worker(admin)
    w.model_client = fake
    drain(w)
    blocked = admin.get(f"/api/v1/admin/model-runs/{queued['id']}").json()
    assert blocked["state"] == "cancelled"
    assert blocked["error_code"] == ("SOURCE_IGNORED" if excluded == "ignored" else "SOURCE_ALREADY_SAVED")
    assert fake.calls == []
    assert admin.get("/api/v1/admin/model-calls").json()["total"] == before


@pytest.mark.parametrize("excluded", ["ignored", "saved"])
def test_manual_retry_obeys_eligibility(admin, excluded):
    config(admin)
    account(admin)
    source = prepared(admin)
    first = candidate(admin, parse(admin, source, FakeModel())) if excluded == "saved" else None
    failed = parse(admin, source, FakeModel(ModelFailure("LLM_AUTH_FAILED", "认证失败")), force=True)
    if first:
        assert publish(admin, first).status_code == 200
    else:
        assert admin.post(f"/api/v1/admin/sources/{source['id']}/ignore").status_code == 200
    response = admin.post(f"/api/v1/admin/tasks/{failed['task']['id']}/retry")
    assert_blocked(response, "SOURCE_IGNORED" if excluded == "ignored" else "SOURCE_ALREADY_SAVED")


def test_pending_view_filters_before_pagination_and_keeps_unsaved_candidates(admin):
    config(admin)
    account(admin)
    saved = prepared(admin)
    run = parse(admin, saved, FakeModel())
    assert publish(admin, candidate(admin, run)).status_code == 200
    ignored = prepared(admin, TEXT + " ignored")
    assert admin.post(f"/api/v1/admin/sources/{ignored['id']}/ignore").status_code == 200
    pending = prepared(admin, TEXT + " pending")
    parse(admin, pending, FakeModel())
    non_bill = prepared(admin, TEXT + " non bill")
    parse(admin, non_bill, FakeModel(result()))
    # Equivalent body shares a saved bill and must also be excluded.
    other = upload(admin, message(TEXT, subject="另一封信用卡电子账单")).json()["source"]
    drain(worker(admin))
    for page in (1, 2):
        response = admin.get(
            "/api/v1/admin/sources", params={"view": "pending", "page_size": 1, "page": page}
        ).json()
        assert response["total"] == 2 and len(response["items"]) == 1
        assert response["items"][0]["id"] in {pending["id"], non_bill["id"]}
    all_rows = admin.get("/api/v1/admin/sources", params={"view": "all"}).json()
    assert all_rows["total"] == 5
    other_row = next(row for row in all_rows["items"] if row["id"] == other["id"])
    assert other_row["saved_statement_count"] == 1 and not other_row["can_parse"]
    assert_blocked(
        admin.post(f"/api/v1/admin/sources/{other['id']}/parse", json={"force": True}), "SOURCE_ALREADY_SAVED"
    )
    filtered = admin.get("/api/v1/admin/sources", params={"view": "pending", "keyword": "另一封"}).json()
    assert filtered["total"] == 0


def test_auto_schedule_excludes_saved_mail_even_with_new_configuration(admin):
    config(admin)
    account(admin)
    source = prepared(admin)
    run = parse(admin, source, FakeModel())
    assert publish(admin, candidate(admin, run)).status_code == 200
    config(admin, auto_parse=True)
    # The synthetic helper's default subject is already eligible for automatic parsing.
    schedule_models(admin.app.state.sessions)
    assert admin.get("/api/v1/admin/model-runs").json()["total"] == 1


def test_saved_history_remains_excluded_after_input_is_replaced(admin):
    config(admin)
    account(admin)
    saved = prepared(admin)
    run = parse(admin, saved, FakeModel())
    assert publish(admin, candidate(admin, run)).status_code == 200
    replacement = prepared(admin, TEXT + " newer conversion representation")
    with transaction(admin.app.state.sessions) as db:
        db.get(MailSource, saved["id"]).input_id = replacement["input_id"]
    path = f"/api/v1/admin/sources/{saved['id']}"
    preview = admin.get(path + "/preview").json()["source"]
    assert preview["saved_statement_count"] == 1 and not preview["can_parse"]
    assert_blocked(admin.post(path + "/parse", json={"force": True}), "SOURCE_ALREADY_SAVED")
    rows = admin.get("/api/v1/admin/sources", params={"view": "pending"}).json()
    assert rows["total"] == 1 and rows["items"][0]["id"] == replacement["id"]
