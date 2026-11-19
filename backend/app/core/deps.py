"""Request dependencies: database session, caller identity, role checks, service token.

The caller's role is read from the users table on every request, never from token claims, so
deactivating a user or changing a role takes effect immediately.
"""

from __future__ import annotations

import hmac
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.errors import Forbidden, Unauthorized
from app.core.tokens import TokenError, decode_access_token
from app.db.models import Role, User
from app.db.session import get_sessionmaker
from app.services import audit

_bearer = HTTPBearer(auto_error=False)
_RANK = {Role.VIEWER: 0, Role.OPERATOR: 1, Role.ADMIN: 2}


@dataclass(frozen=True)
class Principal:
    user_id: uuid.UUID
    role: Role
    email: str

    def at_least(self, role: Role) -> bool:
        return _RANK[self.role] >= _RANK[role]


def get_db() -> Iterator[Session]:
    session = get_sessionmaker()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def client_ip(request: Request, settings: Settings = Depends(get_settings)) -> str:
    if settings.trust_proxy_headers:
        real_ip = request.headers.get("x-real-ip")
        if real_ip:
            return real_ip.strip()[:64]
    return request.client.host if request.client else "unknown"


def correlation_id(request: Request) -> str:
    return str(getattr(request.state, "request_id", "")) or uuid.uuid4().hex


def get_principal(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise Unauthorized("Missing bearer token")
    try:
        claims = decode_access_token(settings, credentials.credentials)
    except TokenError as exc:
        raise Unauthorized("Invalid or expired token") from exc
    user = db.scalar(select(User).where(User.id == claims.user_id, User.is_active.is_(True)))
    if user is None:
        raise Unauthorized("Account no longer active")
    request.state.user_id = str(user.id)
    return Principal(user.id, user.role, user.email)


def require_role(role: Role) -> Callable[..., Principal]:
    def _check(
        request: Request,
        principal: Principal = Depends(get_principal),
        db: Session = Depends(get_db),
        ip: str = Depends(client_ip),
    ) -> Principal:
        if not principal.at_least(role):
            audit.record(
                db,
                "authz.denied",
                outcome="denied",
                actor_user_id=principal.user_id,
                ip=ip,
                details={"method": request.method, "path": request.url.path, "needs": role},
            )
            db.commit()
            raise Forbidden(f"The {role.value} role is required")
        return principal

    return _check


def require_internal(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    settings: Settings = Depends(get_settings),
) -> None:
    """n8n calls the internal endpoints with a static service token (constant-time compare)."""
    expected = settings.internal_token.get_secret_value()
    given = credentials.credentials if credentials else ""
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise Unauthorized("Invalid service token")
