"""Which step a lead needs next, and the operator actions that move it.

n8n asks ``next_step`` at the start of every run, so a replayed or resumed event continues
where the lead stands instead of repeating finished work.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, Conflict, NotFound
from app.db.models import (
    Assessment,
    Decision,
    DraftStatus,
    DuplicateKind,
    DuplicateReview,
    DuplicateStatus,
    FollowUpDraft,
    Lead,
    LeadStatus,
    Stage,
)
from app.services import audit, lifecycle, outbox

S = LeadStatus
REVIEW_REASON = {
    DuplicateKind.POSSIBLE_SAME_PERSON: "possible_duplicate",
    DuplicateKind.SAME_EMAIL_CONFLICT: "same_email_conflict",
}


def next_step(lead: Lead, settings: Settings) -> str:
    if lead.status in (S.NEW, S.PENDING_QUALIFICATION):
        return "qualify"
    if lead.status in (S.RETRY_PENDING, S.FAILED):
        return (
            {
                Stage.QUALIFICATION: "qualify",
                Stage.CRM_SYNC: "crm_sync",
                Stage.FOLLOW_UP_DRAFT: "follow_up_draft",
            }.get(lead.retry_stage, "stop")
            if lead.retry_stage
            else "stop"
        )
    if lead.status is S.QUALIFIED:
        return "crm_sync" if settings.crm_provider != "none" else "follow_up_draft"
    if lead.status is S.SYNCED_TO_CRM:
        return "follow_up_draft"
    return "stop"


def _lead(db: Session, lead_id: uuid.UUID) -> Lead:
    lead = db.get(Lead, lead_id, with_for_update=True)
    if lead is None:
        raise NotFound("Lead not found")
    return lead


def override(
    db: Session,
    lead_id: uuid.UUID,
    decision: Decision,
    reason: str,
    user_id: uuid.UUID,
    correlation_id: str,
) -> Lead:
    lead = _lead(db, lead_id)
    previous = lead.decision
    if lead.current_assessment_id:
        assessment = db.get(Assessment, lead.current_assessment_id)
        if assessment is not None:
            assessment.override_decision = decision
            assessment.override_reason = reason
            assessment.overridden_by = user_id
            assessment.overridden_at = datetime.now(UTC)
    target = {
        Decision.QUALIFIED: S.QUALIFIED,
        Decision.NEEDS_REVIEW: S.NEEDS_REVIEW,
        Decision.UNQUALIFIED: S.UNQUALIFIED,
    }[decision]
    if lead.status is not target:
        lifecycle.transition(lead, target, by_operator=True)
    lead.decision = decision
    if decision is Decision.QUALIFIED:
        lead.qualified_at = lead.qualified_at or datetime.now(UTC)
        outbox.enqueue(
            db,
            outbox.LEAD_RESUME,
            lead_id=lead.id,
            correlation_id=correlation_id,
            payload={"reason": "operator_override"},
        )
    audit.record(
        db,
        "qualification.overridden",
        actor_user_id=user_id,
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={
            "from": previous.value if previous else None,
            "to": decision.value,
            "reason": reason,
        },
    )
    return lead


def requalify(
    db: Session, lead_id: uuid.UUID, reason: str, user_id: uuid.UUID, correlation_id: str
) -> Lead:
    lead = _lead(db, lead_id)
    if lead.status in (S.FOLLOW_UP_APPROVED, S.PENDING_QUALIFICATION):
        raise Conflict(f"A lead in status {lead.status.value} cannot be re-qualified now")
    outbox.enqueue(
        db,
        outbox.LEAD_RECEIVED,
        lead_id=lead.id,
        correlation_id=correlation_id,
        payload={"reason": "operator_requalify", "force": True},
    )
    audit.record(
        db,
        "qualification.rerun_requested",
        actor_user_id=user_id,
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={"reason": reason},
    )
    return lead


def retry(db: Session, lead_id: uuid.UUID, user_id: uuid.UUID, correlation_id: str) -> Lead:
    """Resume a lead that is waiting for a retry or failed permanently (after a fix)."""
    lead = _lead(db, lead_id)
    if lead.status not in (S.RETRY_PENDING, S.FAILED):
        raise Conflict("Only leads waiting for a retry or failed can be retried")
    if lead.status is S.FAILED:
        lifecycle.transition(lead, S.RETRY_PENDING, retry_stage=lead.retry_stage)
    if lead.retry_stage is Stage.FOLLOW_UP_DELIVERY:
        draft = db.scalar(
            select(FollowUpDraft).where(
                FollowUpDraft.lead_id == lead.id, FollowUpDraft.status == DraftStatus.APPROVED
            )
        )
        if draft is None:
            raise AppError(
                "No approved follow-up to deliver", code="nothing_to_retry", status_code=409
            )
        outbox.enqueue(
            db,
            outbox.FOLLOW_UP_APPROVED,
            lead_id=lead.id,
            correlation_id=correlation_id,
            payload={"draft_id": str(draft.id)},
        )
    else:
        outbox.enqueue(
            db,
            outbox.LEAD_RESUME,
            lead_id=lead.id,
            correlation_id=correlation_id,
            payload={"reason": "operator_retry"},
        )
    audit.record(
        db,
        "pipeline.retry_requested",
        actor_user_id=user_id,
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={"stage": lead.retry_stage.value if lead.retry_stage else None},
    )
    return lead


def resolve_duplicate(
    db: Session,
    review_id: uuid.UUID,
    resolution: DuplicateStatus,
    note: str | None,
    user_id: uuid.UUID,
    correlation_id: str,
) -> DuplicateReview:
    review = db.get(DuplicateReview, review_id, with_for_update=True)
    if review is None:
        raise NotFound("Review item not found")
    if review.status is not DuplicateStatus.OPEN:
        raise Conflict("This review item is already resolved")
    if resolution is DuplicateStatus.UPDATE_APPLIED and (
        review.kind is not DuplicateKind.SAME_EMAIL_CONFLICT
    ):
        raise AppError(
            "Only same-email conflicts can apply proposed values",
            code="invalid_resolution",
            status_code=422,
        )
    lead = _lead(db, review.lead_id)
    if resolution is DuplicateStatus.UPDATE_APPLIED:
        for field, value in review.proposed.items():
            if field in ("first_name", "last_name", "company", "job_title", "website", "country"):
                setattr(lead, field, value)
    review.status = resolution
    review.note = note
    review.resolved_by = user_id
    review.resolved_at = datetime.now(UTC)
    still_open = {
        r.kind
        for r in db.scalars(
            select(DuplicateReview).where(
                DuplicateReview.lead_id == lead.id,
                DuplicateReview.status == DuplicateStatus.OPEN,
                DuplicateReview.id != review.id,
            )
        )
    }
    reason = REVIEW_REASON[review.kind]
    if review.kind not in still_open:
        lead.review_reasons = [r for r in lead.review_reasons if r != reason]
    audit.record(
        db,
        "duplicate.resolved",
        actor_user_id=user_id,
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={
            "review_id": str(review.id),
            "kind": review.kind.value,
            "resolution": resolution.value,
            "candidate": str(review.candidate_lead_id) if review.candidate_lead_id else None,
        },
    )
    return review


def delete_lead(db: Session, lead_id: uuid.UUID, user_id: uuid.UUID, correlation_id: str) -> None:
    """Erase the lead and everything derived from it here (events, assessments, drafts, sync
    records). The audit trail keeps only the reference. Records already copied to the CRM
    must be deleted there separately (docs/security.md)."""
    lead = _lead(db, lead_id)
    reference = lead.reference
    db.delete(lead)
    audit.record(
        db,
        "lead.deleted",
        actor_user_id=user_id,
        correlation_id=correlation_id,
        details={"reference": f"L-{reference}"},
    )
