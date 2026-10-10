from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Name = Annotated[str, Field(min_length=1, max_length=80)]
Reason = Annotated[str, Field(min_length=1, max_length=500)]
Mode = Literal["merged", "independent"]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Login(Input):
    model_config = ConfigDict(extra="forbid")
    username: Annotated[str, Field(min_length=1, max_length=80)]
    password: Annotated[str, Field(min_length=1, max_length=256)]


class BankCreate(Input):
    code: Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{1,31}$")]
    name: Name
    default_mode: Mode


class BankUpdate(Input):
    name: Name
    default_mode: Mode
    active: bool


class HolderCreate(Input):
    name: Name


class HolderUpdate(HolderCreate):
    active: bool


class GroupCreate(Input):
    bank_id: Name
    cardholder_id: Name
    name: Name


class GroupUpdate(Input):
    name: Name
    active: bool


class AccountCreate(Input):
    group_id: Name
    name: Name
    mode: Mode | None = None
    statement_day: Annotated[int, Field(ge=1, le=31)] | None = None


class AccountUpdate(Input):
    name: Name
    active: bool
    statement_day: Annotated[int, Field(ge=1, le=31)] | None = None


class CardCreate(Input):
    account_id: Name
    last_four: Annotated[str, Field(pattern=r"^\d{4}$")]
    name: Name


class CardUpdate(Input):
    name: Name
    active: bool


class StatementFields(Input):
    amount: Decimal | None = None
    minimum: Decimal | None = None
    due_date: date | None = None
    period_start: date | None = None
    period_end: date | None = None

    @field_validator("amount", "minimum")
    @classmethod
    def exact_money(cls, value):
        if value is not None and (
            not value.is_finite()
            or abs(value) > Decimal("999999999999.99")
            or value * 100 != (value * 100).to_integral_value()
        ):
            raise ValueError("金额必须为有限值，最多两位小数且不能超过范围")
        return value

    @model_validator(mode="after")
    def relationships(self):
        if self.minimum is not None and self.minimum < 0:
            raise ValueError("最低还款额不能为负数")
        if self.amount is not None and self.minimum is not None and self.minimum > max(self.amount, 0):
            raise ValueError("最低还款额不能超过本期应还")
        if self.period_start and self.period_end and self.period_start > self.period_end:
            raise ValueError("账期起始不能晚于结束")
        return self


class StatementCreate(StatementFields):
    account_id: Name
    statement_date: date
    currency: Literal["CNY", "USD", "EUR", "HKD", "JPY"] = "CNY"
    reason: Reason = "人工录入"


class StatementUpdate(StatementFields):
    expected_version: Annotated[int, Field(ge=1)]
    reason: Reason


class VersionAction(Input):
    expected_version: Annotated[int, Field(ge=1)]
    reason: Reason


class Payment(Input):
    amount: Decimal
    currency: Literal["CNY", "USD", "EUR", "HKD", "JPY"]
    occurred_at: datetime
    note: Annotated[str, Field(max_length=500)] = ""
    confirm_exception: bool = False

    @field_validator("amount")
    @classmethod
    def positive_money(cls, value):
        StatementFields.exact_money(value)
        if value <= 0:
            raise ValueError("记录金额必须大于零")
        return value

    @field_validator("occurred_at")
    @classmethod
    def aware_time(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("时间必须包含时区")
        return value


class Revoke(Input):
    reason: Reason


class Pair(Input):
    code: Annotated[str, Field(min_length=16, max_length=128)]
    name: Name
