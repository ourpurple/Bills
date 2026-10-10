"""Real separate worker process against a migrated temporary SQLite database."""

import os
import subprocess
import sys
from datetime import timedelta
from pathlib import Path

from sqlalchemy import select

from app.core import utc_naive
from app.models import MailSource, WorkerHeartbeat

BACKEND = Path(__file__).resolve().parents[1]


def test_worker_process_once_and_heartbeat_healthcheck(admin):
    settings = admin.app.state.settings
    env = {
        **os.environ,
        "BILLS_DATABASE_URL": settings.database_url,
        "BILLS_STORAGE_DIR": str(settings.storage_path()),
        "BILLS_MASTER_KEY": settings.master_key,
    }

    def run(*args):
        return subprocess.run(
            [sys.executable, "-m", "app.worker", *args],
            cwd=BACKEND,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
        ).returncode

    assert run("--healthcheck") == 1
    r = admin.post(
        "/api/v1/admin/sources/upload",
        content=b"From: synthetic@example.test\r\nSubject: process test\r\n\r\nNo field extraction here.",
        headers={"X-Filename": "worker.eml"},
    )
    assert r.status_code == 202
    source_id = r.json()["source"]["id"]
    assert run("--once") == 0
    with admin.app.state.sessions() as db:
        assert db.get(MailSource, source_id).state == "ready"
    assert run("--healthcheck") == 0
    with admin.app.state.sessions() as db:
        db.connection().exec_driver_sql("BEGIN IMMEDIATE")
        for heartbeat in db.scalars(select(WorkerHeartbeat)):
            heartbeat.seen_at = utc_naive() - timedelta(seconds=90)
        db.commit()
    assert run("--healthcheck") == 1
