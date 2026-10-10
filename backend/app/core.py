from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="BILLS_", env_file=".env", extra="ignore")
    database_url: str = "sqlite:///./data/bills.db"
    public_origin: str = "http://localhost:5173"
    secure_cookies: bool = False
    session_hours: int = 12
    device_days: int = 90
    env: str = "development"
    master_key: str = ""
    storage_dir: str = ""
    mail_max_bytes: int = 20 * 1024 * 1024
    attachment_max_bytes: int = 10 * 1024 * 1024
    storage_max_bytes: int = 1024 * 1024 * 1024
    input_max_chars: int = 250_000
    pdf_max_pages: int = 80
    input_timeout_seconds: int = 30
    mail_timeout_seconds: int = 20
    sync_batch_size: int = 20
    sync_budget_seconds: int = 120
    task_lease_seconds: int = 180
    worker_poll_seconds: int = 5

    def storage_path(self) -> Path:
        if self.storage_dir:
            return Path(self.storage_dir).resolve()
        if self.database_url == "sqlite:///:memory:":
            raise ValueError("内存数据库必须指定 storage_dir")
        return Path(self.database_url.removeprefix("sqlite:///")).resolve().parent / "sources"

    def validate_runtime(self) -> None:
        if not self.database_url.startswith("sqlite:///"):
            raise ValueError("当前仅支持 SQLite 数据库")
        if self.env == "production" and (
            not self.secure_cookies or not self.public_origin.startswith("https://")
        ):
            raise ValueError("生产环境必须配置 HTTPS public_origin 和 secure_cookies")
        if self.session_hours < 1 or self.device_days < 1:
            raise ValueError("凭证有效期必须大于零")
        if self.master_key:
            from cryptography.fernet import Fernet

            try:
                Fernet(self.master_key.encode("ascii"))
            except (ValueError, UnicodeError) as e:
                raise ValueError("master_key 必须是有效的 Fernet 密钥") from e
        if not (1 <= self.sync_batch_size <= 100 and 1 <= self.mail_timeout_seconds <= 60):
            raise ValueError("收信批量或超时配置超出允许范围")
        if not (60 <= self.task_lease_seconds <= 900 and 1 <= self.worker_poll_seconds <= 60):
            raise ValueError("worker 租约或轮询间隔超出允许范围")
        if not (10 <= self.sync_budget_seconds <= 300 and 1 <= self.input_timeout_seconds <= 60):
            raise ValueError("任务时间配额超出允许范围")
        if not (1 <= self.pdf_max_pages <= 200 and 1000 <= self.input_max_chars <= 1_000_000):
            raise ValueError("模型输入长度或 PDF 页数配置超出允许范围")
        if not (1024 <= self.attachment_max_bytes <= self.mail_max_bytes <= 20 * 1024 * 1024):
            raise ValueError("文件大小配置超出允许范围")
        if self.storage_max_bytes < self.mail_max_bytes:
            raise ValueError("资料存储配额不能小于单封邮件大小")
        if self.database_url != "sqlite:///:memory:":
            Path(self.database_url.removeprefix("sqlite:///")).parent.mkdir(parents=True, exist_ok=True)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def utc_naive() -> datetime:
    return utcnow().replace(tzinfo=None)


def new_id() -> str:
    return uuid4().hex


class DomainError(Exception):
    def __init__(self, code: str, message: str, status: int = 422):
        self.code, self.message, self.status = code, message, status
