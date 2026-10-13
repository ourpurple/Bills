"""Cheap header-only screening, not bank/amount extraction or a model verdict.

A positive result only means a *suspected* credit-card statement. Sender strings
are untrusted hints, not proof of authenticity. No body, attachments or links are
opened by this classifier.
"""

import re

STATEMENT = re.compile(r"账单|對帳單|对账单|結單|结单|\b(?:e[- ]?)?statements?\b", re.I)
CREDIT = re.compile(r"信用卡|信用咭|贷记卡|貸記卡|credit[\s_-]*card|cardmember|cccsvc|pccc", re.I)
BANK = re.compile(
    r"银行|銀行|\bbank\b|(?:cmbchina|cgbchina|citicbank|cebbank|abchina|icbc|ccb|bankcomm|cmbc)\.", re.I
)
NON_STATEMENT = re.compile(
    r"还款提醒|還款提醒|还款通知|還款通知|催收|逾期提醒|账单分期|賬單分期|分期优惠|"
    r"营销|行销|促销|优惠|積分|积分|活动|中奖|额度调整|申请|申請|储蓄|儲蓄|借记|借記|"
    r"\b(?:reminder|promotion|offer|debit|savings|application)\b",
    re.I,
)


def suspected_credit_card_bill(subject: str, sender: str) -> bool:
    subject = re.sub(r"\s+", " ", subject or "")
    sender = sender or ""
    if NON_STATEMENT.search(subject) or not STATEMENT.search(subject):
        return False
    return bool(CREDIT.search(subject + " " + sender) or BANK.search(subject + " " + sender))


def automatic_source_allowed(source) -> bool:
    # PDFs are explicit document uploads; ordinary emails must pass screening,
    # even if they were converted manually or before this policy was introduced.
    return source.reason != "MANUALLY_IGNORED" and (
        source.media_type == "application/pdf" or suspected_credit_card_bill(source.subject, source.sender)
    )
