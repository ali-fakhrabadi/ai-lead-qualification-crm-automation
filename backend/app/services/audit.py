"""Audit trail. Details are structured and never contain lead message text or secrets."""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import AuditEvent


def record(
    db: Session,
    action: str,
    *,
    outcome: str = "success",
    actor_type: str = "system",
    actor_user_id: uuid.UUID | None = None,
    lead_id: uuid.UUID | None = None,
    correlation_id: str | None = None,
    ip: str | None = None,
    details: dict[str, Any] | None = None,
) -> None:
    db.add(
        AuditEvent(
            action=action,
            outcome=outcome,
            actor_type="user" if actor_user_id and actor_type == "system" else actor_type,
            actor_user_id=actor_user_id,
            lead_id=lead_id,
            correlation_id=correlation_id,
            ip=ip,
            details=details or {},
        )
    )
