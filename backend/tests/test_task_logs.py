"""Clearing queue logs must not remove active work or linked business records."""

import json
from pathlib import Path

from alembic import command
from alembic.config import Config
from conftest import make_statement
from sqlalchemy import select

from app.core import utc_naive
from app.models import AuditEvent, MailSource, ModelCall, ModelRun, PreparedInput, Task

URL = "/api/v1/admin/tasks/clear-logs"
ACTIVE = ["queued", "running", "retry_wait"]
TERMINAL = ["succeeded", "failed", "unsupported", "cancelled"]


def test_clear_all_terminal_logs_preserves_active_and_future_tasks(admin):
    with admin.app.state.sessions() as db:
        for state in ACTIVE + TERMINAL:
            db.add(Task(id=state, kind="mail_sync", state=state))
        # Clear is global, not limited to the first page or task kind.
        db.add_all(Task(id=f"done-{i}", kind="llm_test", state="succeeded") for i in range(25))
        db.commit()
    assert admin.get("/api/v1/admin/tasks").json()["total"] == 32
    response = admin.post(URL)
    assert response.status_code == 200, response.text
    assert response.json() == {"cleared": 29}
    tasks = admin.get("/api/v1/admin/tasks").json()
    assert tasks["total"] == 3
    assert {row["state"] for row in tasks["items"]} == set(ACTIVE)
    assert admin.get("/api/v1/admin/import-status").json()["tasks"] == dict.fromkeys(ACTIVE, 1)
    for state in TERMINAL:
        assert admin.get(f"/api/v1/admin/tasks?state={state}").json()["total"] == 0
    assert admin.post(URL).json() == {"cleared": 0}
    with admin.app.state.sessions() as db:
        for row in db.scalars(select(Task)):
            assert (row.logs_cleared_at is None) == (row.state in ACTIVE)
        row = db.get(Task, "running")
        row.state = "succeeded"
        row.finished_at = utc_naive()
        db.add(Task(id="new-task", kind="mail_sync", state="queued"))
        db.commit()
    completed = admin.get("/api/v1/admin/tasks?state=succeeded").json()["items"]
    assert [row["id"] for row in completed] == ["running"]
    assert completed[0]["created_at"].endswith("+00:00")
    assert completed[0]["finished_at"].endswith("+00:00")
    assert admin.post(URL).json() == {"cleared": 1}
    assert admin.get("/api/v1/admin/tasks").json()["total"] == 3


def test_clear_keeps_model_history_sources_and_bills(admin):
    statement = make_statement(admin)
    with admin.app.state.sessions() as db:
        db.add(PreparedInput(id="input", state="ready", text_chars=10))
        db.flush()
        db.add(MailSource(id="source", identity_key="source", protocol="upload", input_id="input"))
        db.flush()
        db.add(Task(id="model-task", kind="llm_parse", state="succeeded", source_id="source"))
        db.flush()
        db.add(ModelRun(
            id="run", cache_key="cache", source_id="source", input_id="input",
            config_version=1, task_id="model-task", state="succeeded", result_json='{"preserved": true}',
        ))
        db.flush()
        db.add(ModelCall(
            id="call", task_id="model-task", run_id="run", config_version=1,
            model="synthetic", kind="llm_parse", state="succeeded", price_currency="CNY",
            input_tokens=20, output_tokens=10, prompt_version="v1", schema_version="v1",
            validator_version="v1",
        ))
        db.commit()
    assert admin.post(URL).json() == {"cleared": 1}
    assert admin.get("/api/v1/admin/tasks").json()["total"] == 0
    assert admin.get(f"/api/v1/admin/statements/{statement['id']}").status_code == 200
    with admin.app.state.sessions() as db:
        assert db.get(Task, "model-task").logs_cleared_at is not None
        assert db.get(ModelRun, "run").result_json == '{"preserved": true}'
        assert db.get(ModelRun, "run").task_id == "model-task"
        assert db.get(ModelCall, "call").input_tokens == 20
        assert db.get(ModelCall, "call").task_id == "model-task"
        assert db.get(MailSource, "source").input_id == "input"
        assert db.get(PreparedInput, "input").text_chars == 10
        event = db.scalar(select(AuditEvent).where(AuditEvent.action == "task.logs_cleared"))
        assert json.loads(event.detail) == {"cleared": 1}


def test_clear_requires_login(client):
    assert client.post(URL).status_code == 401


def test_clear_requires_csrf_and_admin(admin):
    with admin.app.state.sessions() as db:
        db.add(Task(kind="mail_sync", state="succeeded"))
        db.commit()
    assert admin.post(URL, headers={"X-CSRF-Token": "invalid"}).status_code == 403
    code = admin.post("/api/v1/admin/devices/pairing-codes").json()["code"]
    token = admin.post("/api/v1/device/pair", json={"code": code, "name": "测试手机"}).json()["token"]
    assert admin.post(URL, headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert admin.get("/api/v1/admin/tasks").json()["total"] == 1


def test_log_visibility_migration_preserves_existing_tasks(admin):
    with admin.app.state.sessions() as db:
        db.add(Task(id="legacy-task", kind="mail_sync", state="succeeded", result='{"count": 3}'))
        db.commit()
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    admin.app.state.engine.dispose()
    command.downgrade(config, "c38f14d7a902")
    command.upgrade(config, "head")
    tasks = admin.get("/api/v1/admin/tasks").json()
    assert tasks["total"] == 1
    assert tasks["items"][0]["id"] == "legacy-task"
    assert tasks["items"][0]["result"] == {"count": 3}
    assert admin.post(URL).json() == {"cleared": 1}
