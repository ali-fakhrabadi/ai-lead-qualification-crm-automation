"""Signed partner webhooks.

Each source has its own shared secret. The sender signs ``"{timestamp}.{raw body}"`` with
HMAC-SHA256 and sends::

    X-Lead-Timestamp: 1763560000
    X-Lead-Signature: v1=<hex digest>

Requests older or newer than the tolerance (5 minutes by default) are refused, so a captured
request cannot be replayed later; within the window the signed ``event_id`` makes a replay a
harmless duplicate. Comparison is constant-time.
"""

from __future__ import annotations

import hashlib
import hmac
import time

from app.core.config import Settings
from app.core.errors import NotFound, Unauthorized


def sign(secret: str, timestamp: int | str, body: bytes) -> str:
    message = f"{timestamp}.".encode() + body
    return "v1=" + hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()


def verify(
    settings: Settings,
    source: str,
    timestamp: str | None,
    signature: str | None,
    body: bytes,
    now: float | None = None,
) -> None:
    secret = settings.webhook_secrets.get(source)
    if not secret:
        # Unknown sources look the same as a missing route.
        raise NotFound("Unknown webhook source")
    if not timestamp or not signature:
        raise Unauthorized("Missing webhook signature", code="invalid_signature")
    try:
        ts = int(timestamp)
    except ValueError as exc:
        raise Unauthorized("Invalid webhook timestamp", code="invalid_signature") from exc
    current = time.time() if now is None else now
    if abs(current - ts) > settings.webhook_tolerance_s:
        raise Unauthorized("Webhook timestamp outside the allowed window", code="expired_signature")
    expected = sign(secret, ts, body)
    candidates = [part.strip() for part in signature.split(",")]
    if not any(hmac.compare_digest(expected, c) for c in candidates):
        raise Unauthorized("Invalid webhook signature", code="invalid_signature")
