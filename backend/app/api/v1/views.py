"""Builders from ORM rows to response models."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.db.models import (
    Assessment,
    CrmSync,
    DuplicateReview,
    FollowUpDraft,
    Lead,
    LeadEvent,
    OutboxEvent,
)
from app.schemas import (
    AssessmentOut,
    CrmSyncOut,
    DraftOut,
    DuplicateOut,
    DuplicateWithLeads,
    EventOut,
    LeadDetail,
    LeadListItem,
    WorkflowEventOut,
)


def reference(lead: Lead) -> str:
    return f"L-{lead.reference}"


def list_item(lead: Lead) -> LeadListItem:
    return LeadListItem(
        id=lead.id,
        reference=reference(lead),
        status=lead.status.value,
        first_name=lead.first_name,
        last_name=lead.last_name,
        email=lead.email,
        company=lead.company,
        interest=lead.interest,
        source=lead.source,
        decision=lead.decision.value if lead.decision else None,
        score=lead.score,
        intent_category=lead.intent_category,
        review_reasons=list(lead.review_reasons or []),
        created_at=lead.created_at,
        last_event_at=lead.last_event_at,
    )


def detail(db: Session, settings: Settings, lead: Lead) -> LeadDetail:
    events = db.scalars(
        select(LeadEvent).where(LeadEvent.lead_id == lead.id).order_by(LeadEvent.received_at)
    ).all()
    assessments = db.scalars(
        select(Assessment)
        .where(Assessment.lead_id == lead.id)
        .order_by(Assessment.created_at.desc())
    ).all()
    duplicates = db.scalars(
        select(DuplicateReview)
        .where(
            (DuplicateReview.lead_id == lead.id) | (DuplicateReview.candidate_lead_id == lead.id)
        )
        .order_by(DuplicateReview.created_at.desc())
    ).all()
    crm = db.scalar(
        select(CrmSync).where(CrmSync.lead_id == lead.id, CrmSync.provider == settings.crm_provider)
    )
    drafts = db.scalars(
        select(FollowUpDraft)
        .where(FollowUpDraft.lead_id == lead.id)
        .order_by(FollowUpDraft.version.desc())
    ).all()
    workflow = db.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.lead_id == lead.id)
        .order_by(OutboxEvent.created_at.desc())
        .limit(20)
    ).all()
    base = list_item(lead).model_dump()
    return LeadDetail(
        **base,
        website=lead.website,
        company_domain=lead.company_domain,
        job_title=lead.job_title,
        country=lead.country,
        message=lead.message,
        consent_contact=lead.consent_contact,
        consent_marketing=lead.consent_marketing,
        retry_stage=lead.retry_stage.value if lead.retry_stage else None,
        qualified_at=lead.qualified_at,
        synced_at=lead.synced_at,
        events=[EventOut.model_validate(e) for e in events],
        assessments=[AssessmentOut.model_validate(a) for a in assessments],
        duplicates=[DuplicateOut.model_validate(d) for d in duplicates],
        crm=CrmSyncOut.model_validate(crm) if crm else None,
        drafts=[DraftOut.model_validate(d) for d in drafts],
        workflow_events=[WorkflowEventOut.model_validate(w) for w in workflow],
    )


def duplicate_with_leads(db: Session, review: DuplicateReview) -> DuplicateWithLeads:
    lead = db.get(Lead, review.lead_id)
    candidate = db.get(Lead, review.candidate_lead_id) if review.candidate_lead_id else None
    assert lead is not None
    return DuplicateWithLeads(
        **DuplicateOut.model_validate(review).model_dump(),
        lead=list_item(lead),
        candidate=list_item(candidate) if candidate else None,
    )
