"""Local mock of the GoHighLevel API v2 subset the connector uses (in memory, per instance).

Modelled on GoHighLevel's published OpenAPI specs (contacts and businesses). Where the spec
does not document a behaviour, the mock makes an explicit choice, marked ASSUMPTION below; the
connector is written so those choices do not decide correctness (see app/crm/gohighlevel.py).

What it checks on every request, like the real API: a bearer token, the ``Version`` header, and
that the ``locationId`` is the one the token belongs to. Contacts are unique per email in the
location (the default "Allow Duplicate Contact" off), and the upsert matches on email.

Test controls (not part of GoHighLevel): ``POST /_admin/faults`` scripts error answers for an
operation, ``GET /_admin/stats`` counts records and duplicate emails.

    from fastapi.testclient import TestClient
    mock = create_app(token="t", location_id="loc-1")
    transport = TestClient(mock)._transport
"""

from __future__ import annotations

import itertools
from collections import Counter
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

VERSION = "2021-07-28"

# operation name -> (method, path template), used to match scripted faults
OPERATIONS = {
    "search_contact": ("GET", "/contacts/search/duplicate"),
    "upsert_contact": ("POST", "/contacts/upsert"),
    "update_contact": ("PUT", "/contacts/{id}"),
    "search_company": ("GET", "/businesses/"),
    "create_company": ("POST", "/businesses/"),
    "associate": ("POST", "/contacts/bulk/business"),
    "create_note": ("POST", "/contacts/{id}/notes"),
}


def create_app(token: str, location_id: str) -> FastAPI:
    app = FastAPI(title="GoHighLevel mock")
    ids = itertools.count(1)
    contacts: dict[str, dict[str, Any]] = {}
    businesses: dict[str, dict[str, Any]] = {}
    notes: list[dict[str, Any]] = []
    faults: dict[str, list[dict[str, Any]]] = {}
    requests: Counter[str] = Counter()
    app.state.contacts, app.state.businesses, app.state.notes = contacts, businesses, notes

    def new_id(prefix: str) -> str:
        return f"{prefix}{next(ids):06d}"

    def error(status: int, message: str, **extra: Any) -> JSONResponse:
        return JSONResponse({"statusCode": status, "message": message, **extra}, status)

    @app.middleware("http")
    async def gate(request: Request, call_next: Any) -> Any:
        if request.url.path.startswith("/_admin"):
            return await call_next(request)
        if request.headers.get("authorization") != f"Bearer {token}":
            return error(401, "Invalid JWT")
        if request.headers.get("version") != VERSION:
            # ASSUMPTION: the spec marks Version as required; the exact refusal is undocumented.
            return error(400, "Version header is missing or unsupported")
        operation = next(
            (
                name
                for name, (method, template) in OPERATIONS.items()
                if method == request.method and _matches(template, request.url.path)
            ),
            None,
        )
        if operation:
            requests[operation] += 1
            queue = faults.get(operation) or faults.get("*")
            if queue:
                fault = queue[0]
                fault["remaining"] -= 1
                if fault["remaining"] <= 0:
                    queue.pop(0)
                headers = {"Retry-After": str(fault["retry_after"])} if fault["retry_after"] else {}
                return JSONResponse(
                    {"statusCode": fault["status"], "message": "scripted fault"},
                    fault["status"],
                    headers=headers,
                )
        return await call_next(request)

    def wrong_location(value: Any) -> JSONResponse | None:
        if value != location_id:
            # ASSUMPTION: a token used for another location is refused with 403.
            return error(403, "The token does not have access to this location")
        return None

    def split(body: dict[str, Any]) -> dict[str, Any]:
        record = {k: v for k, v in body.items() if k not in ("locationId", "customFields")}
        for field in body.get("customFields") or []:
            if not isinstance(field, dict) or not (field.get("id") or field.get("key")):
                raise ValueError("customFields items need an id or a key")
            record.setdefault("customFields", {})[field.get("key") or field["id"]] = field.get(
                "field_value"
            )
        return record

    def by_email(email: str | None) -> dict[str, Any] | None:
        if not email:
            return None
        return next((c for c in contacts.values() if c.get("email") == email.lower()), None)

    def merge(contact: dict[str, Any], fields: dict[str, Any]) -> None:
        custom = fields.pop("customFields", {})
        contact.update(fields)
        contact.setdefault("customFields", {}).update(custom)
        if contact.get("email"):
            contact["email"] = contact["email"].lower()

    @app.get("/contacts/search/duplicate")
    def search_duplicate(locationId: str | None = None, email: str | None = None) -> Any:
        if refused := wrong_location(locationId):
            return refused
        # ASSUMPTION: the 200 answer has no schema in the spec; {"contact": {...} | null}.
        return {"contact": by_email(email)}

    @app.post("/contacts/upsert")
    async def upsert(request: Request) -> Any:
        body = await request.json()
        if refused := wrong_location(body.get("locationId")):
            return refused
        try:
            fields = split(body)
        except ValueError as exc:
            return error(422, str(exc))
        existing = by_email(fields.get("email"))
        if existing is not None:
            merge(existing, fields)
            return {"new": False, "contact": existing, "traceId": new_id("trace")}
        contact = {"id": new_id("ct"), "locationId": location_id}
        merge(contact, fields)
        contacts[contact["id"]] = contact
        return {"new": True, "contact": contact, "traceId": new_id("trace")}

    @app.put("/contacts/{contact_id}")
    async def update(contact_id: str, request: Request) -> Any:
        contact = contacts.get(contact_id)
        if contact is None:
            return error(404, "Contact not found")
        body = await request.json()
        if "locationId" in body:
            # ASSUMPTION: UpdateContactDto has no locationId; extra fields are refused.
            return error(422, "property locationId should not exist")
        try:
            merge(contact, split(body))
        except ValueError as exc:
            return error(422, str(exc))
        return {"succeded": True, "contact": contact}

    @app.get("/businesses/")
    def list_businesses(locationId: str | None = None, limit: str = "100", skip: str = "0") -> Any:
        if refused := wrong_location(locationId):
            return refused
        rows = list(businesses.values())
        return {"businesses": rows[int(skip) : int(skip) + int(limit)]}

    @app.post("/businesses/", status_code=201)
    async def create_business(request: Request) -> Any:
        body = await request.json()
        if refused := wrong_location(body.get("locationId")):
            return refused
        if not body.get("name"):
            return error(422, "name should not be empty")
        business = {"id": new_id("bz"), **body}
        businesses[business["id"]] = business
        return {"success": True, "buiseness": business}  # sic: the spec's spelling

    @app.post("/contacts/bulk/business")
    async def link_business(request: Request) -> Any:
        body = await request.json()
        if refused := wrong_location(body.get("locationId")):
            return refused
        business_id = body.get("businessId")
        if business_id is not None and business_id not in businesses:
            return error(404, "Business not found")
        missing = [i for i in body.get("ids") or [] if i not in contacts]
        if missing or not body.get("ids"):
            return error(422, "ids must name existing contacts")
        for contact_id in body["ids"]:
            contacts[contact_id]["businessId"] = business_id
        return {"success": True, "ids": body["ids"]}

    @app.post("/contacts/{contact_id}/notes", status_code=201)
    async def add_note(contact_id: str, request: Request) -> Any:
        if contact_id not in contacts:
            return error(404, "Contact not found")
        body = await request.json()
        if not body.get("body"):
            return error(422, "body should not be empty")
        note = {"id": new_id("nt"), "contactId": contact_id, "body": body["body"]}
        notes.append(note)
        return {"note": note}

    @app.post("/_admin/faults")
    async def add_fault(request: Request) -> Any:
        body = await request.json()
        faults.setdefault(body.get("operation", "*"), []).append(
            {
                "status": int(body["status"]),
                "remaining": int(body.get("remaining", 1)),
                "retry_after": body.get("retry_after"),
            }
        )
        return {"ok": True}

    @app.get("/_admin/stats")
    def stats() -> Any:
        emails = Counter(c.get("email") for c in contacts.values())
        return {
            "contacts": len(contacts),
            "businesses": len(businesses),
            "notes": len(notes),
            "duplicate_emails": sum(1 for n in emails.values() if n > 1),
            "requests": dict(requests),
        }

    return app


def _matches(template: str, path: str) -> bool:
    want, got = template.strip("/").split("/"), path.strip("/").split("/")
    return len(want) == len(got) and all(
        w == "{id}" or w == g for w, g in zip(want, got, strict=True)
    )
