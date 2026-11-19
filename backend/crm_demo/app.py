"""Demo CRM: a small stand-in for an external CRM, so the whole workflow runs locally.

It implements the subset of the HubSpot CRM v3/v4 API shape that the CRM adapter uses
(contact search/create/update, company search/create, contact-company association, notes),
with its own database and bearer token. It is *not* HubSpot and makes no claim of full
compatibility; it exists for demos, tests and the evaluation.

Fault injection (``CRM_DEMO_FAULTS_ENABLED=true``, admin token required) makes chosen
operations answer 429/5xx/4xx a given number of times, to measure retries honestly.

    uvicorn crm_demo.app:app --port 8100
"""

from __future__ import annotations

import hmac
import html
import os
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Engine,
    ForeignKey,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

metadata = MetaData()
contacts = Table(
    "contacts",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("email", String(320), unique=True, nullable=False),
    Column("properties", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
    Column("update_count", Integer, nullable=False, default=0),
)
companies = Table(
    "companies",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("domain", String(253), unique=True, nullable=False),
    Column("properties", JSONB, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
associations = Table(
    "contact_company",
    metadata,
    Column("contact_id", BigInteger, ForeignKey("contacts.id"), primary_key=True),
    Column("company_id", BigInteger, ForeignKey("companies.id"), primary_key=True),
)
notes = Table(
    "notes",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("contact_id", BigInteger, ForeignKey("contacts.id"), nullable=False),
    Column("body", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
)
faults = Table(
    "faults",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("operation", String(40), nullable=False),  # e.g. contacts.create, or *
    Column("match_email", String(320), nullable=False),  # exact email, or *
    Column("status", Integer, nullable=False),
    Column("remaining", Integer, nullable=False),
)
requests_log = Table(
    "requests",
    metadata,
    Column("id", BigInteger, primary_key=True, autoincrement=True),
    Column("ts", DateTime(timezone=True), nullable=False),
    Column("operation", String(40), nullable=False),
    Column("status", Integer, nullable=False),
    Column("faulted", Boolean, nullable=False),
)


def _secret(name: str) -> str:
    path = os.environ.get(f"{name}_FILE")
    if path:
        return Path(path).read_text(encoding="utf-8").strip()
    return os.environ.get(name, "")


@lru_cache
def engine() -> Engine:
    url = URL.create(
        "postgresql+psycopg",
        username=os.environ.get("CRM_DEMO_DB_USER", "crm_demo"),
        password=_secret("CRM_DEMO_DB_PASSWORD"),
        host=os.environ.get("CRM_DEMO_DB_HOST", "postgres"),
        port=int(os.environ.get("CRM_DEMO_DB_PORT", "5432")),
        database=os.environ.get("CRM_DEMO_DB_NAME", "crm_demo"),
    )
    eng = create_engine(url, pool_pre_ping=True, pool_size=4)
    metadata.create_all(eng)
    return eng


def _now() -> datetime:
    return datetime.now(UTC)


def require_token(authorization: str | None = Header(None)) -> None:
    expected = _secret("CRM_DEMO_TOKEN")
    given = (authorization or "").removeprefix("Bearer ").strip()
    if not expected or not hmac.compare_digest(given.encode(), expected.encode()):
        raise HTTPException(
            401,
            {
                "status": "error",
                "category": "INVALID_AUTHENTICATION",
                "message": "Authentication credentials not found.",
            },
        )


app = FastAPI(title="Demo CRM (HubSpot-shaped subset)", docs_url=None, redoc_url=None)


def _fault(operation: str, email: str | None) -> JSONResponse | None:
    if os.environ.get("CRM_DEMO_FAULTS_ENABLED", "false").lower() != "true":
        return None
    with engine().begin() as conn:
        row = conn.execute(
            select(faults)
            .where(
                faults.c.remaining > 0,
                faults.c.operation.in_([operation, "*"]),
                faults.c.match_email.in_([email or "", "*"]),
            )
            .order_by(faults.c.id)
            .limit(1)
            .with_for_update()
        ).first()
        if row is None:
            return None
        conn.execute(
            update(faults).where(faults.c.id == row.id).values(remaining=row.remaining - 1)
        )
        conn.execute(
            insert(requests_log).values(
                ts=_now(), operation=operation, status=row.status, faulted=True
            )
        )
    headers = {"Retry-After": "1"} if row.status == 429 else None
    return JSONResponse(
        {
            "status": "error",
            "category": "INJECTED_FAULT",
            "message": f"Injected failure {row.status}",
        },
        status_code=row.status,
        headers=headers,
    )


def _log(operation: str, status: int) -> None:
    with engine().begin() as conn:
        conn.execute(
            insert(requests_log).values(
                ts=_now(), operation=operation, status=status, faulted=False
            )
        )


def _contact_out(row: Any) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "properties": {**row.properties, "email": row.email},
        "createdAt": row.created_at.isoformat(),
        "updatedAt": row.updated_at.isoformat(),
        "archived": False,
    }


def _eq_filter(body: dict[str, Any], name: str) -> str | None:
    for group in body.get("filterGroups") or []:
        for f in group.get("filters") or []:
            if f.get("propertyName") == name and f.get("operator") == "EQ":
                return str(f.get("value", "")).strip().lower()
    return None


@app.get("/health")
def health() -> dict[str, str]:
    with engine().connect():
        pass
    return {"status": "ok"}


@app.post("/crm/v3/objects/contacts/search", dependencies=[Depends(require_token)])
async def search_contacts(request: Request) -> Any:
    body = await request.json()
    email = _eq_filter(body, "email")
    if (fault := _fault("contacts.search", email)) is not None:
        return fault
    with engine().connect() as conn:
        rows = (
            conn.execute(select(contacts).where(contacts.c.email == email)).all() if email else []
        )
    _log("contacts.search", 200)
    return {"total": len(rows), "results": [_contact_out(r) for r in rows]}


@app.post("/crm/v3/objects/contacts", status_code=201, dependencies=[Depends(require_token)])
async def create_contact(request: Request) -> Any:
    props = dict((await request.json()).get("properties") or {})
    email = str(props.pop("email", "")).strip().lower()
    if not email or "@" not in email:
        return JSONResponse(
            {
                "status": "error",
                "category": "VALIDATION_ERROR",
                "message": "Property values were not valid: email",
            },
            status_code=400,
        )
    if (fault := _fault("contacts.create", email)) is not None:
        return fault
    now = _now()
    try:
        with engine().begin() as conn:
            row = conn.execute(
                insert(contacts)
                .values(
                    email=email, properties=props, created_at=now, updated_at=now, update_count=0
                )
                .returning(contacts)
            ).one()
    except IntegrityError:
        with engine().connect() as conn:
            existing = conn.execute(select(contacts.c.id).where(contacts.c.email == email)).scalar()
        _log("contacts.create", 409)
        return JSONResponse(
            {
                "status": "error",
                "category": "CONFLICT",
                "message": f"Contact already exists. Existing ID: {existing}",
            },
            status_code=409,
        )
    _log("contacts.create", 201)
    return _contact_out(row)


@app.patch("/crm/v3/objects/contacts/{contact_id}", dependencies=[Depends(require_token)])
async def update_contact(contact_id: int, request: Request) -> Any:
    props = dict((await request.json()).get("properties") or {})
    props.pop("email", None)
    with engine().connect() as conn:
        email = conn.execute(select(contacts.c.email).where(contacts.c.id == contact_id)).scalar()
    if email is None:
        return JSONResponse(
            {"status": "error", "category": "OBJECT_NOT_FOUND", "message": "resource not found"},
            status_code=404,
        )
    if (fault := _fault("contacts.update", email)) is not None:
        return fault
    with engine().begin() as conn:
        current = conn.execute(
            select(contacts.c.properties).where(contacts.c.id == contact_id).with_for_update()
        ).scalar_one()
        row = conn.execute(
            update(contacts)
            .where(contacts.c.id == contact_id)
            .values(
                properties={**current, **props},
                updated_at=_now(),
                update_count=contacts.c.update_count + 1,
            )
            .returning(contacts)
        ).one()
    _log("contacts.update", 200)
    return _contact_out(row)


@app.get("/crm/v3/objects/contacts", dependencies=[Depends(require_token)])
def list_contacts(limit: int = 100) -> Any:
    with engine().connect() as conn:
        rows = conn.execute(
            select(contacts).order_by(contacts.c.id.desc()).limit(min(limit, 500))
        ).all()
    return {"results": [_contact_out(r) for r in rows]}


@app.post("/crm/v3/objects/companies/search", dependencies=[Depends(require_token)])
async def search_companies(request: Request) -> Any:
    domain = _eq_filter(await request.json(), "domain")
    with engine().connect() as conn:
        rows = (
            conn.execute(select(companies).where(companies.c.domain == domain)).all()
            if domain
            else []
        )
    _log("companies.search", 200)
    return {
        "total": len(rows),
        "results": [
            {"id": str(r.id), "properties": {**r.properties, "domain": r.domain}} for r in rows
        ],
    }


@app.post("/crm/v3/objects/companies", status_code=201, dependencies=[Depends(require_token)])
async def create_company(request: Request) -> Any:
    props = dict((await request.json()).get("properties") or {})
    domain = str(props.pop("domain", "")).strip().lower()
    with engine().begin() as conn:
        stmt = pg_insert(companies).values(domain=domain, properties=props, created_at=_now())
        row = conn.execute(
            stmt.on_conflict_do_update(
                index_elements=["domain"], set_={"domain": stmt.excluded.domain}
            ).returning(companies)
        ).one()
    _log("companies.create", 201)
    return {"id": str(row.id), "properties": {**row.properties, "domain": row.domain}}


@app.put(
    "/crm/v4/objects/contact/{contact_id}/associations/default/company/{company_id}",
    dependencies=[Depends(require_token)],
)
def associate(contact_id: int, company_id: int) -> Any:
    with engine().begin() as conn:
        conn.execute(
            pg_insert(associations)
            .values(contact_id=contact_id, company_id=company_id)
            .on_conflict_do_nothing()
        )
    _log("associations.put", 200)
    return {
        "status": "COMPLETE",
        "results": [{"from": {"id": str(contact_id)}, "to": {"id": str(company_id)}}],
    }


@app.post("/crm/v3/objects/notes", status_code=201, dependencies=[Depends(require_token)])
async def create_note(request: Request) -> Any:
    body = await request.json()
    contact_id = int(body["associations"][0]["to"]["id"])
    text = str((body.get("properties") or {}).get("hs_note_body", ""))[:5000]
    with engine().begin() as conn:
        note_id = conn.execute(
            insert(notes)
            .values(contact_id=contact_id, body=text, created_at=_now())
            .returning(notes.c.id)
        ).scalar()
    _log("notes.create", 201)
    return {"id": str(note_id), "properties": {"hs_note_body": text}}


# --- fault injection and stats (tests and evaluation only) -----------------------------------
def require_admin(x_admin_token: str | None = Header(None)) -> None:
    expected = _secret("CRM_DEMO_ADMIN_TOKEN")
    if (
        os.environ.get("CRM_DEMO_FAULTS_ENABLED", "false").lower() != "true"
        or not expected
        or not hmac.compare_digest((x_admin_token or "").encode(), expected.encode())
    ):
        raise HTTPException(404)


@app.post("/_admin/faults", status_code=201, dependencies=[Depends(require_admin)])
async def add_fault(request: Request) -> Any:
    body = await request.json()
    with engine().begin() as conn:
        conn.execute(
            insert(faults).values(
                operation=str(body.get("operation", "*")),
                match_email=str(body.get("email", "*")).lower(),
                status=int(body["status"]),
                remaining=int(body.get("remaining", 1)),
            )
        )
    return {"ok": True}


@app.delete("/_admin/faults", status_code=204, dependencies=[Depends(require_admin)])
def clear_faults() -> None:
    with engine().begin() as conn:
        conn.execute(delete(faults))


@app.get("/_admin/stats", dependencies=[Depends(require_admin)])
def stats() -> Any:
    with engine().connect() as conn:
        dup = conn.execute(
            select(func.count()).select_from(
                select(contacts.c.email)
                .group_by(contacts.c.email)
                .having(func.count() > 1)
                .subquery()
            )
        ).scalar()
        return {
            "contacts": conn.execute(select(func.count()).select_from(contacts)).scalar(),
            "companies": conn.execute(select(func.count()).select_from(companies)).scalar(),
            "notes": conn.execute(select(func.count()).select_from(notes)).scalar(),
            "duplicate_emails": dup,
            "requests": conn.execute(select(func.count()).select_from(requests_log)).scalar(),
            "faulted_requests": conn.execute(
                select(func.count()).select_from(requests_log).where(requests_log.c.faulted)
            ).scalar(),
        }


# --- read-only record view for demos (internal network only) -----------------------------------
@app.get("/ui/contacts", response_class=HTMLResponse)
def ui_contacts() -> str:
    with engine().connect() as conn:
        rows = conn.execute(select(contacts).order_by(contacts.c.id.desc()).limit(50)).all()
        counts: dict[Any, int] = dict(
            conn.execute(
                select(notes.c.contact_id, func.count()).group_by(notes.c.contact_id)
            ).all()
        )
    e = html.escape

    def row(r: Any) -> str:
        p = r.properties
        cells = [
            str(r.id),
            e(r.email),
            f"{e(p.get('firstname', ''))} {e(p.get('lastname', ''))}",
            e(p.get("company", "")),
            e(p.get("lq_reference", "")),
            e(p.get("lq_decision", "")),
            e(str(p.get("lq_score", ""))),
            str(r.update_count),
            str(counts.get(r.id, 0)),
            f"{r.updated_at:%Y-%m-%d %H:%M}",
        ]
        return "<tr>" + "".join(f"<td>{c}</td>" for c in cells) + "</tr>"

    headers = [
        "ID",
        "Email",
        "Name",
        "Company",
        "lq_reference",
        "lq_decision",
        "lq_score",
        "Updates",
        "Notes",
        "Updated",
    ]
    head = "<tr>" + "".join(f"<th>{h}</th>" for h in headers) + "</tr>"
    body = "".join(row(r) for r in rows)
    style = (
        "body{font-family:system-ui,sans-serif;margin:24px;color:#1f2937}h1{font-size:20px}"
        "p{color:#6b7280;font-size:13px}table{border-collapse:collapse;font-size:13px;"
        "width:100%}th,td{border-bottom:1px solid #e5e7eb;padding:6px 8px;text-align:left}"
        "th{background:#f9fafb}"
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8"><title>Demo CRM - contacts</title>'
        f"<style>{style}</style></head><body><h1>Demo CRM - contacts</h1><p>Stand-in CRM for "
        "the reference project (HubSpot-shaped API subset). Synthetic records only.</p>"
        f"<table>{head}{body}</table></body></html>"
    )
