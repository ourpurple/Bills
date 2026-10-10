"""Secrets never enter task payloads, API responses or diagnostic messages."""

from cryptography.fernet import Fernet, InvalidToken

from .core import DomainError


def cipher(settings):
    if not settings.master_key:
        raise DomainError("MASTER_KEY_REQUIRED", "请先配置服务端主密钥；不能以明文保存邮箱凭证", 503)
    return Fernet(settings.master_key.encode("ascii"))


def encrypt_secret(settings, value: str) -> str:
    return cipher(settings).encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(settings, value: str) -> str:
    try:
        return cipher(settings).decrypt(value.encode("ascii")).decode("utf-8")
    except InvalidToken as e:
        raise DomainError("SECRET_DECRYPT_FAILED", "凭证无法解密，请检查主密钥或重新配置凭证", 503) from e
