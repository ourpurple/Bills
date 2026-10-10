"""Run separately from the API: python -m app.worker [--once]."""

import argparse
import os
import signal
import threading
from datetime import timedelta

from sqlalchemy import select

from .core import Settings, utc_naive
from .database import make_engine, make_session_factory
from .mail_jobs import Worker
from .models import WorkerHeartbeat


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description="Bills 邮件资料 worker")
    parser.add_argument("--once", action="store_true", help="执行一轮调度及至多一个任务")
    parser.add_argument("--healthcheck", action="store_true", help="检查最近 worker 心跳，不执行任务")
    args = parser.parse_args()
    settings = Settings()
    settings.validate_runtime()
    engine = make_engine(settings)
    sessions = make_session_factory(engine)
    if args.healthcheck:
        try:
            with sessions() as db:
                recent = db.scalar(
                    select(WorkerHeartbeat.id)
                    .where(
                        WorkerHeartbeat.seen_at
                        >= utc_naive() - timedelta(seconds=max(45, settings.worker_poll_seconds * 3))
                    )
                    .limit(1)
                )
            raise SystemExit(0 if recent else 1)
        except Exception:
            raise SystemExit(1) from None
        finally:
            engine.dispose()
    worker = Worker(settings, sessions)
    stopped = threading.Event()

    def stop(*_):
        stopped.set()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    try:
        while not stopped.is_set():
            try:
                did_work = worker.run_once()
            except Exception:
                # Never print potentially secret-bearing library exceptions.
                print("worker 数据库暂时不可用；等待下轮，不输出私人诊断内容", flush=True)
                did_work = False
            if args.once:
                break
            if not did_work:
                stopped.wait(settings.worker_poll_seconds)
    finally:
        engine.dispose()


if __name__ == "__main__":
    main()
