"""Operator API: authentication, leads, review queues, drafts, workflow status, audit, users.

Roles: ``viewer`` reads, ``operator`` reviews and decides, ``admin`` also manages users and
deletes data.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.orm import Session

from app.api.v1 import views
from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.core.deps import (
    Principal,
    client_ip,
    correlation_id,
    get_db,
    get_principal,
    require_role,
)
from app.core.errors import AppError, NotFound
from app.db.models import (
    AuditEvent,
    Decision,
    DraftStatus,
    DuplicateReview,
    DuplicateStatus,
    FollowUpDraft,
    Lead,
    LeadStatus,
    OutboxEvent,
    OutboxStatus,
    Role,
    User,
)
from app.qualification.rubric import get_rubric
from app.schemas import (
    AuditOut,
    DraftDecision,
    DraftEdit,
    DraftOut,
    DuplicateResolve,
    DuplicateWithLeads,
    LeadDetail,
    LeadListItem,
    LoginRequest,
    MeOut,
    OverrideRequest,
    Page,
    ReasonRequest,
    RefreshRequest,
    Stats,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
    WorkflowEventOut,
)
from app.services import audit, auth, followups, pipeline

router = APIRouter()
Viewer = Annotated[Principal, Depends(require_role(Role.VIEWER))]
Operator = Annotated[Principal, Depends(require_role(Role.OPERATOR))]
Admin = Annotated[Principal, Depends(require_role(Role.ADMIN))]
Db = Annotated[Session, Depends(get_db)]
Cid = Annotated[str, Depends(correlation_id)]
Limit = Annotated[int, Query(ge=1, le=200)]
Offset = Annotated[int, Query(ge=0)]


# --- auth ------------------------------------------------------------------------------------
@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(
    body: LoginRequest,
    db: Db,
    settings: Settings = Depends(get_settings),
    ip: str = Depends(client_ip),
) -> TokenResponse:
    ratelimit.hit("login-ip", ip, settings.rate_limit_login_per_minute)
    ratelimit.hit("login-email", body.email.lower(), settings.rate_limit_login_per_minute)
    pair = auth.login(db, settings, body.email, body.password, ip)
    return TokenResponse(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.post("/auth/refresh", response_model=TokenResponse, tags=["auth"])
def refresh(
    body: RefreshRequest,
    db: Db,
    settings: Settings = Depends(get_settings),
    ip: str = Depends(client_ip),
) -> TokenResponse:
    ratelimit.hit("refresh-ip", ip, settings.rate_limit_login_per_minute * 3)
    pair = auth.refresh(db, settings, body.refresh_token, ip)
    return TokenResponse(
        access_token=pair.access_token, refresh_token=pair.refresh_token, expires_in=pair.expires_in
    )


@router.post("/auth/logout", status_code=204, tags=["auth"])
def logout(
    body: RefreshRequest,
    db: Db,
    principal: Principal = Depends(get_principal),
    ip: str = Depends(client_ip),
) -> Response:
    auth.logout(db, body.refresh_token, principal.user_id, ip)
    return Response(status_code=204)


@router.get("/me", response_model=MeOut, tags=["auth"])
def me(principal: Viewer, db: Db) -> MeOut:
    user = db.get(User, principal.user_id)
    assert user is not None
    return MeOut(id=user.id, email=user.email, full_name=user.full_name, role=user.role.value)


# --- leads -----------------------------------------------------------------------------------
@router.get("/leads", response_model=Page[LeadListItem], tags=["leads"])
def list_leads(
    _: Viewer,
    db: Db,
    status: Annotated[list[LeadStatus] | None, Query()] = None,
    decision: Decision | None = None,
    source: Annotated[str | None, Query(max_length=60)] = None,
    q: Annotated[str | None, Query(max_length=100)] = None,
    needs_attention: bool = False,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[LeadListItem]:
    query = select(Lead)
    if status:
        query = query.where(Lead.status.in_(status))
    if decision:
        query = query.where(Lead.decision == decision)
    if source:
        query = query.where(Lead.source == source)
    if q:
        like = f"%{q.strip().replace('%', '').replace('_', '')}%"
        conditions: list[ColumnElement[bool]] = [
            Lead.first_name.ilike(like),
            Lead.last_name.ilike(like),
            Lead.email.ilike(like),
            Lead.company.ilike(like),
        ]
        digits = q.strip().upper().removeprefix("L-")
        if digits.isdigit():
            conditions.append(Lead.reference == int(digits))
        query = query.where(or_(*conditions))
    if needs_attention:
        query = query.where(
            Lead.status.in_(
                [
                    LeadStatus.NEEDS_REVIEW,
                    LeadStatus.RETRY_PENDING,
                    LeadStatus.FAILED,
                    LeadStatus.FOLLOW_UP_DRAFT_READY,
                ]
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(Lead.created_at.desc()).limit(limit).offset(offset)).all()
    return Page(items=[views.list_item(r) for r in rows], total=total, limit=limit, offset=offset)


@router.get("/leads/{lead_id}", response_model=LeadDetail, tags=["leads"])
def get_lead(
    lead_id: uuid.UUID, _: Viewer, db: Db, settings: Settings = Depends(get_settings)
) -> LeadDetail:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise NotFound("Lead not found")
    return views.detail(db, settings, lead)


@router.get("/leads/{lead_id}/history", response_model=list[AuditOut], tags=["leads"])
def lead_history(lead_id: uuid.UUID, _: Viewer, db: Db) -> list[AuditOut]:
    if db.get(Lead, lead_id) is None:
        raise NotFound("Lead not found")
    return _audit(db, select(AuditEvent).where(AuditEvent.lead_id == lead_id), 200)


@router.post("/leads/{lead_id}/override", response_model=LeadDetail, tags=["leads"])
def override(
    lead_id: uuid.UUID,
    body: OverrideRequest,
    principal: Operator,
    db: Db,
    cid: Cid,
    settings: Settings = Depends(get_settings),
) -> LeadDetail:
    lead = pipeline.override(
        db, lead_id, Decision(body.decision), body.reason, principal.user_id, cid
    )
    db.flush()
    return views.detail(db, settings, lead)


@router.post("/leads/{lead_id}/requalify", status_code=202, tags=["leads"])
def requalify(
    lead_id: uuid.UUID,
    body: ReasonRequest,
    principal: Operator,
    db: Db,
    cid: Cid,
    settings: Settings = Depends(get_settings),
) -> dict[str, str]:
    ratelimit.hit(
        "requalify", str(principal.user_id), settings.rate_limit_requalify_per_hour, window_s=3600
    )
    pipeline.requalify(db, lead_id, body.reason, principal.user_id, cid)
    return {"status": "queued", "correlation_id": cid}


@router.post("/leads/{lead_id}/retry", status_code=202, tags=["leads"])
def retry(lead_id: uuid.UUID, principal: Operator, db: Db, cid: Cid) -> dict[str, str]:
    pipeline.retry(db, lead_id, principal.user_id, cid)
    return {"status": "queued", "correlation_id": cid}


@router.delete("/leads/{lead_id}", status_code=204, tags=["leads"])
def delete_lead(lead_id: uuid.UUID, principal: Admin, db: Db, cid: Cid) -> Response:
    pipeline.delete_lead(db, lead_id, principal.user_id, cid)
    return Response(status_code=204)


# --- duplicates -------------------------------------------------------------------------------
@router.get("/duplicates", response_model=Page[DuplicateWithLeads], tags=["review"])
def list_duplicates(
    _: Viewer,
    db: Db,
    status: DuplicateStatus = DuplicateStatus.OPEN,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[DuplicateWithLeads]:
    query = select(DuplicateReview).where(DuplicateReview.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(DuplicateReview.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[views.duplicate_with_leads(db, r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/duplicates/{review_id}/resolve", response_model=DuplicateWithLeads, tags=["review"])
def resolve_duplicate(
    review_id: uuid.UUID, body: DuplicateResolve, principal: Operator, db: Db, cid: Cid
) -> DuplicateWithLeads:
    review = pipeline.resolve_duplicate(
        db, review_id, DuplicateStatus(body.resolution), body.note, principal.user_id, cid
    )
    db.flush()
    return views.duplicate_with_leads(db, review)


# --- follow-up drafts -------------------------------------------------------------------------
@router.get("/follow-ups", response_model=Page[DraftOut], tags=["follow-ups"])
def list_drafts(
    _: Viewer,
    db: Db,
    status: DraftStatus = DraftStatus.DRAFT,
    limit: Limit = 25,
    offset: Offset = 0,
) -> Page[DraftOut]:
    query = select(FollowUpDraft).where(FollowUpDraft.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(FollowUpDraft.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[DraftOut.model_validate(r) for r in rows], total=total, limit=limit, offset=offset
    )


@router.patch("/follow-ups/{draft_id}", response_model=DraftOut, tags=["follow-ups"])
def edit_draft(
    draft_id: uuid.UUID, body: DraftEdit, principal: Operator, db: Db, cid: Cid
) -> DraftOut:
    return DraftOut.model_validate(
        followups.edit(db, draft_id, body.subject, body.body, principal.user_id, cid)
    )


@router.post("/follow-ups/{draft_id}/approve", response_model=DraftOut, tags=["follow-ups"])
def approve_draft(
    draft_id: uuid.UUID, body: DraftDecision, principal: Operator, db: Db, cid: Cid
) -> DraftOut:
    return DraftOut.model_validate(
        followups.approve(db, draft_id, principal.user_id, body.note, cid)
    )


@router.post("/follow-ups/{draft_id}/reject", response_model=DraftOut, tags=["follow-ups"])
def reject_draft(
    draft_id: uuid.UUID, body: DraftDecision, principal: Operator, db: Db, cid: Cid
) -> DraftOut:
    return DraftOut.model_validate(
        followups.reject(db, draft_id, principal.user_id, body.note, cid)
    )


# --- workflow status, audit, stats -------------------------------------------------------------
@router.get("/workflow-events", response_model=Page[WorkflowEventOut], tags=["operations"])
def workflow_events(
    _: Viewer, db: Db, status: OutboxStatus | None = None, limit: Limit = 50, offset: Offset = 0
) -> Page[WorkflowEventOut]:
    query = select(OutboxEvent)
    if status:
        query = query.where(OutboxEvent.status == status)
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(
        query.order_by(OutboxEvent.created_at.desc()).limit(limit).offset(offset)
    ).all()
    return Page(
        items=[WorkflowEventOut.model_validate(r) for r in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


def _audit(db: Session, query: object, limit: int) -> list[AuditOut]:
    rows = db.execute(
        query.add_columns(User.email)  # type: ignore[attr-defined]
        .outerjoin(User, User.id == AuditEvent.actor_user_id)
        .order_by(AuditEvent.ts.desc())
        .limit(limit)
    ).all()
    out = []
    for event, email in rows:
        item = AuditOut.model_validate(event)
        item.actor_email = email
        out.append(item)
    return out


@router.get("/audit-events", response_model=list[AuditOut], tags=["operations"])
def audit_events(
    _: Viewer,
    db: Db,
    action: Annotated[str | None, Query(max_length=60)] = None,
    limit: Limit = 100,
) -> list[AuditOut]:
    query = select(AuditEvent)
    if action:
        query = query.where(AuditEvent.action.startswith(action))
    return _audit(db, query, limit)


@router.get("/stats", response_model=Stats, tags=["operations"])
def stats(_: Viewer, db: Db) -> Stats:
    by_status = dict(db.execute(select(Lead.status, func.count()).group_by(Lead.status)).all())
    by_decision = dict(
        db.execute(
            select(Lead.decision, func.count())
            .where(Lead.decision.isnot(None))
            .group_by(Lead.decision)
        ).all()
    )
    return Stats(
        by_status={k.value: v for k, v in by_status.items()},
        by_decision={k.value: v for k, v in by_decision.items() if k is not None},
        open_duplicates=db.scalar(
            select(func.count()).where(DuplicateReview.status == DuplicateStatus.OPEN)
        )
        or 0,
        drafts_awaiting_review=db.scalar(
            select(func.count()).where(FollowUpDraft.status == DraftStatus.DRAFT)
        )
        or 0,
        events_pending=db.scalar(
            select(func.count()).where(
                OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.DISPATCHED])
            )
        )
        or 0,
        events_failed=db.scalar(
            select(func.count()).where(
                OutboxEvent.status.in_([OutboxStatus.FAILED, OutboxStatus.DEAD])
            )
        )
        or 0,
        leads_total=sum(by_status.values()),
    )


@router.get("/settings/qualification", tags=["operations"])
def qualification_settings(
    _: Viewer, settings: Settings = Depends(get_settings)
) -> dict[str, object]:
    """The rubric and model configuration (read-only, no secrets)."""
    rubric = get_rubric()
    return {
        "rubric": rubric.model_dump(),
        "llm_provider": settings.llm_provider,
        "llm_model": settings.llm_model,
        "crm_provider": settings.crm_provider,
        "delivery_mode": settings.delivery_mode,
    }


# --- users (admin) -----------------------------------------------------------------------------
@router.get("/users", response_model=list[UserOut], tags=["users"])
def list_users(_: Admin, db: Db) -> list[UserOut]:
    return [UserOut.model_validate(u) for u in db.scalars(select(User).order_by(User.email))]


@router.post("/users", response_model=UserOut, status_code=201, tags=["users"])
def create_user(body: UserCreate, principal: Admin, db: Db, cid: Cid) -> UserOut:
    try:
        user = auth.create_user(db, body.email, body.password, Role(body.role), body.full_name)
    except ValueError as exc:
        raise AppError(str(exc), code="weak_password", status_code=422) from exc
    audit.record(
        db,
        "user.created",
        actor_user_id=principal.user_id,
        correlation_id=cid,
        details={"user_id": str(user.id), "role": body.role},
    )
    return UserOut.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserOut, tags=["users"])
def update_user(
    user_id: uuid.UUID, body: UserUpdate, principal: Admin, db: Db, cid: Cid
) -> UserOut:
    user = db.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    if user.id == principal.user_id and (
        body.role not in (None, "admin") or body.is_active is False
    ):
        raise AppError(
            "You cannot demote or deactivate yourself", code="self_change", status_code=409
        )
    if body.role:
        user.role = Role(body.role)
    if body.is_active is not None:
        user.is_active = body.is_active
    audit.record(
        db,
        "user.updated",
        actor_user_id=principal.user_id,
        correlation_id=cid,
        details={"user_id": str(user.id), "role": body.role, "is_active": body.is_active},
    )
    return UserOut.model_validate(user)
