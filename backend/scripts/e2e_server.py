"""Isolated loopback-only server. Never points at a personal database."""

import os
import subprocess
import sys
import tempfile
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="bills-browser-test-") as directory:
        os.environ.update(
            BILLS_DATABASE_URL=f"sqlite:///{Path(directory).as_posix()}/test.db",
            BILLS_PUBLIC_ORIGIN="http://localhost:5173",
            BILLS_ENV="development",
            BILLS_SECURE_COOKIES="false",
            BILLS_MASTER_KEY=Fernet.generate_key().decode("ascii"),
            BILLS_WORKER_POLL_SECONDS="1",
        )
        from app.core import Settings
        from app.main import create_app
        from app.models import Administrator
        from app.security import PASSWORDS
        from app.seed import seed_banks

        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
        app = create_app(Settings())
        with app.state.sessions() as db:
            db.connection().exec_driver_sql("BEGIN IMMEDIATE")
            seed_banks(db)
            db.add(
                Administrator(username="e2e-admin", password_hash=PASSWORDS.hash("browser-test-only-2026"))
            )
            db.commit()
        # Only uploaded synthetic documents and predetermined model replies; no public provider.
        worker = subprocess.Popen([sys.executable, str(BACKEND / "scripts" / "e2e_worker.py")], cwd=BACKEND)
        try:
            uvicorn.run(app, host="127.0.0.1", port=8000, log_level="warning")
        finally:
            worker.terminate()
            try:
                worker.wait(timeout=10)
            except subprocess.TimeoutExpired:
                worker.kill()
                worker.wait()
