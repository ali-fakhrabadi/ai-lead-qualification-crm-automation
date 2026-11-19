"""Fixed-window rate limits stored in PostgreSQL.

One upsert per request; no extra service. Counters live in their own short transaction so a
rejected request is still counted even when the caller's transaction rolls back.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, text

from app.core.errors import TooManyRequests
from app.db.models import RateLimitBucket
from app.db.session import get_engine

_UPSERT = text(
    """
    INSERT INTO rate_limit_buckets (key, window_start, count) VALUES (:key, :window, 1)
    ON CONFLICT (key, window_start) DO UPDATE SET count = rate_limit_buckets.count + 1
    RETURNING count
    """
)


def _window(now: datetime, seconds: int) -> datetime:
    epoch = int(now.timestamp())
    return datetime.fromtimestamp(epoch - epoch % seconds, UTC)


def hit(bucket: str, identity: str, limit: int, window_s: int = 60) -> None:
    """Count one request; raise 429 with Retry-After when the window's limit is exceeded."""
    if limit <= 0:
        return
    now = datetime.now(UTC)
    window = _window(now, window_s)
    with get_engine().begin() as conn:
        count = conn.execute(_UPSERT, {"key": f"{bucket}:{identity}"[:200], "window": window})
        value = count.scalar_one()
    if value > limit:
        retry_after = int((window + timedelta(seconds=window_s) - now).total_seconds()) + 1
        raise TooManyRequests(
            "Too many requests, please retry later",
            headers={"Retry-After": str(max(retry_after, 1))},
        )


def purge_old(older_than: timedelta = timedelta(hours=2)) -> int:
    with get_engine().begin() as conn:
        result = conn.execute(
            delete(RateLimitBucket).where(
                RateLimitBucket.window_start < datetime.now(UTC) - older_than
            )
        )
    return result.rowcount or 0
