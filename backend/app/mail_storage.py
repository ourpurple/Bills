"""Immutable content-addressed private files. Never use sender-supplied paths."""

import hashlib
import os
import tempfile
from pathlib import Path

from .core import DomainError


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def blob_path(settings, key: str, kind="raw") -> Path:
    if len(key) != 64 or any(c not in "0123456789abcdef" for c in key) or kind not in {"raw", "input"}:
        raise DomainError("INVALID_BLOB", "资料标识无效")
    root = settings.storage_path()
    target = root / kind / key[:2] / key
    if not target.resolve().is_relative_to(root):
        raise DomainError("INVALID_BLOB", "资料路径无效")
    return target


def write_blob(settings, data: bytes, kind="raw", key=None) -> str:
    key = key or content_hash(data)
    target = blob_path(settings, key, kind)
    if target.exists():
        if target.read_bytes() != data:
            raise DomainError("BLOB_CONFLICT", "资料指纹冲突或文件损坏", 409)
        return key
    root = settings.storage_path()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    used = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
    if used + len(data) > settings.storage_max_bytes:
        raise DomainError("STORAGE_QUOTA", "资料存储已达到配额，请先备份并处理历史资料", 503)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp = tempfile.mkstemp(prefix=".writing-", dir=target.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp, target)
    finally:
        Path(temp).unlink(missing_ok=True)
    return key


def read_blob(settings, key, kind="raw") -> bytes:
    path = blob_path(settings, key, kind)
    if not path.is_file():
        raise DomainError("SOURCE_UNAVAILABLE", "原始资料不存在，请恢复备份或重新导入", 404)
    limit = (
        settings.mail_max_bytes if kind == "raw" else settings.input_max_chars * 12 + settings.mail_max_bytes
    )
    if path.stat().st_size > limit:
        raise DomainError("SOURCE_TOO_LARGE", "资料超过读取上限")
    data = path.read_bytes()
    if kind == "raw" and content_hash(data) != key:
        raise DomainError("SOURCE_CORRUPT", "原始资料指纹校验失败", 409)
    return data
