"""Model output validation only: never extract fields from source documents."""

from __future__ import annotations

import ipaddress
from datetime import date
from decimal import Decimal
from typing import Annotated, Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .schemas import Input, StatementFields

PROMPT_VERSION = "bill-prompt-v1"
SCHEMA_VERSION = "bill-v1"
VALIDATOR_VERSION = "bill-check-v1"
Currency = Literal["CNY", "USD", "EUR", "HKD", "JPY"]
DetailStatus = Literal["not_provided", "partial", "complete"]
MoneyText = Annotated[str, Field(pattern=r"^-?[0-9]{1,12}(?:\.[0-9]{1,2})?$")]
Tail = Annotated[str, Field(pattern=r"^[0-9]{4}$")]


def normalize_base(value: str) -> str:
    if any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("模型基础地址必须为无空白的 HTTPS URL")
    u = urlsplit(value)
    if u.scheme != "https" or not u.hostname or u.username or u.password or u.query or u.fragment:
        raise ValueError("仅允许 HTTPS 地址，不允许 URL 凭证、查询参数或片段")
    if u.port is not None and not 1 <= u.port <= 65535:
        raise ValueError("端口无效")
    try:
        ip = ipaddress.ip_address(u.hostname)
        if not ip.is_global or (ip.version == 6 and ip.ipv4_mapped and not ip.ipv4_mapped.is_global):
            raise ValueError("不允许内网、回环或保留地址")
    except ValueError as error:
        if "不允许" in str(error):
            raise
    path = u.path.rstrip("/")
    if "%" in path or "//" in path or any(x in {".", ".."} for x in path.split("/")):
        raise ValueError("基础地址路径无效")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    if not path.endswith("/v1"):
        path += "/v1"
    return urlunsplit(("https", u.netloc.lower(), path, "", ""))


class PublishProfile(Input):
    bank_code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")]
    media_type: Literal["message/rfc822", "application/pdf"]
    prompt_version: Literal["bill-prompt-v1"] = PROMPT_VERSION
    converter_version: Literal["generic-v1"] = "generic-v1"
    verified: Literal[True]


class ModelConfigInput(Input):
    model_config = ConfigDict(extra="forbid")
    expected_version: Annotated[int, Field(ge=0)]
    base_url: Annotated[str, Field(max_length=500)]
    model: Annotated[str, Field(min_length=1, max_length=160)]
    api_key: Annotated[str, Field(min_length=1, max_length=4096)] | None = None
    privacy_values: (
        Annotated[list[Annotated[str, Field(min_length=2, max_length=200)]], Field(max_length=50)] | None
    ) = None
    active: bool = False
    auto_parse: bool = False
    auto_publish_profiles: Annotated[list[PublishProfile], Field(max_length=30)] = Field(default_factory=list)
    timeout_seconds: Annotated[int, Field(ge=5, le=120)] = 60
    max_tokens: Annotated[int, Field(ge=512, le=32768)] = 8192
    input_chars: Annotated[int, Field(ge=1000, le=250000)] = 120000
    daily_call_limit: Annotated[int, Field(ge=1, le=1000)] = 50
    task_call_limit: Annotated[int, Field(ge=1, le=3)] = 2
    json_mode: bool = False
    input_price: Decimal | None = None
    output_price: Decimal | None = None
    price_currency: Literal["CNY", "USD"] = "CNY"

    @field_validator("base_url")
    @classmethod
    def base(cls, value):
        return normalize_base(value)

    @field_validator("api_key", "model")
    @classmethod
    def safe_value(cls, value):
        if value and (any(ord(c) < 32 or ord(c) == 127 for c in value) or (value != value.strip())):
            raise ValueError("模型名和密钥不允许控制字符或首尾空白")
        return value

    @field_validator("input_price", "output_price")
    @classmethod
    def price(cls, value):
        if value is not None and (
            not value.is_finite() or not 0 <= value <= Decimal("1000000") or value.as_tuple().exponent < -8
        ):
            raise ValueError("每百万 token 单价必须为非负有限值，最多八位小数")
        return value

    @model_validator(mode="after")
    def switches(self):
        if not self.active and (self.auto_parse or self.auto_publish_profiles):
            raise ValueError("自动识别或发布须先启用模型")
        return self


class StrictOutput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Evidence(StrictOutput):
    part: Annotated[int, Field(ge=1, le=500)]
    quote: Annotated[str, Field(min_length=1, max_length=240)]


class ModelTransaction(StrictOutput):
    transaction_date: date | None
    posted_date: date | None
    description: Annotated[str, Field(max_length=500)]
    amount: MoneyText | None
    direction: Literal["debit", "credit", "unknown"]
    currency: Currency | None
    card_last_four: Tail | None
    evidence: Evidence | None

    @field_validator("transaction_date", "posted_date", mode="before")
    @classmethod
    def iso_dates(cls, value):
        if isinstance(value, str):
            parsed = date.fromisoformat(value)
            if parsed.isoformat() != value:
                raise ValueError("交易日期必须为 YYYY-MM-DD")
            return parsed
        return value

    @field_validator("amount")
    @classmethod
    def positive(cls, value):
        if value is not None and Decimal(value) < 0:
            raise ValueError("交易金额为绝对值，方向单独返回")
        return value


EvidenceKey = Literal[
    "bank_code",
    "holder_name",
    "card_last_four",
    "statement_date",
    "currency",
    "amount",
    "minimum",
    "due_date",
    "period_start",
    "period_end",
    "credit_limit",
]


class ExtractedBill(StrictOutput):
    bank_code: Annotated[str, Field(max_length=32)] | None
    holder_name: Annotated[str, Field(max_length=80)] | None
    card_last_four: Annotated[list[Tail], Field(max_length=30)]
    statement_date: date | None
    period_start: date | None
    period_end: date | None
    due_date: date | None
    currency: Currency | None
    amount: MoneyText | None
    minimum: MoneyText | None
    credit_limit: MoneyText | None
    transaction_status: DetailStatus
    transactions: Annotated[list[ModelTransaction], Field(max_length=1000)]
    evidence: dict[EvidenceKey, Evidence]
    warnings: Annotated[list[Annotated[str, Field(max_length=180)]], Field(max_length=30)]

    @model_validator(mode="after")
    def relationships(self):
        if self.transaction_status == "not_provided" and self.transactions:
            raise ValueError("没有详单时不能返回交易")
        if self.currency:
            scale = 0 if self.currency == "JPY" else 2
            for value in [self.amount, self.minimum, self.credit_limit]:
                if (
                    value is not None
                    and Decimal(value) * 10**scale != (Decimal(value) * 10**scale).to_integral_value()
                ):
                    raise ValueError("金额精度与币种不符")
        # Pure output consistency checks, with no source-derived inference.
        StatementFields(
            amount=self.amount,
            minimum=self.minimum,
            due_date=self.due_date,
            period_start=self.period_start,
            period_end=self.period_end,
        )
        if self.statement_date and any(
            d and (d < self.statement_date if key == "due" else d > self.statement_date)
            for key, d in [("due", self.due_date), ("start", self.period_start), ("end", self.period_end)]
        ):
            raise ValueError("账单日期关系不一致")
        for tx in self.transactions:
            if (
                tx.currency == "JPY"
                and tx.amount
                and Decimal(tx.amount) != Decimal(tx.amount).to_integral_value()
            ):
                raise ValueError("JPY 交易金额不能有小数")
        return self


class ModelResult(StrictOutput):
    schema_version: Literal["bill-v1"]
    is_bill: bool
    statements: Annotated[list[ExtractedBill], Field(max_length=20)]
    warnings: Annotated[list[Annotated[str, Field(max_length=180)]], Field(max_length=30)]

    @model_validator(mode="after")
    def consistent(self):
        if self.is_bill != bool(self.statements):
            raise ValueError("账单标记与摘要数量矛盾")
        return self


class ParseRequest(Input):
    force: bool = False


class ReviewFields(StatementFields):
    account_id: Annotated[str, Field(min_length=1, max_length=80)]
    statement_date: date
    currency: Currency
    transaction_status: DetailStatus = "not_provided"
    transactions: Annotated[list[ModelTransaction], Field(max_length=1000)] = Field(default_factory=list)

    @model_validator(mode="after")
    def detail_consistency(self):
        if self.transaction_status == "not_provided" and self.transactions:
            raise ValueError("没有详单不能填写交易")
        return self


class ReviewEdit(Input):
    expected_version: Annotated[int, Field(ge=1)]
    fields: ReviewFields
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class PublishRequest(Input):
    expected_version: Annotated[int, Field(ge=1)]
    expected_statement_version: Annotated[int, Field(ge=1)] | None = None
    action: Literal["create", "revise", "duplicate"] = "create"
    acknowledge_warnings: bool = False
    reason: Annotated[str, Field(min_length=1, max_length=500)]


class RejectRequest(Input):
    expected_version: Annotated[int, Field(ge=1)]
    reason: Annotated[str, Field(min_length=1, max_length=500)]
