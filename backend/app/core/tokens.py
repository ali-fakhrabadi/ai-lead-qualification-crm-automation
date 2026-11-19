"""Operator JWT access tokens, opaque refresh tokens and the dispatcher's webhook JWT."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from app.core.config import Settings

ALGORITHM = "HS256"


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    jti: str


class TokenError(Exception):
    """The token is missing, malformed, expired or signed for another audience."""


def create_access_token(
    settings: Settings, user_id: uuid.UUID, now: datetime | None = None
) -> tuple[str, int]:
    now = now or datetime.now(UTC)
    ttl = timedelta(minutes=settings.access_token_minutes)
    payload = {
        "sub": str(user_id),
        "typ": "access",
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "iat": int(now.timestamp()),
        "nbf": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_hex(8),
    }
    token = jwt.encode(payload, settings.jwt_secret.get_secret_value(), algorithm=ALGORITHM)
    return token, int(ttl.total_seconds())


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    try:
        payload = jwt.decode(
            token,
            settings.jwt_secret.get_secret_value(),
            algorithms=[ALGORITHM],
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": ["exp", "iat", "sub", "typ", "jti"]},
            leeway=5,
        )
        if payload.get("typ") != "access":
            raise TokenError("wrong token type")
        return AccessClaims(uuid.UUID(payload["sub"]), payload["jti"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise TokenError(str(exc)) from exc


def new_refresh_token() -> tuple[str, str]:
    """Return (token for the client, SHA-256 hex digest for the database)."""
    token = secrets.token_urlsafe(32)
    return token, hash_refresh_token(token)


def hash_refresh_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def dispatch_token(settings: Settings, event_id: uuid.UUID, payload: bytes) -> str:
    """Short-lived JWT for one webhook delivery to n8n: binds the event id and the payload hash.

    n8n's webhook node verifies the signature (JWT auth credential); the workflow's first Code
    node checks that ``payload_sha256`` matches the received payload string and that ``eid``
    matches the envelope, so a captured token cannot be replayed with another payload.
    """
    now = datetime.now(UTC)
    claims = {
        "iss": "lead-automation-dispatcher",
        "aud": "n8n",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "eid": str(event_id),
        "payload_sha256": hashlib.sha256(payload).hexdigest(),
    }
    return jwt.encode(claims, settings.dispatch_secret.get_secret_value(), algorithm=ALGORITHM)
