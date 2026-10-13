"""Shared saved-bill eligibility for mail listing and every model entry point."""

from sqlalchemy import or_, select

from .core import DomainError
from .models import MailSource, ModelCandidate, ModelRun, Statement


def saved_statement_exists(source_id=MailSource.id, input_id=MailSource.input_id):
    # Match both historical runs for this source and cached equivalent inputs.
    # A candidate alone is not a saved bill; voided statements do not count.
    return (
        select(ModelRun.id)
        .join(ModelCandidate, ModelCandidate.run_id == ModelRun.id)
        .join(Statement, Statement.id == ModelCandidate.statement_id)
        .where(
            or_(ModelRun.source_id == source_id, ModelRun.input_id == input_id),
            ModelRun.state == "succeeded",
            ModelCandidate.state.in_(["published", "duplicate"]),
            Statement.status != "void",
        )
        .correlate(MailSource)
        .exists()
    )


def require_parse_eligible(db, source):
    if source is None:
        raise DomainError("SOURCE_UNAVAILABLE", "来源资料不存在", 404)
    if source.state == "skipped":
        raise DomainError("SOURCE_IGNORED", "该邮件已被忽略，请先恢复后再识别", 409)
    if db.scalar(select(saved_statement_exists(source.id, source.input_id))):
        raise DomainError("SOURCE_ALREADY_SAVED", "该邮件已识别并保存为账单，不可重复识别", 409)
