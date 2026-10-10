"""Model tasks reuse P2 leases. Reserve call budgets before network work."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from .core import DomainError, new_id, utc_naive
from .llm_adapter import ModelFailure, strict_json
from .llm_prompt import messages
from .llm_schemas import PROMPT_VERSION, SCHEMA_VERSION, VALIDATOR_VERSION, PublishRequest
from .llm_services import (
    decode_result,
    existing_statement,
    match_accounts,
    output_warnings,
    publish_candidate,
    suggested_fields,
)
from .mail_crypto import decrypt_secret
from .mail_jobs import ACTIVE, finish_row, owned, transaction
from .mail_storage import read_blob
from .models import Bank, MailSource, ModelCall, ModelCandidate, ModelConfig, ModelRun, PreparedInput, Task
from .security import audit


def day_start():
    today = datetime.now(ZoneInfo("Asia/Shanghai")).replace(hour=0, minute=0, second=0, microsecond=0)
    return today.astimezone(timezone.utc).replace(tzinfo=None)


def require_config(db, *, active=True):
    config = db.get(ModelConfig, "primary")
    if not config or (active and not config.active):
        raise DomainError("LLM_NOT_CONFIGURED", "请先保存并启用模型配置", 409)
    return config


def enqueue_model(db, kind, config, *, source=None, run=None):
    key = f"llm:{kind}:{run.id if run else config.version}"
    current = db.scalar(select(Task).where(Task.active_key == key))
    if current:
        return current
    if db.scalar(select(func.count()).select_from(Task).where(Task.state.in_(ACTIVE))) >= 1000:
        raise DomainError("TASK_QUEUE_FULL", "任务队列已达到上限，请等待或处理失败任务", 429)
    row = Task(
        kind=kind,
        active_key=key,
        resource_key="model:primary",
        source_id=source.id if source else None,
        payload=json.dumps({"config_version": config.version, "run_id": run.id if run else None}),
        max_attempts=config.task_call_limit,
    )
    db.add(row)
    db.flush()
    return row


def queue_source(db, source, *, force=False):
    config = require_config(db)
    if not source.input_id or source.state not in {"ready", "partial"}:
        raise DomainError("INPUT_NOT_READY", "仅已准备或部分准备的资料可交给模型", 409)
    # An active run is shared even if a second click requests a forced run.
    current = db.scalar(
        select(ModelRun)
        .join(Task, Task.id == ModelRun.task_id)
        .where(
            ModelRun.input_id == source.input_id,
            ModelRun.config_version == config.version,
            Task.state.in_(ACTIVE),
        )
    )
    if current:
        return current
    cache_key = f"{source.input_id}:{config.version}:{PROMPT_VERSION}:{SCHEMA_VERSION}"
    previous = db.scalar(select(ModelRun).where(ModelRun.cache_key == cache_key))
    if previous and not force:
        return previous
    run = ModelRun(
        cache_key=cache_key + (":" + new_id() if force else ""),
        source_id=source.id,
        input_id=source.input_id,
        config_version=config.version,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        validator_version=VALIDATOR_VERSION,
    )
    db.add(run)
    db.flush()
    task = enqueue_model(db, "llm_parse", config, source=source, run=run)
    run.task_id = task.id
    audit(
        db, "worker" if config.auto_parse else "admin", "model.queued", run.id, json.dumps({"force": force})
    )
    return run


def schedule_models(sessions):
    with transaction(sessions) as db:
        config = db.get(ModelConfig, "primary")
        if not config or not config.active or not config.auto_parse:
            return
        # Opt-in covers prepared backlog. A failed/cached run is not automatically resubmitted.
        sources = db.scalars(
            select(MailSource)
            .where(MailSource.state.in_(["ready", "partial"]), MailSource.input_id.is_not(None))
            .order_by(MailSource.created_at)
        ).all()
        if db.scalar(select(func.count()).select_from(Task).where(Task.state.in_(ACTIVE))) >= 980:
            return
        queued = 0
        for source in sources:
            key = f"{source.input_id}:{config.version}:{PROMPT_VERSION}:{SCHEMA_VERSION}"
            if db.scalar(select(ModelRun.id).where(ModelRun.cache_key == key)):
                continue
            queue_source(db, source)
            queued += 1
            if queued >= 20:
                break


def retry_model(db, old):
    config = require_config(db, active=old.kind != "llm_test")
    if old.kind == "llm_test":
        return enqueue_model(db, "llm_test", config)
    source = db.get(MailSource, old.source_id)
    if not source:
        raise DomainError("SOURCE_UNAVAILABLE", "来源资料不存在", 404)
    return db.get(Task, queue_source(db, source, force=True).task_id)


def config_snapshot(row):
    fields = [
        "version",
        "base_url",
        "model",
        "timeout_seconds",
        "max_tokens",
        "input_chars",
        "daily_call_limit",
        "task_call_limit",
        "json_mode",
        "input_price",
        "output_price",
        "price_currency",
        "auto_publish_profiles",
        "key_ciphertext",
        "privacy_ciphertext",
    ]
    return {key: getattr(row, key) for key in fields}


def guard_config(db, task):
    version = json.loads(task.payload)["config_version"]
    config = require_config(db, active=task.kind != "llm_test")
    if config.version != version:
        raise DomainError("LLM_CONFIG_CHANGED", "模型配置已更新，旧结果不会提交，请用新配置重新识别", 409)
    return config


def execute(worker, task):
    with transaction(worker.sessions) as db:
        row = owned(db, task.id, task.lease_token)
        config = config_snapshot(guard_config(db, row))
        run_id = json.loads(row.payload).get("run_id")
        run = db.get(ModelRun, run_id) if run_id else None
        input_id = run.input_id if run else None
        source_media = db.get(MailSource, run.source_id).media_type if run else None
        prepared_row = db.get(PreparedInput, input_id) if run else None
        converter = prepared_row.converter_version if prepared_row else None
        input_ready = bool(
            run and prepared_row.state == "ready" and db.get(MailSource, run.source_id).state == "ready"
        )
        banks = [{"code": bank.code, "name": bank.name} for bank in db.scalars(select(Bank))]
    config["api_key"] = decrypt_secret(worker.settings, config.pop("key_ciphertext"))
    privacy = strict_json(decrypt_secret(worker.settings, config.pop("privacy_ciphertext")))
    parts = []
    original = []
    warnings = []
    replacements = []
    if run_id:
        prepared = strict_json(read_blob(worker.settings, input_id, "input"))
        if prepared.get("id") != input_id or prepared.get("version") != converter:
            raise DomainError("SOURCE_CORRUPT", "模型输入资料标识或转换版本不一致", 409)
        original = prepared["parts"]
        warnings = list(prepared["warnings"])
        remaining = config["input_chars"]
        for index, part in enumerate(original, 1):
            text = part["text"][: max(0, remaining)]
            remaining -= len(text)
            if text:
                parts.append({"part": index, "text": text})
            if len(text) != len(part["text"]):
                warnings.append("LLM_INPUT_TRUNCATED")
        # Only explicit known-value replacement, with local reversible mapping. No discovery/regex.
        used_text = "\n".join(part["text"] for part in parts)
        for index, value in enumerate(privacy, 1):
            placeholder = f"<BILLS_PRIVATE_{index}>"
            while placeholder in used_text:
                placeholder = "<" + placeholder + ">"
            replacements.append((placeholder, value))
            for part in parts:
                part["text"] = part["text"].replace(value, placeholder)
        payload = messages(parts, banks, sorted(set(warnings)))
    else:
        payload = [
            {"role": "system", "content": "Return only a JSON object. No tools."},
            {
                "role": "user",
                "content": 'Return {"ok":true}. This is a connection test, not a bank statement.',
            },
        ]
    with transaction(worker.sessions) as db:
        row = owned(db, task.id, task.lease_token)
        current = guard_config(db, row)
        # Count reservations across versions, including unknown outcomes after crashes/timeouts.
        if (
            db.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.created_at >= day_start()))
            >= current.daily_call_limit
        ):
            raise ModelFailure("LLM_DAILY_BUDGET", "今日模型调用次数已达到上限；不会自动超额")
        if (
            db.scalar(select(func.count()).select_from(ModelCall).where(ModelCall.task_id == row.id))
            >= current.task_call_limit
        ):
            raise ModelFailure("LLM_TASK_BUDGET", "该任务模型调用次数已达到上限；人工重试会创建新任务")
        call = ModelCall(
            task_id=row.id,
            run_id=run_id,
            config_version=current.version,
            model=current.model,
            kind=row.kind,
            price_currency=current.price_currency,
            input_price=current.input_price,
            output_price=current.output_price,
            prompt_version=PROMPT_VERSION,
            schema_version=SCHEMA_VERSION,
            validator_version=VALIDATOR_VERSION,
        )
        db.add(call)
        db.flush()
        call_id = call.id
        if run_id:
            db.get(ModelRun, run_id).state = "running"
    reply = None
    failure = None
    try:
        reply = worker.model_client(config, payload)
        result = decode_result(reply.content, replacements) if run_id else strict_json(reply.content)
        if not run_id and (not isinstance(result, dict) or result.get("ok") is not True):
            raise ModelFailure("LLM_TEST_INVALID", "模型未返回预期连接测试结果")
    except ModelFailure as error:
        failure = error
    except Exception:
        failure = ModelFailure(
            "LLM_OUTPUT_INVALID", "模型返回无效内容或适配失败，不输出私人响应", retryable=True
        )
    with transaction(worker.sessions) as db:
        call = db.get(ModelCall, call_id)
        call.finished_at = utc_naive()
        call.state = "failed" if failure else "succeeded"
        call.error_code = failure.code if failure else None
        if reply:
            call.input_tokens, call.output_tokens = reply.input_tokens, reply.output_tokens
            if all(
                x is not None
                for x in [call.input_tokens, call.output_tokens, call.input_price, call.output_price]
            ):
                value = (
                    Decimal(call.input_tokens) * Decimal(call.input_price)
                    + Decimal(call.output_tokens) * Decimal(call.output_price)
                ) / Decimal(1_000_000)
                call.cost_estimate = format(
                    value.quantize(Decimal("0.00000001"), rounding=ROUND_HALF_UP), "f"
                )
    if failure:
        raise failure
    with transaction(worker.sessions) as db:
        row = owned(db, task.id, task.lease_token)
        current = guard_config(db, row)
        if not run_id:
            row.result = json.dumps({"ok": True, "bill_parsed": False, "call_id": call_id})
            finish_row(row, "succeeded")
            return
        run = db.get(ModelRun, run_id)
        # Recheck inside the publishing transaction: source readiness may change during a call.
        current_source = db.get(MailSource, run.source_id)
        current_input = db.get(PreparedInput, run.input_id)
        input_ready = bool(
            input_ready
            and current_source
            and current_input
            and current_source.state == "ready"
            and current_source.input_id == run.input_id
            and current_input.state == "ready"
        )
        run.state, run.error_code = "succeeded", None
        if result["warnings"]:
            warnings.append("MODEL_WARNING")
        run.warnings = json.dumps(sorted(set(warnings)))
        run.result_json = json.dumps(result, ensure_ascii=False)
        candidates = []
        for index, bill in enumerate(result["statements"], 1):
            match, matches = match_accounts(db, bill)
            issues = sorted(set(warnings + output_warnings(bill, original)))
            candidate = ModelCandidate(
                run_id=run.id,
                item_index=index,
                extraction_json=json.dumps(bill, ensure_ascii=False),
                account_id=matches[0] if len(matches) == 1 else None,
                match_state=match,
                warnings=json.dumps(issues),
            )
            db.add(candidate)
            db.flush()
            candidates.append(candidate.id)
            profiles = json.loads(current.auto_publish_profiles)
            allowed = any(
                profile["bank_code"] == bill["bank_code"]
                and profile["media_type"] == source_media
                and profile["converter_version"] == converter
                and profile["prompt_version"] == PROMPT_VERSION
                and profile["verified"]
                for profile in profiles
            )
            # Never auto-revise or silently overwrite manually entered bills.
            if (
                allowed
                and input_ready
                and not issues
                and match == "unique"
                and existing_statement(db, suggested_fields(candidate)) is None
            ):
                publish_candidate(
                    db,
                    candidate,
                    PublishRequest(expected_version=1, reason="已验证格式自动发布"),
                    SimpleNamespace(id="worker:model"),
                    automatic=True,
                )
        row.result = json.dumps(
            {
                "run_id": run.id,
                "candidate_ids": candidates,
                "is_bill": result["is_bill"],
                "auto_published": any(db.get(ModelCandidate, c).state == "published" for c in candidates),
            }
        )
        finish_row(row, "succeeded")
        audit(
            db, "worker", "model.result_validated", run.id, json.dumps({"candidate_count": len(candidates)})
        )
