"""Consistent local SQLite backup. Does not copy a live WAL database file."""

import os
import sqlite3
from pathlib import Path


def backup_database(database_url: str, destination: Path):
    if not database_url.startswith("sqlite:///") or database_url.endswith(":memory:"):
        raise ValueError("仅支持文件 SQLite 数据库")
    source = Path(database_url.removeprefix("sqlite:///")).resolve()
    destination = destination.resolve()
    if not source.is_file():
        raise ValueError("源数据库不存在")
    if source == destination or destination.exists():
        raise ValueError("备份不能覆盖源文件或已有文件")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Reserve the output atomically. A failed backup remains for inspection, never
    # overwriting a good backup. Use SQLite online backup for consistency with WAL.
    descriptor = os.open(destination, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(descriptor)
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as origin:
        with sqlite3.connect(destination) as target:
            origin.backup(target, pages=256)
            if target.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("备份完整性检查失败")
    return destination
