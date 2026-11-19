"""Transactional outbox: events are rows written with the business change they describe.

Event types:

- ``lead.received``: run the pipeline (qualification, then CRM sync and follow-up draft if the
  rules qualify the lead).
- ``lead.resume``: continue a lead from its current stage (after an operator override, a retry
  request or a replay).
- ``follow_up.approved``: deliver an approved follow-up (to the local sink by default).

Delivery is at-least-once; every consumer step is idempotent (see
docs/idempotency-and-retries.md).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import OutboxEvent

LEAD_RECEIVED = "lead.received"
LEAD_RESUME = "lead.resume"
FOLLOW_UP_APPROVED = "follow_up.approved"


def enqueue(
    db: Session,
    event_type: str,
    *,
    lead_id: uuid.UUID,
    correlation_id: str,
    payload: dict[str, Any] | None = None,
) -> OutboxEvent:
    event = OutboxEvent(
        id=uuid.uuid4(),
        event_type=event_type,
        lead_id=lead_id,
        correlation_id=correlation_id,
        payload=payload or {},
    )
    db.add(event)
    return event


def envelope(event: OutboxEvent) -> dict[str, Any]:
    """The JSON body sent to n8n. Identifiers only: n8n fetches what it needs from the API."""
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "lead_id": str(event.lead_id) if event.lead_id else None,
        "correlation_id": event.correlation_id,
        "attempt": event.attempts + 1,
        "created_at": event.created_at.isoformat() if event.created_at else None,
        "data": event.payload,
    }
