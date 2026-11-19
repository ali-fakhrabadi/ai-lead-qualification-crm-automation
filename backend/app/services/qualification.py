"""AI qualification: model call, strict validation, deterministic rules, idempotent storage.

The lead row is not locked while the model runs (that can take a minute on a CPU): the status
change is committed first, the model is called, and the result is stored in a new transaction.
A concurrent duplicate run produces the same input hash and loses on the unique index, so a
lead never gets two valid assessments for the same input.
"""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, NotFound, ServiceUnavailable
from app.db.models import (
    Assessment,
    AssessmentStatus,
    Decision,
    DuplicateKind,
    DuplicateReview,
    DuplicateStatus,
    FaultRule,
    Lead,
    LeadStatus,
    Stage,
)
from app.qualification import prompt as prompts
from app.qualification.providers import (
    LLMProvider,
    LLMResult,
    ProviderError,
    ProviderUnavailable,
    get_provider,
)
from app.qualification.rubric import Rubric, get_rubric
from app.qualification.rules import apply_rules, invalid_output_result
from app.qualification.schema import OutputError, QualificationOutput, json_schema, parse_output
from app.services import audit, lifecycle

log = logging.getLogger("lead.qualification")
S = LeadStatus
QUALIFIABLE = frozenset({S.NEW, S.PENDING_QUALIFICATION, S.RETRY_PENDING, S.FAILED})


@dataclass
class QualifyOutcome:
    lead: Lead
    assessment: Assessment
    reused: bool


def lead_context(lead: Lead) -> prompts.LeadContext:
    return prompts.LeadContext(
        interest=lead.interest,
        message=lead.message,
        company=lead.company,
        job_title=lead.job_title,
        company_domain=lead.company_domain,
        country=lead.country,
    )


def input_hash(context: prompts.LeadContext, rubric: Rubric, provider: LLMProvider) -> str:
    material = {
        "lead": context.as_dict(),
        "rubric": rubric.version,
        "provider": provider.name,
        "model": provider.model,
    }
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def _take_fault(db: Session, settings: Settings, lead: Lead) -> str | None:
    if not settings.fault_injection_enabled:
        return None
    rule = db.scalar(
        select(FaultRule)
        .where(
            FaultRule.target == "llm",
            FaultRule.email_normalized == lead.email_normalized,
            FaultRule.remaining > 0,
        )
        .with_for_update()
        .limit(1)
    )
    if rule is None:
        return None
    rule.remaining -= 1
    return rule.mode


def _call(provider: LLMProvider, messages: list[dict[str, str]], fault: str | None) -> LLMResult:
    if fault == "unavailable":
        raise ProviderUnavailable("injected: model unavailable")
    result = provider.chat_json(messages, json_schema(QualificationOutput))
    if fault == "malformed":
        return LLMResult(
            '{"intent_category": "new_project", "service_fit": ',
            result.model,
            result.prompt_tokens,
            result.completion_tokens,
            result.duration_ms,
        )
    return result


def _open_reviews(db: Session, lead_id: uuid.UUID) -> list[DuplicateReview]:
    return list(
        db.scalars(
            select(DuplicateReview).where(
                DuplicateReview.lead_id == lead_id,
                DuplicateReview.status == DuplicateStatus.OPEN,
            )
        )
    )


def qualify(
    db: Session,
    settings: Settings,
    lead_id: uuid.UUID,
    *,
    correlation_id: str,
    force: bool = False,
    provider: LLMProvider | None = None,
) -> QualifyOutcome:
    provider = provider or get_provider(settings)
    rubric = get_rubric()
    lead = db.get(Lead, lead_id, with_for_update=True)
    if lead is None:
        raise NotFound("Lead not found")
    context = lead_context(lead)
    ihash = input_hash(context, rubric, provider)

    existing = db.scalar(
        select(Assessment).where(
            Assessment.lead_id == lead.id,
            Assessment.input_hash == ihash,
            Assessment.status == AssessmentStatus.VALID,
            Assessment.superseded.is_(False),
        )
    )
    if existing is not None and not force:
        # Replayed event or duplicate delivery: no second model call, no state change.
        return QualifyOutcome(lead, existing, reused=True)
    if lead.status not in QUALIFIABLE and not force:
        current = (
            db.get(Assessment, lead.current_assessment_id) if lead.current_assessment_id else None
        )
        if current is not None:
            return QualifyOutcome(lead, current, reused=True)
        raise AppError(
            f"A lead in status {lead.status.value} is not waiting for qualification",
            code="not_qualifiable",
            status_code=409,
        )
    if force:
        db.execute(
            update(Assessment)
            .where(Assessment.lead_id == lead.id, Assessment.superseded.is_(False))
            .values(superseded=True)
        )
        if lead.status not in QUALIFIABLE and lifecycle.can_transition(lead.status, S.NEW):
            lifecycle.transition(lead, S.NEW)
    lifecycle.transition(lead, S.PENDING_QUALIFICATION)
    fault = _take_fault(db, settings, lead)
    db.commit()

    messages = prompts.qualification_messages(rubric, context)
    injection = prompts.looks_like_injection(lead.message, lead.company, lead.job_title)
    started = datetime.now(UTC)
    raw: str | None = None
    result: LLMResult | None = None
    output: QualificationOutput | None = None
    error: str | None = None
    status = AssessmentStatus.VALID
    try:
        result = _call(provider, messages, fault)
        raw = result.content
        try:
            output = parse_output(QualificationOutput, raw)
        except OutputError as first:
            # One repair attempt with the validation error; temperature 0 keeps it cheap.
            repair = [
                *messages,
                {"role": "assistant", "content": raw[:2000]},
                prompts.repair_message(str(first)),
            ]
            fault = _take_fault(db, settings, lead)
            db.commit()
            result = _call(provider, repair, fault)
            raw = result.content
            output = parse_output(QualificationOutput, raw)
    except OutputError as exc:
        status, error = AssessmentStatus.INVALID_OUTPUT, str(exc)
    except ProviderUnavailable as exc:
        status, error = AssessmentStatus.PROVIDER_ERROR, str(exc)
    except ProviderError as exc:
        status, error = AssessmentStatus.PROVIDER_ERROR, f"permanent: {exc}"

    latency = int((datetime.now(UTC) - started).total_seconds() * 1000)
    lead = db.get(Lead, lead_id, with_for_update=True)
    assert lead is not None
    assessment = Assessment(
        id=uuid.uuid4(),
        lead_id=lead.id,
        input_hash=ihash,
        provider=provider.name,
        model=result.model if result else provider.model,
        status=status,
        error=error,
        raw_output=(raw or "")[:8000] or None,
        latency_ms=latency,
        prompt_tokens=result.prompt_tokens if result else None,
        completion_tokens=result.completion_tokens if result else None,
    )

    if status is AssessmentStatus.PROVIDER_ERROR:
        transient = not (error or "").startswith("permanent")
        lifecycle.transition(
            lead, S.RETRY_PENDING if transient else S.FAILED, retry_stage=Stage.QUALIFICATION
        )
        db.add(assessment)
        audit.record(
            db,
            "qualification.provider_error",
            outcome="failure",
            lead_id=lead.id,
            correlation_id=correlation_id,
            details={"provider": provider.name, "transient": transient},
        )
        db.commit()
        if transient:
            raise ServiceUnavailable(
                "The model is not available; the lead is kept and will be retried",
                code="model_unavailable",
                headers={"Retry-After": "30"},
            )
        raise AppError(
            "The model provider rejected the request", code="model_error", status_code=422
        )

    if output is None:
        rules = invalid_output_result()
    else:
        assessment.parsed = output.model_dump(mode="json")
        rules = apply_rules(rubric, output, lead.message, injection_detected=injection)
    for review in _open_reviews(db, lead.id):
        reason = (
            "possible_duplicate_open"
            if review.kind is DuplicateKind.POSSIBLE_SAME_PERSON
            else "same_email_conflict_open"
        )
        if reason not in rules.reasons:
            rules.reasons.append(reason)
        rules.decision = Decision.NEEDS_REVIEW
    assessment.rules = rules.as_dict()
    assessment.decision = rules.decision
    assessment.score = rules.score

    try:
        with db.begin_nested():
            db.add(assessment)
    except IntegrityError:
        # A concurrent run stored the same input first: use that one.
        db.expire_all()
        winner = db.scalar(
            select(Assessment).where(
                Assessment.lead_id == lead_id,
                Assessment.input_hash == ihash,
                Assessment.status == AssessmentStatus.VALID,
                Assessment.superseded.is_(False),
            )
        )
        lead = db.get(Lead, lead_id)
        assert lead is not None
        assert winner is not None
        return QualifyOutcome(lead, winner, reused=True)

    lead.current_assessment_id = assessment.id
    lead.decision = rules.decision
    lead.score = rules.score
    lead.intent_category = output.intent_category.value if output else None
    lead.review_reasons = sorted(set(lead.review_reasons) | set(rules.reasons))
    target = S.QUALIFIED if rules.decision is Decision.QUALIFIED else S.NEEDS_REVIEW
    lifecycle.transition(lead, target)
    if target is S.QUALIFIED:
        lead.qualified_at = datetime.now(UTC)
    audit.record(
        db,
        "qualification.completed",
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={
            "decision": rules.decision.value,
            "score": rules.score,
            "provider": provider.name,
            "model": assessment.model,
            "status": status.value,
            "reasons": rules.reasons,
            "latency_ms": latency,
        },
    )
    db.flush()
    return QualifyOutcome(lead, assessment, reused=False)
