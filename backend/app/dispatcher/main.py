"""Outbox dispatcher: delivers pending events to n8n webhooks.

    python -m app.dispatcher.main

- Claims due events with ``FOR UPDATE SKIP LOCKED`` (several dispatchers can run safely).
- Signs each delivery with a short-lived HS256 JWT bound to the event id and body hash.
- 2xx: ``dispatched``. n8n later reports the workflow result (``completed`` or ``failed``).
- Errors: bounded exponential backoff with full jitter; after the maximum number of attempts the
  event is ``dead`` and shown to operators.
- Dispatched events that never report a result (n8n restarted mid-run) are delivered again
  after ``redeliver_after_s``. Delivery is therefore at-least-once; every workflow step is
  idempotent.
"""

from __future__ import annotations

import json
import logging
import random
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import FrameType

import httpx
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.core import ratelimit
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging
from app.core.tokens import dispatch_token
from app.db.models import OutboxEvent, OutboxStatus
from app.db.session import get_sessionmaker
from app.services import audit, outbox

log = logging.getLogger("lead.dispatcher")
REDELIVER_AFTER = timedelta(minutes=15)
_stop = False


def backoff(settings: Settings, attempts: int) -> timedelta:
    ceiling = min(settings.dispatch_backoff_cap_s, settings.dispatch_backoff_base_s * 2**attempts)
    return timedelta(seconds=random.uniform(ceiling / 2, ceiling))  # noqa: S311


def webhook_url(settings: Settings, event_type: str) -> str:
    path = (
        settings.n8n_follow_up_webhook_path
        if event_type == outbox.FOLLOW_UP_APPROVED
        else settings.n8n_lead_webhook_path
    )
    return f"{settings.n8n_base_url.rstrip('/')}/webhook/{path}"


def claim(db: Session, settings: Settings, now: datetime) -> list[OutboxEvent]:
    due = or_(
        (OutboxEvent.status.in_([OutboxStatus.PENDING, OutboxStatus.FAILED]))
        & (OutboxEvent.next_attempt_at <= now),
        (OutboxEvent.status == OutboxStatus.DISPATCHED)
        & (OutboxEvent.dispatched_at <= now - REDELIVER_AFTER),
    )
    return list(
        db.scalars(
            select(OutboxEvent)
            .where(due)
            .order_by(OutboxEvent.created_at)
            .limit(settings.dispatch_batch_size)
            .with_for_update(skip_locked=True)
        )
    )


def deliver(db: Session, settings: Settings, event: OutboxEvent, client: httpx.Client) -> bool:
    # The envelope travels as a JSON *string* inside {"payload": ...}: n8n parses the outer
    # body, and the workflow hashes the exact string it received against the signed claim.
    payload = json.dumps(outbox.envelope(event), separators=(",", ":"))
    body = json.dumps({"payload": payload}).encode()
    headers = {
        "Authorization": f"Bearer {dispatch_token(settings, event.id, payload.encode())}",
        "Content-Type": "application/json",
        "X-Event-Id": str(event.id),
        "X-Correlation-Id": event.correlation_id,
    }
    now = datetime.now(UTC)
    event.attempts += 1
    try:
        response = client.post(
            webhook_url(settings, event.event_type), content=body, headers=headers
        )
        ok = 200 <= response.status_code < 300
        error = None if ok else f"n8n answered HTTP {response.status_code}"
    except httpx.HTTPError as exc:
        ok, error = False, f"n8n unreachable ({type(exc).__name__})"
    if ok:
        event.status = OutboxStatus.DISPATCHED
        event.dispatched_at = now
        event.last_error = None
        return True
    event.last_error = error
    if event.attempts >= settings.dispatch_max_attempts:
        event.status = OutboxStatus.DEAD
        audit.record(
            db,
            "outbox.dead",
            outcome="failure",
            actor_type="dispatcher",
            lead_id=event.lead_id,
            correlation_id=event.correlation_id,
            details={"event_id": str(event.id), "attempts": event.attempts},
        )
    else:
        event.status = OutboxStatus.FAILED
        event.next_attempt_at = now + backoff(settings, event.attempts)
    log.warning(
        "delivery failed",
        extra={"event_id": str(event.id), "error": error, "attempts": event.attempts},
    )
    return False


def run_once(settings: Settings, client: httpx.Client) -> int:
    sessions = get_sessionmaker()
    handled = 0
    with sessions() as db:
        for event in claim(db, settings, datetime.now(UTC)):
            deliver(db, settings, event, client)
            db.commit()  # one event at a time: a crash never loses a delivery result
            handled += 1
    return handled


def _handle_stop(_signum: int, _frame: FrameType | None) -> None:
    global _stop
    _stop = True


HEARTBEAT = Path("/tmp/dispatcher-heartbeat")  # noqa: S108 - tmpfs inside the container


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    signal.signal(signal.SIGTERM, _handle_stop)
    signal.signal(signal.SIGINT, _handle_stop)
    log.info("dispatcher started", extra={"n8n": settings.n8n_base_url})
    last_purge = 0.0
    with httpx.Client(timeout=settings.dispatch_timeout_s) as client:
        while not _stop:
            try:
                handled = run_once(settings, client)
                HEARTBEAT.touch()  # the container health check: the loop runs and reaches the DB
                if time.monotonic() - last_purge > 3600:
                    ratelimit.purge_old()
                    last_purge = time.monotonic()
            except Exception:
                log.exception("dispatch loop error")
                handled = 0
            if not handled:
                time.sleep(settings.dispatch_poll_s)
    log.info("dispatcher stopped")


if __name__ == "__main__":
    main()
