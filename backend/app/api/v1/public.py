"""Public endpoints: the demo lead form and signed partner webhooks."""

from __future__ import annotations

import hashlib
import json
import logging

from fastapi import APIRouter, Depends, Header, Request
from pydantic import ValidationError
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.core.deps import client_ip, correlation_id, get_db
from app.core.errors import PayloadTooLarge, Unprocessable
from app.qualification.rubric import get_rubric
from app.schemas import FormConfig, FormOption, IntakeResponse, PublicLeadForm, WebhookLeadEvent
from app.services import audit, intake, webhooks
from app.services.normalize import InvalidField

router = APIRouter(tags=["intake"])
log = logging.getLogger("lead.intake")


async def raw_body(request: Request, settings: Settings = Depends(get_settings)) -> bytes:
    body = await request.body()
    if len(body) > settings.max_body_bytes:
        raise PayloadTooLarge("Request body too large")
    return body


def _invalid(exc: InvalidField) -> Unprocessable:
    return Unprocessable(
        "Request validation failed",
        code="validation_error",
        details=[{"field": exc.field, "msg": exc.message}],
    )


def _response(result: intake.IntakeResult, cid: str) -> IntakeResponse:
    duplicate = result.replayed or result.outcome.value == "duplicate_ignored"
    return IntakeResponse(
        status="duplicate" if duplicate else "accepted",
        outcome=result.outcome.value,
        lead_reference=f"L-{result.lead.reference}",
        correlation_id=cid,
        replayed=result.replayed,
    )


@router.get("/public/form-config", response_model=FormConfig)
def form_config(settings: Settings = Depends(get_settings)) -> FormConfig:
    rubric = get_rubric()
    return FormConfig(
        business_name=rubric.business_name,
        interests=[FormOption(**o) for o in rubric.interest_options()],
        required_fields=[
            "first_name",
            "last_name",
            "email",
            "interest",
            "message",
            "consent_contact",
            *intake.settings_required(settings),
        ],
        consent_text=intake.CONSENT_TEXT,
        consent_text_version=intake.CONSENT_TEXT_VERSION,
    )


@router.post("/public/leads", response_model=IntakeResponse, status_code=202)
def submit_form(
    form: PublicLeadForm,
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ip: str = Depends(client_ip),
    cid: str = Depends(correlation_id),
) -> IntakeResponse:
    ratelimit.hit("public-form", ip, settings.rate_limit_public_per_minute)
    if form.nickname:
        # Honeypot filled in: answer like a success so bots learn nothing, store nothing.
        audit.record(
            db,
            "intake.spam_dropped",
            outcome="denied",
            ip=ip,
            correlation_id=cid,
            details={"reason": "honeypot"},
        )
        return IntakeResponse(
            status="accepted", outcome="created", lead_reference="L-pending", correlation_id=cid
        )
    try:
        data = intake.normalize_submission(form, settings, get_rubric())
    except InvalidField as exc:
        raise _invalid(exc) from exc
    key = (
        form.submission_id
        or "content:" + hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    )
    result = intake.submit(
        db, data, source="website-form", channel="form", idempotency_key=key, correlation_id=cid
    )
    return _response(result, cid)


@router.post("/webhooks/leads/{source}", response_model=IntakeResponse, status_code=202)
def receive_webhook(
    source: str,
    body: bytes = Depends(raw_body),
    timestamp: str | None = Header(None, alias="X-Lead-Timestamp"),
    signature: str | None = Header(None, alias="X-Lead-Signature"),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
    ip: str = Depends(client_ip),
    cid: str = Depends(correlation_id),
) -> IntakeResponse:
    ratelimit.hit("webhook-ip", ip, settings.rate_limit_webhook_per_minute)
    try:
        webhooks.verify(settings, source, timestamp, signature, body)
    except Exception as exc:
        audit.record(
            db,
            "webhook.rejected",
            outcome="denied",
            ip=ip,
            correlation_id=cid,
            details={"source": source[:60], "reason": getattr(exc, "code", "invalid")},
        )
        db.commit()
        raise
    ratelimit.hit("webhook", source, settings.rate_limit_webhook_per_minute)
    try:
        event = WebhookLeadEvent.model_validate_json(body)
    except ValidationError as exc:
        details = [
            {"field": ".".join(str(p) for p in e["loc"]), "msg": e["msg"]} for e in exc.errors()
        ]
        raise Unprocessable(
            "Request validation failed", code="validation_error", details=details
        ) from exc
    try:
        data = intake.normalize_submission(event.lead, settings, get_rubric())
    except InvalidField as exc:
        raise _invalid(exc) from exc
    result = intake.submit(
        db,
        data,
        source=source,
        channel="webhook",
        idempotency_key=event.event_id,
        correlation_id=cid,
        occurred_at=event.occurred_at,
    )
    return _response(result, cid)
