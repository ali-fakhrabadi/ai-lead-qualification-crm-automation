"""Operator login, refresh-token rotation (with reuse detection), logout and user management."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, Unauthorized
from app.core.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.tokens import create_access_token, hash_refresh_token, new_refresh_token
from app.db.models import RefreshToken, Role, User
from app.services import audit

_GENERIC = "Invalid email or password"


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    refresh_token: str
    expires_in: int


def _issue(
    db: Session, settings: Settings, user_id: uuid.UUID, family_id: uuid.UUID | None = None
) -> TokenPair:
    access, expires_in = create_access_token(settings, user_id)
    refresh, digest = new_refresh_token()
    db.add(
        RefreshToken(
            family_id=family_id or uuid.uuid4(),
            user_id=user_id,
            token_hash=digest,
            expires_at=datetime.now(UTC) + timedelta(days=settings.refresh_token_days),
        )
    )
    return TokenPair(access, refresh, expires_in)


def login(db: Session, settings: Settings, email: str, password: str, ip: str) -> TokenPair:
    email = email.strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    ok = verify_password(password, user.password_hash if user else None)
    if user is None or not ok or not user.is_active:
        audit.record(
            db,
            "auth.login",
            outcome="failure",
            actor_user_id=user.id if user else None,
            ip=ip,
            details={"reason": "bad_credentials"},
        )
        db.commit()
        raise Unauthorized(_GENERIC)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    user.last_login_at = datetime.now(UTC)
    pair = _issue(db, settings, user.id)
    audit.record(db, "auth.login", actor_user_id=user.id, ip=ip)
    return pair


def refresh(db: Session, settings: Settings, token: str, ip: str) -> TokenPair:
    now = datetime.now(UTC)
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))
    )
    if row is None:
        raise Unauthorized("Invalid refresh token")
    if row.revoked_at is not None:
        # Reuse of a rotated token: someone has a copy. Revoke the whole family.
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=now)
        )
        audit.record(db, "auth.refresh_reuse", outcome="denied", actor_user_id=row.user_id, ip=ip)
        db.commit()
        raise Unauthorized("Refresh token reuse detected; please sign in again")
    user = db.get(User, row.user_id)
    if row.expires_at <= now or user is None or not user.is_active:
        raise Unauthorized("Refresh token expired")
    row.revoked_at = now
    return _issue(db, settings, row.user_id, family_id=row.family_id)


def logout(db: Session, token: str, user_id: uuid.UUID, ip: str) -> None:
    row = db.scalar(
        select(RefreshToken).where(RefreshToken.token_hash == hash_refresh_token(token))
    )
    if row is not None and row.user_id == user_id:
        db.execute(
            update(RefreshToken)
            .where(RefreshToken.family_id == row.family_id, RefreshToken.revoked_at.is_(None))
            .values(revoked_at=datetime.now(UTC))
        )
    audit.record(db, "auth.logout", actor_user_id=user_id, ip=ip)


def create_user(db: Session, email: str, password: str, role: Role, full_name: str = "") -> User:
    email = email.strip().lower()
    if db.scalar(select(User.id).where(User.email == email)):
        raise Conflict("A user with this email already exists")
    validate_password_strength(password)
    user = User(
        email=email,
        full_name=full_name.strip()[:200],
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def set_password(db: Session, email: str, password: str) -> User:
    """Operator reset: new password and every refresh token of the user revoked."""
    user = db.scalar(select(User).where(User.email == email.strip().lower()))
    if user is None:
        raise NotFound("Unknown user")
    validate_password_strength(password)
    user.password_hash = hash_password(password)
    db.execute(
        update(RefreshToken)
        .where(RefreshToken.user_id == user.id, RefreshToken.revoked_at.is_(None))
        .values(revoked_at=datetime.now(UTC))
    )
    return user
