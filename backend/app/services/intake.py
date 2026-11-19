"""Lead intake: validation, normalization, idempotency, duplicate handling and the outbox event.

Everything for one submission happens in one transaction: the event row, the lead change, any
review item and the outbox event are committed together or not at all.

Duplicate rules (documented in docs/lead-lifecycle.md):

- Same ``(source, idempotency key)``: a replay. The original result is returned; nothing is
  processed again. The same key with a different payload is refused (409).
- Same normalized email: the same person. Identical content is recorded and ignored; a new
  inquiry updates the lead (empty fields are filled, the new message is processed). Fields that
  conflict with stored values are never overwritten: a review item holds the proposed values.
- Same name and company domain (or the same mailbox with a different ``+tag``) but a different
  email: a *possible* duplicate. Both leads stay separate; a person decides.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict
from app.db.models import (
    DuplicateKind,
    DuplicateReview,
    DuplicateStatus,
    EventOutcome,
    Lead,
    LeadEvent,
    LeadStatus,
)
from app.qualification.rubric import Rubric
from app.schemas import LeadSubmission
from app.services import audit, lifecycle, outbox
from app.services.normalize import (
    InvalidField,
    clean_text,
    company_domain,
    email_base,
    name_key,
    normalize_email,
    normalize_name,
    normalize_website,
    payload_hash,
)

CONSENT_TEXT_VERSION = "2026-11"
CONSENT_TEXT = (
    "I agree that Northwind Automation Studio may store these details and contact me about "
    "this inquiry. (Demo form: please use made-up data.)"
)
OPTIONAL_FIELDS = ("company", "website", "job_title", "country")
CONFLICT_FIELDS = ("first_name", "last_name", "company", "job_title", "website", "country")


@dataclass
class IntakeResult:
    lead: Lead
    event: LeadEvent
    outcome: EventOutcome
    replayed: bool


def normalize_submission(
    submission: LeadSubmission, settings: Settings, rubric: Rubric
) -> dict[str, Any]:
    """Return the normalized payload or raise InvalidField (reported as a 422)."""
    message = clean_text(submission.message, multiline=True)
    if len(message) < 20:
        raise InvalidField("message", "must be at least 20 characters")
    interest = submission.interest.strip()
    if interest not in [o["value"] for o in rubric.interest_options()]:
        raise InvalidField("interest", "is not one of the offered options")
    if not submission.consent_contact:
        raise InvalidField("consent_contact", "consent to be contacted is required")
    data: dict[str, Any] = {
        "first_name": normalize_name(submission.first_name, "first_name"),
        "last_name": normalize_name(submission.last_name, "last_name"),
        "email": normalize_email(submission.email),
        "company": clean_text(submission.company) or None,
        "website": normalize_website(submission.website),
        "job_title": clean_text(submission.job_title) or None,
        "country": clean_text(submission.country) or None,
        "interest": interest,
        "message": message,
        "consent_contact": True,
        "consent_marketing": bool(submission.consent_marketing),
    }
    for name in settings_required(settings):
        if not data.get(name):
            raise InvalidField(name, "is required")
    return data


def settings_required(settings: Settings) -> list[str]:
    return [f for f in settings.required_optional_fields if f in OPTIONAL_FIELDS]


def _differs(a: str | None, b: str | None) -> bool:
    return bool(a and b and a.strip().casefold() != b.strip().casefold())


def _find_event(db: Session, source: str, key: str) -> LeadEvent | None:
    return db.scalar(
        select(LeadEvent).where(LeadEvent.source == source, LeadEvent.idempotency_key == key)
    )


def _replay(db: Session, event: LeadEvent, p_hash: str, correlation_id: str) -> IntakeResult:
    if event.payload_hash != p_hash:
        raise Conflict(
            "This idempotency key was already used with different content",
            code="idempotency_conflict",
        )
    event.replay_count += 1
    audit.record(
        db,
        "lead.event_replayed",
        actor_type="system",
        lead_id=event.lead_id,
        correlation_id=correlation_id,
        details={"source": event.source, "original_event": str(event.id)},
    )
    return IntakeResult(event.lead, event, event.outcome, replayed=True)


def _new_lead(data: dict[str, Any], source: str) -> Lead:
    return Lead(
        id=uuid.uuid4(),
        status=LeadStatus.NEW,
        first_name=data["first_name"],
        last_name=data["last_name"],
        name_key=name_key(data["first_name"], data["last_name"]),
        email=data["email"],
        email_normalized=data["email"],
        company=data["company"],
        company_domain=company_domain(data["email"], data["website"]),
        website=data["website"],
        job_title=data["job_title"],
        country=data["country"],
        interest=data["interest"],
        message=data["message"],
        source=source,
        consent_contact=data["consent_contact"],
        consent_marketing=data["consent_marketing"],
        consent_text_version=CONSENT_TEXT_VERSION,
        review_reasons=[],
    )


def _possible_duplicates(db: Session, lead: Lead) -> list[tuple[Lead, list[str]]]:
    found: dict[uuid.UUID, tuple[Lead, list[str]]] = {}
    if lead.company_domain:
        rows = db.scalars(
            select(Lead).where(
                Lead.id != lead.id,
                Lead.name_key == lead.name_key,
                Lead.company_domain == lead.company_domain,
            )
        )
        for other in rows:
            found[other.id] = (other, ["same_name", "same_company_domain"])
    base = email_base(lead.email_normalized)
    local, domain = base.rsplit("@", 1)
    rows = db.scalars(
        select(Lead).where(
            Lead.id != lead.id,
            Lead.email_normalized.like(f"{local}+%@{domain}") | (Lead.email_normalized == base),
        )
    )
    for other in rows:
        entry = found.setdefault(other.id, (other, []))
        entry[1].append("same_mailbox_different_plus_tag")
    return list(found.values())


def _add_reason(lead: Lead, reason: str) -> None:
    if reason not in lead.review_reasons:
        lead.review_reasons = [*lead.review_reasons, reason]


def submit(
    db: Session,
    data: dict[str, Any],
    *,
    source: str,
    channel: str,
    idempotency_key: str,
    correlation_id: str,
    occurred_at: datetime | None = None,
) -> IntakeResult:
    p_hash = payload_hash(data)
    existing_event = _find_event(db, source, idempotency_key)
    if existing_event is not None:
        return _replay(db, existing_event, p_hash, correlation_id)

    lead = db.scalar(select(Lead).where(Lead.email_normalized == data["email"]).with_for_update())
    created = False
    if lead is None:
        candidate = _new_lead(data, source)
        try:
            with db.begin_nested():
                db.add(candidate)
            lead, created = candidate, True
        except IntegrityError:
            # A concurrent submission created the same person first.
            lead = db.scalar(
                select(Lead).where(Lead.email_normalized == data["email"]).with_for_update()
            )
            if lead is None:  # pragma: no cover - the conflict was not on email
                raise

    now = datetime.now(UTC)
    review: DuplicateReview | None = None
    if created:
        outcome = EventOutcome.CREATED
    else:
        same_message = clean_text(lead.message).casefold() == clean_text(data["message"]).casefold()
        conflicts = {f: data[f] for f in CONFLICT_FIELDS if _differs(getattr(lead, f), data.get(f))}
        filled = [f for f in OPTIONAL_FIELDS if not getattr(lead, f) and data.get(f)]
        for f in filled:
            setattr(lead, f, data[f])
        if "website" in filled:
            lead.company_domain = company_domain(lead.email_normalized, lead.website)
        if same_message and not conflicts and not filled:
            outcome = EventOutcome.DUPLICATE_IGNORED
        else:
            outcome = EventOutcome.UPDATED_EXISTING
            if conflicts:
                outcome = EventOutcome.NEEDS_REVIEW
                review = DuplicateReview(
                    kind=DuplicateKind.SAME_EMAIL_CONFLICT,
                    lead_id=lead.id,
                    reasons=[f"different_{f}" for f in conflicts],
                    proposed=conflicts,
                )
                _add_reason(lead, "same_email_conflict")
            if not same_message:
                lead.message = data["message"]
                lead.interest = data["interest"]
                if lead.status in lifecycle.REOPENABLE:
                    lifecycle.transition(lead, LeadStatus.NEW)
                elif lead.status not in (LeadStatus.NEW, LeadStatus.PENDING_QUALIFICATION):
                    # Mid-pipeline (e.g. a draft is waiting): do not restart behind the
                    # operator's back; flag the new inquiry instead.
                    _add_reason(lead, "new_inquiry_during_processing")

    event = LeadEvent(
        id=uuid.uuid4(),
        lead_id=lead.id,
        source=source,
        channel=channel,
        idempotency_key=idempotency_key,
        payload_hash=p_hash,
        payload=data,
        outcome=outcome,
        correlation_id=correlation_id,
        occurred_at=occurred_at,
        received_at=now,
    )
    try:
        with db.begin_nested():
            db.add(event)
    except IntegrityError:
        # The same idempotency key arrived concurrently: answer like a replay.
        db.expire_all()
        existing_event = _find_event(db, source, idempotency_key)
        if existing_event is None:  # pragma: no cover
            raise
        return _replay(db, existing_event, p_hash, correlation_id)

    lead.last_event_at = now
    if review is not None:
        review.event_id = event.id
        db.add(review)
    if created:
        for other, reasons in _possible_duplicates(db, lead):
            db.add(
                DuplicateReview(
                    kind=DuplicateKind.POSSIBLE_SAME_PERSON,
                    lead_id=lead.id,
                    candidate_lead_id=other.id,
                    event_id=event.id,
                    reasons=reasons,
                    status=DuplicateStatus.OPEN,
                )
            )
            _add_reason(lead, "possible_duplicate")
    starts_pipeline = created or (
        outcome in (EventOutcome.UPDATED_EXISTING, EventOutcome.NEEDS_REVIEW)
        and data["message"] == lead.message
        and lead.status in (LeadStatus.NEW, LeadStatus.PENDING_QUALIFICATION)
    )
    if starts_pipeline:
        outbox.enqueue(
            db,
            outbox.LEAD_RECEIVED,
            lead_id=lead.id,
            correlation_id=correlation_id,
            payload={"reason": "new_lead" if created else "new_inquiry"},
        )
    audit.record(
        db,
        f"lead.{outcome.value}",
        actor_type="system",
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={"source": source, "channel": channel, "event_id": str(event.id)},
    )
    db.flush()
    return IntakeResult(lead, event, outcome, replayed=False)
