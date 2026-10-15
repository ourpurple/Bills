import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import func, select

from app.backup import backup_database
from app.models import Bank


def test_online_backup_restore_and_no_overwrite(admin, tmp_path):
    output = tmp_path / "backup.db"
    url = admin.app.state.settings.database_url
    backup_database(url, output)
    with sqlite3.connect(output) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT count(*) FROM banks").fetchone()[0] == 13
        assert db.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "d49a25e8b013"
    with pytest.raises(ValueError):
        backup_database(url, output)
    with pytest.raises(ValueError):
        backup_database(url, Path(url.removeprefix("sqlite:///")))


def test_empty_db_migration_roundtrip(client, monkeypatch):
    config = Config(str(Path(__file__).resolve().parents[1] / "alembic.ini"))
    client.app.state.engine.dispose()
    command.downgrade(config, "base")
    command.upgrade(config, "head")
    with client.app.state.sessions() as db:
        assert db.scalar(select(func.count()).select_from(Bank)) == 0
