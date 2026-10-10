"""Loopback-only disposable Android fixture. Not copied into Docker images.

Only predetermined synthetic model replies; no email or model provider calls.
"""

import os
import sys
import tempfile
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(BACKEND), str(BACKEND / "tests")]

if __name__ == "__main__":
    with tempfile.TemporaryDirectory(prefix="bills-android-test-") as directory:
        os.environ.update(
            BILLS_DATABASE_URL=f"sqlite:///{Path(directory).as_posix()}/test.db",
            BILLS_PUBLIC_ORIGIN="http://localhost:5173",
            BILLS_ENV="development",
            BILLS_SECURE_COOKIES="false",
            BILLS_MASTER_KEY=Fernet.generate_key().decode("ascii"),
        )
        from test_llm import FakeModel, account, bill, candidate, config, parse, prepared, publish, result, tx

        from app.core import Settings
        from app.main import create_app
        from app.models import Administrator
        from app.security import CSRF_COOKIE, PASSWORDS
        from app.seed import seed_banks

        command.upgrade(Config(str(BACKEND / "alembic.ini")), "head")
        app = create_app(Settings())
        with app.state.sessions() as db:
            seed_banks(db)
            db.add(
                Administrator(
                    username="android-fixture", password_hash=PASSWORDS.hash("android-test-only-2026")
                )
            )
            db.commit()
        with TestClient(app) as fixture:
            fixture.post(
                "/api/v1/auth/login",
                headers={"Origin": "http://localhost:5173"},
                json={"username": "android-fixture", "password": "android-test-only-2026"},
            )
            fixture.headers.update(
                {"Origin": "http://localhost:5173", "X-CSRF-Token": fixture.cookies[CSRF_COOKIE]}
            )
            config(fixture)
            account(fixture)
            row = candidate(
                fixture,
                parse(
                    fixture,
                    prepared(fixture),
                    FakeModel(result(bill(transaction_status="partial", transactions=[tx()] * 25))),
                ),
            )
            published = publish(fixture, row, acknowledge_warnings=True)
            assert published.status_code == 200, published.text
            statement_id = published.json()["statement"]["id"]

            @app.get("/testing/fixture")
            def synthetic_fixture():
                # Private disposable loopback server only. Production app has no /testing routes.
                code = fixture.post("/api/v1/admin/devices/pairing-codes").json()["code"]
                return {"code": code, "statement_id": statement_id}

            uvicorn.run(app, host="127.0.0.1", port=8014, log_level="warning")
