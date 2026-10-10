"""Isolated browser fixture worker; not copied into the production Docker image.
Predetermined synthetic replies, not a local bank parser and never an HTTP provider.
"""

import json
import signal
import sys
import threading
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))
from app.core import Settings  # noqa: E402
from app.database import make_engine, make_session_factory  # noqa: E402
from app.llm_adapter import ModelFailure, ModelReply  # noqa: E402
from app.mail_jobs import Worker  # noqa: E402


def synthetic_reply(configuration, messages):
    name = configuration["model"]
    if name == "synthetic-connection":
        return ModelReply('{"ok":true}', 10, 5)
    if name not in {"synthetic-review-v1", "synthetic-review-v2"}:
        raise ModelFailure("TEST_FIXTURE_ONLY", "隔离测试进程只支持预定义合成模型")
    amount = "1000.00" if name == "synthetic-review-v1" else "900.00"
    quotes = dict(
        bank_code="招商银行",
        holder_name="模型验收持卡人",
        card_last_four="5678",
        statement_date="2026-09-01",
        due_date="2026-09-21",
        currency="人民币",
        amount=amount,
        minimum="100.00",
    )
    tx = dict(
        transaction_date="2026-08-20",
        posted_date=None,
        description="合成商店",
        amount="30.00",
        direction="debit",
        currency="CNY",
        card_last_four="5678",
        evidence=dict(part=1, quote="合成商店"),
    )
    bill = dict(
        bank_code="cmb",
        holder_name="模型验收持卡人",
        card_last_four=["5678"],
        statement_date="2026-09-01",
        period_start=None,
        period_end=None,
        due_date="2026-09-21",
        currency="CNY",
        amount=amount,
        minimum="100.00",
        credit_limit=None,
        transaction_status="complete",
        transactions=[tx, tx],
        evidence={k: dict(part=1, quote=v) for k, v in quotes.items()},
        warnings=[],
    )
    return ModelReply(
        json.dumps(
            dict(schema_version="bill-v1", is_bill=True, statements=[bill], warnings=[]), ensure_ascii=False
        ),
        100,
        20,
    )


if __name__ == "__main__":
    settings = Settings()
    database = Path(settings.database_url.removeprefix("sqlite:///")).resolve()
    if (
        settings.env != "development"
        or settings.public_origin != "http://localhost:5173"
        or not database.parent.name.startswith("bills-browser-test-")
    ):
        raise SystemExit("Only an isolated browser-test database is allowed")
    engine = make_engine(settings)
    worker = Worker(settings, make_session_factory(engine), model_client=synthetic_reply)
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    try:
        while not stopped.is_set():
            if not worker.run_once():
                stopped.wait(0.5)
    finally:
        engine.dispose()
