"""Follow-up drafts: generated as drafts, reviewed by a person, delivered only after approval.

Nothing here sends anything on its own: ``deliver`` refuses drafts that are not approved, and
delivery goes to the local sink unless real delivery is explicitly configured.
"""

from __future__ import annotations

import re
import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, Conflict, NotFound, ServiceUnavailable
from app.db.models import (
    Assessment,
    DraftStatus,
    FollowUpDraft,
    Lead,
    LeadStatus,
    Stage,
)
from app.delivery.mail import DeliveryError, send
from app.qualification import prompt as prompts
from app.qualification.providers import (
    LLMProvider,
    ProviderError,
    ProviderUnavailable,
    get_provider,
)
from app.qualification.rubric import get_rubric
from app.qualification.schema import DraftOutput, OutputError, json_schema, parse_output
from app.services import audit, lifecycle, outbox
from app.services.qualification import lead_context

S = LeadStatus
DRAFTABLE = frozenset({S.SYNCED_TO_CRM, S.QUALIFIED, S.FOLLOW_UP_REJECTED})
_URL = re.compile(r"(?i)\b(https?://|www\.)\S+")


def _open_draft(db: Session, lead_id: uuid.UUID) -> FollowUpDraft | None:
    return db.scalar(
        select(FollowUpDraft).where(
            FollowUpDraft.lead_id == lead_id,
            FollowUpDraft.status.in_([DraftStatus.DRAFT, DraftStatus.APPROVED]),
        )
    )


def template_draft(lead: Lead) -> tuple[str, str]:
    business = get_rubric().business_name
    subject = f"Your inquiry to {business}"
    body = (
        f"Hi {lead.first_name},\n\n"
        f"Thank you for contacting {business}. We have received your message about "
        f"{lead.interest.replace('_', ' ')} and would like to understand your process and "
        "timeline before suggesting next steps.\n\n"
        "Would a short call in the coming days work for you?\n\n"
        f"The {business} team"
    )
    return subject, body


def _safe(draft: DraftOutput) -> tuple[str, str] | None:
    """Reject model drafts with links or leaked prompt markers; plain text only."""
    text = f"{draft.subject}\n{draft.body}"
    if _URL.search(text) or "<<<" in text or "Rules:" in text:
        return None
    return draft.subject.strip(), draft.body.strip()


def create_draft(
    db: Session,
    settings: Settings,
    lead_id: uuid.UUID,
    *,
    correlation_id: str,
    provider: LLMProvider | None = None,
) -> tuple[FollowUpDraft, bool]:
    """Returns (draft, created). Idempotent: an open draft is returned as is."""
    lead = db.get(Lead, lead_id, with_for_update=True)
    if lead is None:
        raise NotFound("Lead not found")
    existing = _open_draft(db, lead.id)
    if existing is not None:
        return existing, False
    if lead.status not in DRAFTABLE and not (
        lead.status is S.RETRY_PENDING and lead.retry_stage is Stage.FOLLOW_UP_DRAFT
    ):
        raise AppError(
            f"A lead in status {lead.status.value} is not ready for a follow-up draft",
            code="not_draftable",
            status_code=409,
        )
    assessment = (
        db.get(Assessment, lead.current_assessment_id) if lead.current_assessment_id else None
    )
    context = lead_context(lead)
    first_name = lead.first_name
    db.commit()

    subject, body = template_draft(lead)
    generated_by, model, latency = "template", None, None
    if settings.llm_draft_followups:
        provider = provider or get_provider(settings)
        messages = prompts.draft_messages(
            get_rubric(), first_name, context, (assessment.parsed or {}) if assessment else {}
        )
        try:
            result = provider.chat_json(messages, json_schema(DraftOutput))
            safe = _safe(parse_output(DraftOutput, result.content))
            if safe is not None:
                subject, body = safe
                generated_by, model, latency = (
                    "llm",
                    f"{provider.name}/{result.model}",
                    (result.duration_ms),
                )
        except (ProviderUnavailable, ProviderError, OutputError):
            pass  # the template draft is used and labelled as such

    lead = db.get(Lead, lead_id, with_for_update=True)
    assert lead is not None
    existing = _open_draft(db, lead.id)
    if existing is not None:
        return existing, False
    version = (
        db.scalar(select(func.max(FollowUpDraft.version)).where(FollowUpDraft.lead_id == lead.id))
        or 0
    ) + 1
    draft = FollowUpDraft(
        id=uuid.uuid4(),
        lead_id=lead.id,
        assessment_id=lead.current_assessment_id,
        version=version,
        subject=subject[:200],
        body=body,
        generated_by=generated_by,
        model=model,
        status=DraftStatus.DRAFT,
        latency_ms=latency,
    )
    try:
        with db.begin_nested():
            db.add(draft)
    except IntegrityError:
        db.expire_all()
        winner = _open_draft(db, lead_id)
        assert winner is not None
        return winner, False
    lifecycle.transition(lead, S.FOLLOW_UP_DRAFT_READY)
    audit.record(
        db,
        "follow_up.draft_created",
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={"draft_id": str(draft.id), "generated_by": generated_by, "version": version},
    )
    db.flush()
    return draft, True


def _get(db: Session, draft_id: uuid.UUID, lock: bool = True) -> tuple[FollowUpDraft, Lead]:
    draft = db.get(FollowUpDraft, draft_id, with_for_update=lock)
    if draft is None:
        raise NotFound("Draft not found")
    lead = db.get(Lead, draft.lead_id, with_for_update=lock)
    assert lead is not None
    return draft, lead


def edit(
    db: Session,
    draft_id: uuid.UUID,
    subject: str,
    body: str,
    user_id: uuid.UUID,
    correlation_id: str,
) -> FollowUpDraft:
    draft, lead = _get(db, draft_id)
    if draft.status is not DraftStatus.DRAFT:
        raise Conflict("Only a draft that has not been decided can be edited")
    before = (len(draft.subject), len(draft.body))
    draft.subject, draft.body = subject.strip(), body.strip()
    draft.edited_by, draft.edited_at = user_id, datetime.now(UTC)
    audit.record(
        db,
        "follow_up.edited",
        actor_user_id=user_id,
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={
            "draft_id": str(draft.id),
            "chars_before": before,
            "chars_after": (len(draft.subject), len(draft.body)),
        },
    )
    return draft


def approve(
    db: Session, draft_id: uuid.UUID, user_id: uuid.UUID, note: str | None, correlation_id: str
) -> FollowUpDraft:
    draft, lead = _get(db, draft_id)
    if draft.status is not DraftStatus.DRAFT:
        raise Conflict(f"The draft is already {draft.status.value}")
    draft.status = DraftStatus.APPROVED
    draft.decided_by, draft.decided_at, draft.decision_note = user_id, datetime.now(UTC), note
    lifecycle.transition(lead, S.FOLLOW_UP_APPROVED)
    outbox.enqueue(
        db,
        outbox.FOLLOW_UP_APPROVED,
        lead_id=lead.id,
        correlation_id=correlation_id,
        payload={"draft_id": str(draft.id)},
    )
    audit.record(
        db,
        "follow_up.approved",
        actor_user_id=user_id,
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={"draft_id": str(draft.id)},
    )
    return draft


def reject(
    db: Session, draft_id: uuid.UUID, user_id: uuid.UUID, note: str | None, correlation_id: str
) -> FollowUpDraft:
    draft, lead = _get(db, draft_id)
    if draft.status is not DraftStatus.DRAFT:
        raise Conflict(f"The draft is already {draft.status.value}")
    draft.status = DraftStatus.REJECTED
    draft.decided_by, draft.decided_at, draft.decision_note = user_id, datetime.now(UTC), note
    lifecycle.transition(lead, S.FOLLOW_UP_REJECTED)
    audit.record(
        db,
        "follow_up.rejected",
        actor_user_id=user_id,
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={"draft_id": str(draft.id)},
    )
    return draft


def deliver(
    db: Session, settings: Settings, draft_id: uuid.UUID, correlation_id: str
) -> tuple[FollowUpDraft, bool]:
    """Send an approved draft (to the sink by default). Idempotent: delivered once."""
    draft, lead = _get(db, draft_id)
    if draft.status is DraftStatus.DELIVERED:
        return draft, False
    if draft.status is not DraftStatus.APPROVED:
        raise AppError(
            "Only an approved follow-up can be delivered", code="not_approved", status_code=409
        )
    try:
        message_id = send(settings, lead.email, draft.subject, draft.body, f"L-{lead.reference}")
    except DeliveryError as exc:
        draft.delivery_error = str(exc)[:300]
        lifecycle.transition(lead, S.RETRY_PENDING, retry_stage=Stage.FOLLOW_UP_DELIVERY)
        audit.record(
            db,
            "follow_up.delivery_failed",
            outcome="failure",
            lead_id=lead.id,
            correlation_id=correlation_id,
            details={"draft_id": str(draft.id)},
        )
        db.commit()
        raise ServiceUnavailable(
            "The mail sink is not reachable; delivery will be retried",
            code="delivery_unavailable",
            headers={"Retry-After": "15"},
        ) from exc
    draft.status = DraftStatus.DELIVERED
    draft.delivery_channel = settings.delivery_mode
    draft.delivery_message_id = message_id
    draft.delivered_at = datetime.now(UTC)
    draft.delivery_error = None
    if lead.status is S.RETRY_PENDING:
        lifecycle.transition(lead, S.FOLLOW_UP_APPROVED)
    lifecycle.transition(lead, S.FOLLOW_UP_SENT)
    audit.record(
        db,
        "follow_up.delivered",
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={"draft_id": str(draft.id), "channel": settings.delivery_mode},
    )
    return draft, True
