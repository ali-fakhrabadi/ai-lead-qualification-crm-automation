"""Operator commands.

    python -m app.cli create-user --email ops@example.com --role admin
    python -m app.cli set-password --email ops@example.com   # also signs the user out
    python -m app.cli purge --dry-run                         # retention: closed leads

Passwords are read from LEAD_CLI_PASSWORD or prompted for; never accepted as arguments.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select

from app.core.config import get_settings
from app.db.models import AuditEvent, Lead, LeadStatus, Role
from app.db.session import session_scope
from app.services import auth


def _password() -> str:
    password = os.environ.get("LEAD_CLI_PASSWORD") or getpass.getpass("Password: ")
    if not password:
        sys.exit("A password is required")
    return password


def create_user(args: argparse.Namespace) -> None:
    with session_scope() as db:
        auth.create_user(db, args.email, _password(), Role(args.role), args.full_name or "")
    print(f"{args.email} created with role {args.role}")


def set_password(args: argparse.Namespace) -> None:
    password = _password()
    with session_scope() as db:
        auth.set_password(db, args.email, password)
    print(f"password of {args.email} changed; existing sessions revoked")


def purge(args: argparse.Namespace) -> None:
    """Delete closed or unqualified leads not touched for LEAD_RETENTION_DAYS."""
    cutoff = datetime.now(UTC) - timedelta(days=get_settings().retention_days)
    with session_scope() as db:
        ids = list(
            db.scalars(
                select(Lead.id).where(
                    Lead.status.in_([LeadStatus.CLOSED, LeadStatus.UNQUALIFIED]),
                    Lead.updated_at < cutoff,
                )
            )
        )
        if not args.dry_run and ids:
            db.execute(delete(Lead).where(Lead.id.in_(ids)))
            db.add(
                AuditEvent(
                    actor_type="system",
                    action="retention.purged",
                    details={"count": len(ids), "cutoff": cutoff.isoformat()},
                )
            )
    verb = "would delete" if args.dry_run else "deleted"
    print(f"{verb} {len(ids)} leads older than {cutoff:%Y-%m-%d}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(required=True)
    p = sub.add_parser("create-user")
    p.add_argument("--email", required=True)
    p.add_argument("--full-name")
    p.add_argument("--role", choices=[r.value for r in Role], default=Role.OPERATOR.value)
    p.set_defaults(func=create_user)
    p = sub.add_parser("set-password")
    p.add_argument("--email", required=True)
    p.set_defaults(func=set_password)
    p = sub.add_parser("purge")
    p.add_argument("--dry-run", action="store_true")
    p.set_defaults(func=purge)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
