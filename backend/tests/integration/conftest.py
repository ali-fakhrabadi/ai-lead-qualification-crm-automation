"""Integration fixtures: a real PostgreSQL test database, migrated by Alembic as its owner.

The application connects as the non-owner role (LEAD_DB_USER, normally ``lead_app``), so
missing grants fail here as they would in production. The demo CRM runs in-process against
the same test database (its own tables) and is reached through a real HTTP transport.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.engine import URL

BACKEND = Path(__file__).resolve().parents[2]
APP_TABLES = (
    "audit_events, crm_attempts, crm_syncs, follow_up_drafts, outbox_events, assessments, "
    "duplicate_reviews, lead_events, leads, refresh_tokens, users, rate_limit_buckets, fault_rules"
)
PASSWORD = "integration-password-1"


def _secret(name: str) -> str:
    path = os.environ.get(f"{name}_FILE")
    return Path(path).read_text().strip() if path else os.environ.get(name, "")


def admin_url() -> URL:
    return URL.create(
        "postgresql+psycopg",
        username=os.environ.get("LEAD_DB_ADMIN_USER", "lead_owner"),
        password=_secret("LEAD_DB_ADMIN_PASSWORD"),
        host=os.environ.get("LEAD_DB_HOST", "postgres"),
        port=int(os.environ.get("LEAD_DB_PORT", "5432")),
        database=os.environ.get("LEAD_DB_NAME", "leads_test"),
    )


# The demo CRM shares the test database (its tables do not overlap with the app's).
os.environ.setdefault("CRM_DEMO_DB_HOST", os.environ.get("LEAD_DB_HOST", "postgres"))
os.environ.setdefault("CRM_DEMO_DB_NAME", os.environ.get("LEAD_DB_NAME", "leads_test"))
os.environ.setdefault("CRM_DEMO_DB_USER", os.environ.get("LEAD_DB_ADMIN_USER", "lead_owner"))
if os.environ.get("LEAD_DB_ADMIN_PASSWORD_FILE"):
    os.environ.setdefault("CRM_DEMO_DB_PASSWORD_FILE", os.environ["LEAD_DB_ADMIN_PASSWORD_FILE"])
os.environ.setdefault("CRM_DEMO_TOKEN", os.environ.get("LEAD_CRM_TOKEN", "crm-token-for-tests"))
os.environ.setdefault("CRM_DEMO_ADMIN_TOKEN", "crm-admin-token-for-tests")
os.environ.setdefault("CRM_DEMO_FAULTS_ENABLED", "true")


@pytest.fixture(scope="session")
def admin_engine() -> Iterator[Engine]:
    from alembic.config import Config

    from alembic import command

    config = Config(str(BACKEND / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND / "alembic"))
    command.upgrade(config, "head")
    engine = create_engine(admin_url())
    yield engine
    engine.dispose()


@pytest.fixture
def clean_db(admin_engine: Engine) -> Iterator[Engine]:
    from app.core.config import get_settings
    from app.qualification.rubric import get_rubric

    get_settings.cache_clear()
    get_rubric.cache_clear()
    with admin_engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {APP_TABLES} RESTART IDENTITY CASCADE"))
        conn.execute(text("ALTER SEQUENCE lead_reference_seq RESTART WITH 1001"))
        conn.execute(
            text(
                "DO $$ BEGIN IF to_regclass('contacts') IS NOT NULL THEN "
                "TRUNCATE contact_company, notes, contacts, companies, faults, requests "
                "RESTART IDENTITY CASCADE; END IF; END $$"
            )
        )
    yield admin_engine


@pytest.fixture
def api(clean_db: Engine) -> Iterator[TestClient]:
    from app.main import create_app

    with TestClient(create_app()) as client:
        yield client


@pytest.fixture
def crm_transport(
    clean_db: Engine, monkeypatch: pytest.MonkeyPatch
) -> Iterator[httpx.BaseTransport]:
    """Route the CRM adapter to the in-process demo CRM over its HTTP interface."""
    from app.services import crm_sync
    from crm_demo.app import app as crm_app

    crm_client = TestClient(crm_app)
    transport = crm_client._transport  # the HTTP transport into the ASGI app
    original = crm_sync.make_client

    def make_client(settings: Any, record: Any, _transport: Any = None) -> Any:
        return original(settings, record, transport)

    monkeypatch.setattr(crm_sync, "make_client", make_client)
    with crm_client:
        yield transport


@pytest.fixture
def crm_admin(crm_transport: httpx.BaseTransport) -> httpx.Client:
    return httpx.Client(
        base_url="http://crm",
        transport=crm_transport,
        headers={"X-Admin-Token": "crm-admin-token-for-tests"},
    )


@pytest.fixture
def sent_mail(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, str]]:
    from app.services import followups

    outbox: list[dict[str, str]] = []

    def send(_settings: Any, to: str, subject: str, body: str, reference: str) -> str:
        outbox.append({"to": to, "subject": subject, "body": body, "reference": reference})
        return f"<{uuid.uuid4().hex}@test>"

    monkeypatch.setattr(followups, "send", send)
    return outbox


@pytest.fixture
def make_user(clean_db: Engine) -> Callable[[str, str], str]:
    from app.db.models import Role
    from app.db.session import session_scope
    from app.services import auth

    def _make(email: str, role: str) -> str:
        with session_scope() as db:
            auth.create_user(db, email, PASSWORD, Role(role))
        return email

    return _make


def login(api: TestClient, email: str) -> dict[str, str]:
    response = api.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


@pytest.fixture
def operator(api: TestClient, make_user: Callable[[str, str], str]) -> dict[str, str]:
    return login(api, make_user("olivia@ops.example", "operator"))


@pytest.fixture
def viewer(api: TestClient, make_user: Callable[[str, str], str]) -> dict[str, str]:
    return login(api, make_user("victor@ops.example", "viewer"))


@pytest.fixture
def admin(api: TestClient, make_user: Callable[[str, str], str]) -> dict[str, str]:
    return login(api, make_user("ada@ops.example", "admin"))


INTERNAL = {"Authorization": f"Bearer {os.environ['LEAD_INTERNAL_TOKEN']}"}


def lead_payload(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "first_name": "Maya",
        "last_name": "Chen",
        "email": "maya.chen@harborview-logistics.example",
        "company": "Harborview Logistics",
        "website": "harborview-logistics.example",
        "job_title": "Operations Manager",
        "country": "Canada",
        "interest": "crm_integration",
        "message": (
            "We copy about 60 web form inquiries a week into HubSpot by hand and want them "
            "synced automatically without duplicates, by the end of next month."
        ),
        "consent_contact": True,
    }
    data.update(overrides)
    return data
