from datetime import date
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator


class MailboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    name: str = Field(min_length=1, max_length=80)
    address: str = Field(min_length=3, max_length=254)
    username: str = Field(min_length=1, max_length=254)
    protocol: Literal["imap", "pop3"] = "imap"
    host: str = Field(min_length=1, max_length=253)
    port: int = Field(993, ge=1, le=65535)
    tls_mode: Literal["ssl", "starttls"] = "ssl"
    folder: str = Field("INBOX", min_length=1, max_length=120)
    active: bool = True
    interval_minutes: int = Field(30, ge=5, le=1440)
    initial_days: int = Field(90, ge=1, le=3650)
    sender_filters: list[str] = Field(default_factory=list, max_length=20)
    subject_filters: list[str] = Field(default_factory=list, max_length=20)
    secret: Annotated[str, StringConstraints(strip_whitespace=False)] | None = Field(
        None, min_length=1, max_length=1024
    )
    expected_version: int | None = Field(None, ge=1)

    @field_validator("host")
    @classmethod
    def host_name(cls, value):
        import ipaddress

        if any(c in value for c in "/@?#\\") or any(c.isspace() for c in value):
            raise ValueError("主机只能填写域名或 IP，不填写 URL")
        try:
            return str(ipaddress.ip_address(value))
        except ValueError:
            name = value.encode("idna").decode("ascii").rstrip(".").lower()
            labels = name.split(".")
            if any(
                not p
                or len(p) > 63
                or p.startswith("-")
                or p.endswith("-")
                or not all(c.isalnum() or c == "-" for c in p)
                for p in labels
            ):
                raise ValueError("邮箱主机名无效")
            return name

    @field_validator("folder", "username", "address", "name")
    @classmethod
    def no_controls(cls, value):
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("不允许控制字符")
        return value

    @field_validator("sender_filters", "subject_filters")
    @classmethod
    def filters(cls, values):
        if any(not v.strip() or len(v) > 120 or any(ord(c) < 32 for c in v) for v in values):
            raise ValueError("筛选词需为 1–120 个可见字符")
        return list(dict.fromkeys(v.strip() for v in values))

    @model_validator(mode="after")
    def pop_folder(self):
        if self.protocol == "pop3" and self.folder != "INBOX":
            raise ValueError("POP3 仅支持 INBOX，不支持文件夹")
        return self


class ScanRange(BaseModel):
    model_config = ConfigDict(extra="forbid")
    from_date: date | None = None
    to_date: date | None = None

    @model_validator(mode="after")
    def range_order(self):
        if bool(self.from_date) != bool(self.to_date):
            raise ValueError("补扫必须同时填写开始和结束日期")
        if self.from_date and (self.to_date < self.from_date or (self.to_date - self.from_date).days > 3650):
            raise ValueError("补扫日期无效或超过十年")
        return self
