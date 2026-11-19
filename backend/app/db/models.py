"""Database schema.

Business state (leads, assessments, drafts) and integration state (outbox events, CRM sync
records and attempts) live in separate tables. Uniqueness that protects against duplicates is
enforced by constraints, not only by application code.
"""

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Sequence,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    pass


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(cls, name=name, values_callable=lambda e: [m.value for m in e])


class Role(enum.StrEnum):
    ADMIN = "admin"
    OPERATOR = "operator"
    VIEWER = "viewer"


class LeadStatus(enum.StrEnum):
    NEW = "new"
    PENDING_QUALIFICATION = "pending_qualification"
    QUALIFIED = "qualified"
    NEEDS_REVIEW = "needs_review"
    UNQUALIFIED = "unqualified"
    SYNCED_TO_CRM = "synced_to_crm"
    FOLLOW_UP_DRAFT_READY = "follow_up_draft_ready"
    FOLLOW_UP_APPROVED = "follow_up_approved"
    FOLLOW_UP_REJECTED = "follow_up_rejected"
    FOLLOW_UP_SENT = "follow_up_sent"
    CLOSED = "closed"
    RETRY_PENDING = "retry_pending"
    FAILED = "failed"


class Stage(enum.StrEnum):
    """Where a lead in ``retry_pending``/``failed`` should resume."""

    QUALIFICATION = "qualification"
    CRM_SYNC = "crm_sync"
    FOLLOW_UP_DRAFT = "follow_up_draft"
    FOLLOW_UP_DELIVERY = "follow_up_delivery"


class EventOutcome(enum.StrEnum):
    CREATED = "created"
    UPDATED_EXISTING = "updated_existing"
    DUPLICATE_IGNORED = "duplicate_ignored"
    NEEDS_REVIEW = "needs_review"


class DuplicateKind(enum.StrEnum):
    SAME_EMAIL_CONFLICT = "same_email_conflict"
    POSSIBLE_SAME_PERSON = "possible_same_person"


class DuplicateStatus(enum.StrEnum):
    OPEN = "open"
    KEPT_SEPARATE = "kept_separate"
    SAME_PERSON = "same_person"
    UPDATE_APPLIED = "update_applied"
    DISMISSED = "dismissed"


class AssessmentStatus(enum.StrEnum):
    VALID = "valid"
    INVALID_OUTPUT = "invalid_output"
    PROVIDER_ERROR = "provider_error"


class Decision(enum.StrEnum):
    QUALIFIED = "qualified"
    NEEDS_REVIEW = "needs_review"
    UNQUALIFIED = "unqualified"  # only ever set by a person


class SyncStatus(enum.StrEnum):
    PENDING = "pending"
    SYNCED = "synced"
    RETRY_PENDING = "retry_pending"
    FAILED_PERMANENT = "failed_permanent"


class DraftStatus(enum.StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    REJECTED = "rejected"
    DELIVERED = "delivered"
    DELIVERY_FAILED = "delivery_failed"


class OutboxStatus(enum.StrEnum):
    PENDING = "pending"
    DISPATCHED = "dispatched"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(_enum(Role, "user_role"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RefreshToken(Base):
    """Opaque refresh tokens, stored as SHA-256 hashes. Rotated on every use."""

    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    family_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


lead_reference_seq = Sequence("lead_reference_seq", start=1001)


class Lead(Base):
    __tablename__ = "leads"
    __table_args__ = (
        Index("ix_leads_status_created", "status", "created_at"),
        Index("ix_leads_name_domain", "name_key", "company_domain"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    reference: Mapped[int] = mapped_column(
        BigInteger, lead_reference_seq, server_default=lead_reference_seq.next_value(), unique=True
    )
    status: Mapped[LeadStatus] = mapped_column(
        _enum(LeadStatus, "lead_status"), default=LeadStatus.NEW
    )
    retry_stage: Mapped[Stage | None] = mapped_column(_enum(Stage, "lead_stage"))
    first_name: Mapped[str] = mapped_column(String(100))
    last_name: Mapped[str] = mapped_column(String(100))
    # Lower-cased full name used only to find possible duplicates (never to merge).
    name_key: Mapped[str] = mapped_column(String(210))
    email: Mapped[str] = mapped_column(String(320))
    email_normalized: Mapped[str] = mapped_column(String(320), unique=True)
    company: Mapped[str | None] = mapped_column(String(200))
    company_domain: Mapped[str | None] = mapped_column(String(253))
    website: Mapped[str | None] = mapped_column(String(253))
    job_title: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    interest: Mapped[str] = mapped_column(String(60))
    message: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(60))
    consent_contact: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_marketing: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_text_version: Mapped[str | None] = mapped_column(String(40))
    # Denormalised from the current assessment for filtering and the pipeline view.
    decision: Mapped[Decision | None] = mapped_column(_enum(Decision, "lead_decision"))
    score: Mapped[int | None] = mapped_column(Integer)
    intent_category: Mapped[str | None] = mapped_column(String(40))
    review_reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    current_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    qualified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # The database deletes dependent rows (ON DELETE CASCADE); the ORM must not null them.
    events: Mapped[list[LeadEvent]] = relationship(
        back_populates="lead", order_by="LeadEvent.received_at", passive_deletes="all"
    )


class LeadEvent(Base):
    """Every accepted submission (form or webhook), including ignored duplicates."""

    __tablename__ = "lead_events"
    __table_args__ = (UniqueConstraint("source", "idempotency_key", name="uq_event_idempotency"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    source: Mapped[str] = mapped_column(String(60))
    channel: Mapped[str] = mapped_column(String(20))  # form | webhook
    idempotency_key: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    # Normalised submitted fields (the original values the lead sent), kept separately from the
    # lead record and from any AI output.
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    outcome: Mapped[EventOutcome] = mapped_column(_enum(EventOutcome, "event_outcome"))
    correlation_id: Mapped[str] = mapped_column(String(64))
    occurred_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    replay_count: Mapped[int] = mapped_column(Integer, default=0)

    lead: Mapped[Lead] = relationship(back_populates="events")


class DuplicateReview(Base):
    __tablename__ = "duplicate_reviews"
    __table_args__ = (
        Index(
            "uq_duplicate_open_pair",
            "lead_id",
            "candidate_lead_id",
            "kind",
            unique=True,
            postgresql_where=text("status = 'open'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    kind: Mapped[DuplicateKind] = mapped_column(_enum(DuplicateKind, "duplicate_kind"))
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    # The other lead for possible-same-person matches; NULL for same-email conflicts, where the
    # proposed values are in ``proposed``.
    candidate_lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE")
    )
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("lead_events.id", ondelete="SET NULL")
    )
    reasons: Mapped[list[str]] = mapped_column(JSONB, default=list)
    proposed: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    status: Mapped[DuplicateStatus] = mapped_column(
        _enum(DuplicateStatus, "duplicate_status"), default=DuplicateStatus.OPEN
    )
    resolved_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Assessment(Base):
    """One AI qualification attempt and the rules applied to it.

    ``parsed`` holds the validated model output; ``rules`` the deterministic result. The
    original submission stays in ``lead_events`` and is never overwritten by AI output.
    """

    __tablename__ = "assessments"
    __table_args__ = (
        Index(
            "uq_assessment_valid_input",
            "lead_id",
            "input_hash",
            unique=True,
            postgresql_where=text("status = 'valid' AND superseded = false"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    input_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(20))
    model: Mapped[str] = mapped_column(String(100))
    status: Mapped[AssessmentStatus] = mapped_column(_enum(AssessmentStatus, "assessment_status"))
    error: Mapped[str | None] = mapped_column(String(300))
    raw_output: Mapped[str | None] = mapped_column(Text)
    parsed: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rules: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    decision: Mapped[Decision | None] = mapped_column(_enum(Decision, "lead_decision"))
    score: Mapped[int | None] = mapped_column(Integer)
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    prompt_tokens: Mapped[int | None] = mapped_column(Integer)
    completion_tokens: Mapped[int | None] = mapped_column(Integer)
    superseded: Mapped[bool] = mapped_column(Boolean, default=False)
    override_decision: Mapped[Decision | None] = mapped_column(_enum(Decision, "lead_decision"))
    override_reason: Mapped[str | None] = mapped_column(String(500))
    overridden_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    overridden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class CrmSync(Base):
    __tablename__ = "crm_syncs"
    __table_args__ = (UniqueConstraint("lead_id", "provider", name="uq_crm_sync_lead_provider"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    provider: Mapped[str] = mapped_column(String(20))
    status: Mapped[SyncStatus] = mapped_column(
        _enum(SyncStatus, "sync_status"), default=SyncStatus.PENDING
    )
    remote_contact_id: Mapped[str | None] = mapped_column(String(100))
    remote_company_id: Mapped[str | None] = mapped_column(String(100))
    noted_assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    payload_hash: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error_code: Mapped[str | None] = mapped_column(String(40))
    last_error: Mapped[str | None] = mapped_column(String(300))
    synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class CrmAttempt(Base):
    """Every HTTP call to the CRM: kept to measure retries and recovery honestly."""

    __tablename__ = "crm_attempts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    sync_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("crm_syncs.id", ondelete="CASCADE"), index=True
    )
    operation: Mapped[str] = mapped_column(String(40))
    outcome: Mapped[str] = mapped_column(String(20))  # success | transient | permanent
    http_status: Mapped[int | None] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(String(300))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class FollowUpDraft(Base):
    __tablename__ = "follow_up_drafts"
    __table_args__ = (
        Index(
            "uq_one_open_draft_per_lead",
            "lead_id",
            unique=True,
            postgresql_where=text("status IN ('draft', 'approved')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    lead_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    assessment_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    version: Mapped[int] = mapped_column(Integer, default=1)
    subject: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    generated_by: Mapped[str] = mapped_column(String(20))  # llm | template
    model: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[DraftStatus] = mapped_column(
        _enum(DraftStatus, "draft_status"), default=DraftStatus.DRAFT
    )
    edited_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    edited_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
    delivery_channel: Mapped[str | None] = mapped_column(String(20))
    delivery_message_id: Mapped[str | None] = mapped_column(String(200))
    delivery_error: Mapped[str | None] = mapped_column(String(300))
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    latency_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class OutboxEvent(Base):
    """Transactional outbox: written in the same transaction as the business change."""

    __tablename__ = "outbox_events"
    __table_args__ = (
        Index(
            "ix_outbox_due",
            "next_attempt_at",
            postgresql_where=text("status IN ('pending', 'failed')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(40))
    lead_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("leads.id", ondelete="CASCADE"), index=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    status: Mapped[OutboxStatus] = mapped_column(
        _enum(OutboxStatus, "outbox_status"), default=OutboxStatus.PENDING
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_error: Mapped[str | None] = mapped_column(String(300))
    correlation_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    workflow_execution_id: Mapped[str | None] = mapped_column(String(40))
    workflow_status: Mapped[str | None] = mapped_column(String(20))
    workflow_duration_ms: Mapped[int | None] = mapped_column(Integer)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (Index("ix_audit_lead_ts", "lead_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    actor_type: Mapped[str] = mapped_column(String(20))  # user | system | n8n | dispatcher
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(60))
    outcome: Mapped[str] = mapped_column(String(20), default="success")
    lead_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("leads.id", ondelete="SET NULL"))
    correlation_id: Mapped[str | None] = mapped_column(String(64))
    ip: Mapped[str | None] = mapped_column(String(64))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)


class FaultRule(Base):
    """Evaluation/test only (LEAD_FAULT_INJECTION_ENABLED): make the model provider fail for
    leads whose email matches, so recovery paths can be measured. Ignored in production."""

    __tablename__ = "fault_rules"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target: Mapped[str] = mapped_column(String(20))  # llm
    mode: Mapped[str] = mapped_column(String(20))  # malformed | unavailable
    email_normalized: Mapped[str] = mapped_column(String(320), index=True)
    remaining: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateLimitBucket(Base):
    __tablename__ = "rate_limit_buckets"

    key: Mapped[str] = mapped_column(String(200), primary_key=True)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    count: Mapped[int] = mapped_column(Integer, default=0)
