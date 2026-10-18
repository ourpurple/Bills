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
    group_id: Name | None = None
    bank_id: Name | None = None
    cardholder_id: Name | None = None
    name: Name
    mode: Mode | None = None
    statement_day: Annotated[int, Field(ge=1, le=31)] | None = None

    @model_validator(mode="after")
    def check_ownership(self):
        if self.group_id is not None:
            if self.bank_id is not None or self.cardholder_id is not None:
                raise ValueError("所属分组与银行、持卡人不能同时指定")
        elif self.bank_id is None or self.cardholder_id is None:
            raise ValueError("请选择银行和持卡人")
        return self


class AccountUpdate(Input):
    name: Name
    active: bool
    statement_day: Annotated[int, Field(ge=1, le=31)] | None = None


class AnnualFeeUpdate(Input):
    annual_fee_month: Annotated[int, Field(ge=1, le=12)] | None = None
    annual_fee_day: Annotated[int, Field(ge=1, le=31)] | None = None
    annual_fee_benefits: Annotated[str, Field(max_length=2000)] | None = None

    @model_validator(mode="after")
    def valid_annual_fee_date(self):
        if ("annual_fee_month" in self.model_fields_set) != ("annual_fee_day" in self.model_fields_set):
            raise ValueError("年费收取月份和日期必须同时填写或清空")
        if (self.annual_fee_month is None) != (self.annual_fee_day is None):
            raise ValueError("年费收取月份和日期必须同时填写或清空")
        if self.annual_fee_month is not None:
            # A recurring month/day has no year; February 29 is a valid choice.
            date(2000, self.annual_fee_month, self.annual_fee_day)
        return self


class CardFields(AnnualFeeUpdate):
    track_bills: bool = True
    card_number: Annotated[str, Field(pattern=r"^[0-9]{12,19}$")] | None = Field(default=None, repr=False)
    expiry: Annotated[str, Field(pattern=r"^(0[1-9]|1[0-2])/[0-9]{2}$")] | None = None
    cvv: Annotated[str, Field(pattern=r"^[0-9]{3,4}$")] | None = Field(default=None, repr=False)
    card_type: Literal["credit", "debit"] | None = None
    card_network: Literal["unionpay", "visa", "mastercard", "amex"] | None = None
    credit_limit: Annotated[Decimal, Field(ge=0, le=Decimal("999999999999.99"), decimal_places=2)] | None = (
        None
    )

    @field_validator("card_number", mode="before")
    @classmethod
    def normalize_card_number(cls, value):
        return value.replace(" ", "").replace("-", "") if isinstance(value, str) else value


class CardCreate(CardFields):
    account_id: Name
    last_four: Annotated[str, Field(pattern=r"^[0-9]{4}$")] | None = None
    name: Name

    @model_validator(mode="after")
    def check_card_tail(self):
        if self.card_number:
            if self.last_four is not None and self.last_four != self.card_number[-4:]:
                raise ValueError("卡号与卡片尾号不一致")
            self.last_four = self.card_number[-4:]
        if self.last_four is None:
            raise ValueError("请填写卡片尾号或完整卡号")
        return self


class CardUpdate(CardFields):
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
