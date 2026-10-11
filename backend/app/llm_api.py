from __future__ import annotations

import json
from decimal import Decimal

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import func, select

from .core import DomainError, utc_naive
from .llm_jobs import day_start, enqueue_model, queue_source, require_config
from .llm_schemas import (
    DeleteModelConfigRequest,
    ModelConfigInput,
    ParseRequest,
    PublishRequest,
    RejectRequest,
    ReviewEdit,
)
from .llm_services import candidate_view, edit_candidate, pending, publish_candidate
from .mail_api import paginated, task_json
from .mail_crypto import encrypt_secret
from .mail_jobs import finish_row
from .models import Bank, MailSource, ModelCall, ModelCandidate, ModelConfig, ModelRun, Task
from .security import administrator, audit, get_db
from .services import require, stamp

router = APIRouter(prefix="/api/v1/admin", dependencies=[Depends(administrator, scope="function")])
CONFIG_FIELDS = [
    "version",
    "base_url",
    "model",
    "active",
    "auto_parse",
    "timeout_seconds",
    "max_tokens",
    "input_chars",
    "daily_call_limit",
    "task_call_limit",
    "json_mode",
    "input_price",
    "output_price",
    "price_currency",
]


def config_view(config):
    if not config or not config.key_ciphertext:
        return dict(configured=False, version=config.version if config else 0)
    return dict(
        configured=True,
        **{k: getattr(config, k) for k in CONFIG_FIELDS},
        api_key_configured=bool(config.key_ciphertext),
        privacy_values_configured=bool(config.privacy_ciphertext),
        auto_publish_profiles=json.loads(config.auto_publish_profiles),
    )


@router.get("/model-config")
def get_config(request: Request, db=Depends(get_db, scope="function")):
    config = db.get(ModelConfig, "primary")
    calls = db.scalars(select(ModelCall).where(ModelCall.created_at >= day_start())).all()
    costs = {}
    for call in calls:
        if call.cost_estimate is not None:
            costs[call.price_currency] = costs.get(call.price_currency, Decimal(0)) + Decimal(
                call.cost_estimate
            )
    return dict(
        config=config_view(config),
        master_key_configured=bool(request.app.state.settings.master_key),
        usage={
            "day_timezone": "Asia/Shanghai",
            "reserved_calls": len(calls),
            "unknown_cost_calls": sum(c.cost_estimate is None for c in calls),
            "cost_estimates": {k: str(v) for k, v in costs.items()},
        },
        pending_candidates=db.scalar(
            select(func.count()).select_from(ModelCandidate).where(ModelCandidate.state == "pending")
        ),
    )


@router.put("/model-config")
def save_config(
    data: ModelConfigInput,
    request: Request,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    row = db.get(ModelConfig, "primary")
    if data.expected_version != (row.version if row else 0):
        raise DomainError("MODEL_VERSION_CONFLICT", "模型配置已被修改，请刷新", 409)
    if (not row or not row.key_ciphertext) and data.api_key is None:
        raise DomainError("MODEL_KEY_REQUIRED", "首次配置必须提供 API Key", 422)
    for profile in data.auto_publish_profiles:
        bank = db.scalar(select(Bank).where(Bank.code == profile.bank_code, Bank.active.is_(True)))
        if not bank:
            raise DomainError("MODEL_PROFILE_INVALID", "自动发布格式只能选择已存在且启用的银行", 422)
    settings = request.app.state.settings
    # Validate master key even when retaining an existing ciphertext.
    empty = encrypt_secret(settings, "[]")
    if row is None:
        row = ModelConfig(id="primary", key_ciphertext="", privacy_ciphertext=empty, version=1)
        db.add(row)
    else:
        row.version += 1
        if not row.key_ciphertext:
            row.privacy_ciphertext = empty
    if data.api_key is not None:
        row.key_ciphertext = encrypt_secret(settings, data.api_key)
    if data.privacy_values is not None:
        row.privacy_ciphertext = encrypt_secret(settings, json.dumps(data.privacy_values, ensure_ascii=False))
    values = data.model_dump(
        exclude={"expected_version", "api_key", "privacy_values", "auto_publish_profiles"}
    )
    for name, value in values.items():
        setattr(
            row, name, str(value) if name in {"input_price", "output_price"} and value is not None else value
        )
    row.auto_publish_profiles = json.dumps([p.model_dump() for p in data.auto_publish_profiles])
    db.flush()
    audit(
        db,
        actor.id,
        "model.config_updated",
        row.id,
        json.dumps(
            {
                "version": row.version,
                "auto_parse": row.auto_parse,
                "auto_publish_profiles": len(data.auto_publish_profiles),
            }
        ),
    )
    return config_view(row)


@router.delete("/model-config")
def delete_config(
    data: DeleteModelConfigRequest,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    row = db.get(ModelConfig, "primary")
    if not row or not row.key_ciphertext:
        raise DomainError("LLM_NOT_CONFIGURED", "没有可删除的模型配置", 409)
    if data.expected_version != row.version:
        raise DomainError("MODEL_VERSION_CONFLICT", "模型配置已被修改，请刷新", 409)
    # Keep only a version tombstone: re-adding must never match old tasks/cache entries.
    row.version += 1
    row.base_url = row.model = row.key_ciphertext = row.privacy_ciphertext = ""
    row.active = row.auto_parse = row.json_mode = False
    row.auto_publish_profiles = "[]"
    row.input_price = row.output_price = None
    for task in db.scalars(
        select(Task).where(
            Task.kind.in_(["llm_test", "llm_parse"]),
            Task.resource_key == "model:primary",
            Task.state.in_(["queued", "retry_wait"]),
        )
    ):
        finish_row(task, "cancelled", "LLM_CONFIG_CHANGED", "模型配置已删除，不再发送资料")
        run_id = json.loads(task.payload).get("run_id")
        run = db.get(ModelRun, run_id) if run_id else None
        if run:
            run.state, run.error_code = "cancelled", "LLM_CONFIG_CHANGED"
    # Running calls keep their leases; guard_config rejects their results on completion.
    audit(db, actor.id, "model.config_deleted", row.id, json.dumps({"version": row.version}))
    return config_view(row)


@router.post("/model-config/test", status_code=202)
def test_config(db=Depends(get_db, scope="function"), actor=Depends(administrator, scope="function")):
    task = enqueue_model(db, "llm_test", require_config(db, active=False))
    audit(db, actor.id, "model.test_queued", task.id)
    return task_json(task)


def run_view(db, run, details=False):
    task = db.get(Task, run.task_id)
    candidates = db.scalars(select(ModelCandidate.id).where(ModelCandidate.run_id == run.id)).all()
    result = dict(
        id=run.id,
        source_id=run.source_id,
        input_id=run.input_id,
        config_version=run.config_version,
        state=run.state,
        warnings=json.loads(run.warnings),
        error_code=run.error_code,
        prompt_version=run.prompt_version,
        schema_version=run.schema_version,
        validator_version=run.validator_version,
        candidate_ids=candidates,
        task=task_json(task) if task else None,
        created_at=stamp(run.created_at),
    )
    if details:
        result["result"] = json.loads(run.result_json)
        result["source_ids"] = list(
            db.scalars(select(MailSource.id).where(MailSource.input_id == run.input_id))
        )
    return result


@router.post("/sources/{object_id}/parse", status_code=202)
def parse_source(
    object_id: str,
    data: ParseRequest,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    run = queue_source(db, require(db, MailSource, object_id), force=data.force)
    audit(db, actor.id, "model.parse_requested", run.id, json.dumps({"force": data.force}))
    return run_view(db, run)


@router.get("/model-runs")
def runs(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db=Depends(get_db, scope="function")
):
    return paginated(
        db,
        select(ModelRun).order_by(ModelRun.created_at.desc(), ModelRun.id),
        lambda r: run_view(db, r),
        page,
        page_size,
    )


@router.get("/model-runs/{object_id}")
def run_detail(object_id: str, db=Depends(get_db, scope="function")):
    return run_view(db, require(db, ModelRun, object_id), True)


def call_view(row):
    fields = [
        "id",
        "run_id",
        "task_id",
        "config_version",
        "model",
        "kind",
        "state",
        "error_code",
        "input_tokens",
        "output_tokens",
        "cost_estimate",
        "price_currency",
        "input_price",
        "output_price",
        "prompt_version",
        "schema_version",
        "validator_version",
    ]
    return {
        **{k: getattr(row, k) for k in fields},
        "created_at": stamp(row.created_at),
        "finished_at": stamp(row.finished_at),
    }


@router.get("/model-calls")
def calls(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100), db=Depends(get_db, scope="function")
):
    return paginated(
        db, select(ModelCall).order_by(ModelCall.created_at.desc(), ModelCall.id), call_view, page, page_size
    )


@router.get("/candidates")
def candidates(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: str | None = None,
    run_id: str | None = None,
    db=Depends(get_db, scope="function"),
):
    query = select(ModelCandidate)
    if state:
        query = query.where(ModelCandidate.state == state)
    if run_id:
        query = query.where(ModelCandidate.run_id == run_id)
    return paginated(
        db,
        query.order_by(ModelCandidate.created_at.desc(), ModelCandidate.id),
        lambda c: candidate_view(db, c),
        page,
        page_size,
    )


@router.get("/candidates/{object_id}")
def candidate_detail(object_id: str, db=Depends(get_db, scope="function")):
    return candidate_view(db, require(db, ModelCandidate, object_id), True)


@router.patch("/candidates/{object_id}")
def edit(
    object_id: str,
    data: ReviewEdit,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    return edit_candidate(db, require(db, ModelCandidate, object_id), data, actor)


@router.post("/candidates/{object_id}/publish")
def publish(
    object_id: str,
    data: PublishRequest,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    return publish_candidate(db, require(db, ModelCandidate, object_id), data, actor)


@router.post("/candidates/{object_id}/reject")
def reject(
    object_id: str,
    data: RejectRequest,
    db=Depends(get_db, scope="function"),
    actor=Depends(administrator, scope="function"),
):
    row = require(db, ModelCandidate, object_id)
    pending(row, data.expected_version)
    row.state = "rejected"
    row.version += 1
    row.reviewed_by = actor.id
    row.reviewed_at = utc_naive()
    audit(db, actor.id, "candidate.rejected", row.id, json.dumps({"reason": data.reason}, ensure_ascii=False))
    return candidate_view(db, row, True)
