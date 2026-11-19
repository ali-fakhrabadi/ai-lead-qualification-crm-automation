"""Lead lifecycle: the allowed status transitions, in one place.

Nothing moves a lead to ``unqualified`` except a person (``operator_only``), and an AI
recommendation alone never closes a lead.
"""

from __future__ import annotations

from app.core.errors import Conflict
from app.db.models import Lead, LeadStatus, Stage

S = LeadStatus

TRANSITIONS: dict[LeadStatus, frozenset[LeadStatus]] = {
    S.NEW: frozenset({S.PENDING_QUALIFICATION, S.RETRY_PENDING, S.FAILED}),
    S.PENDING_QUALIFICATION: frozenset(
        {S.QUALIFIED, S.NEEDS_REVIEW, S.RETRY_PENDING, S.FAILED, S.NEW}
    ),
    S.QUALIFIED: frozenset(
        {S.SYNCED_TO_CRM, S.RETRY_PENDING, S.FAILED, S.NEEDS_REVIEW, S.UNQUALIFIED, S.NEW}
    ),
    S.NEEDS_REVIEW: frozenset(
        {S.QUALIFIED, S.UNQUALIFIED, S.PENDING_QUALIFICATION, S.NEW, S.CLOSED}
    ),
    S.UNQUALIFIED: frozenset({S.NEEDS_REVIEW, S.CLOSED, S.NEW}),
    S.SYNCED_TO_CRM: frozenset({S.FOLLOW_UP_DRAFT_READY, S.RETRY_PENDING, S.NEW, S.CLOSED}),
    S.FOLLOW_UP_DRAFT_READY: frozenset(
        {S.FOLLOW_UP_APPROVED, S.FOLLOW_UP_REJECTED, S.NEW, S.CLOSED}
    ),
    S.FOLLOW_UP_APPROVED: frozenset({S.FOLLOW_UP_SENT, S.RETRY_PENDING, S.FAILED}),
    S.FOLLOW_UP_REJECTED: frozenset({S.FOLLOW_UP_DRAFT_READY, S.CLOSED, S.NEW}),
    S.FOLLOW_UP_SENT: frozenset({S.CLOSED, S.NEW}),
    S.CLOSED: frozenset({S.NEW}),
    S.RETRY_PENDING: frozenset(
        {
            S.PENDING_QUALIFICATION,
            S.QUALIFIED,
            S.SYNCED_TO_CRM,
            S.FOLLOW_UP_APPROVED,
            S.FAILED,
            S.NEEDS_REVIEW,
            S.NEW,
        }
    ),
    S.FAILED: frozenset({S.PENDING_QUALIFICATION, S.RETRY_PENDING, S.NEEDS_REVIEW, S.CLOSED}),
}

# Transitions only a person may trigger (through the operator API).
OPERATOR_ONLY = frozenset({S.UNQUALIFIED, S.CLOSED})

# Statuses in which a new inquiry from the same person reopens the lead.
REOPENABLE = frozenset({S.CLOSED, S.UNQUALIFIED, S.FOLLOW_UP_SENT, S.FOLLOW_UP_REJECTED, S.FAILED})


def can_transition(current: LeadStatus, target: LeadStatus) -> bool:
    return current == target or target in TRANSITIONS[current]


def transition(
    lead: Lead,
    target: LeadStatus,
    *,
    by_operator: bool = False,
    retry_stage: Stage | None = None,
) -> LeadStatus:
    """Move ``lead`` to ``target`` or raise 409. Returns the previous status."""
    previous = lead.status
    if target in OPERATOR_ONLY and not by_operator:
        raise Conflict(f"Only an operator can move a lead to {target.value}")
    if not can_transition(previous, target):
        raise Conflict(
            f"A lead in status {previous.value} cannot move to {target.value}",
            code="invalid_transition",
        )
    lead.status = target
    if target in (S.RETRY_PENDING, S.FAILED):
        lead.retry_stage = retry_stage
    elif target != previous:
        lead.retry_stage = None
    return previous
