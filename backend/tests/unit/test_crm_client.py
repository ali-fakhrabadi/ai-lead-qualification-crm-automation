"""Unit/contract tests of the HubSpot-shaped CRM client against a fake transport."""

from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from app.crm.client import NOTE_TO_CONTACT, CrmError, HubSpotStyleClient, RetryPolicy
from app.crm.mapping import DEFAULT_MAP, contact_properties, note_body
from app.db.models import Assessment, AssessmentStatus, Decision, Lead


class FakeCrm:
    """In-memory model of the API subset, with scripted failures per operation."""

    def __init__(self) -> None:
        self.contacts: dict[str, dict[str, Any]] = {}
        self.calls: list[tuple[str, str]] = []
        self.failures: dict[str, list[int]] = {}
        self.notes: list[dict[str, Any]] = []
        self.associations: set[tuple[str, str]] = set()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        self.calls.append((request.method, request.url.path))
        assert request.headers["authorization"] == "Bearer token-123"
        queue = self.failures.get(key)
        if queue:
            status = queue.pop(0)
            return httpx.Response(
                status,
                json={"message": "scripted"},
                headers={"Retry-After": "1"} if status == 429 else {},
            )
        body = json.loads(request.content) if request.content else {}
        if key == "POST /crm/v3/objects/contacts/search":
            email = body["filterGroups"][0]["filters"][0]["value"]
            hits = [
                {"id": cid, "properties": c}
                for cid, c in self.contacts.items()
                if c["email"] == email
            ]
            return httpx.Response(200, json={"total": len(hits), "results": hits})
        if key == "POST /crm/v3/objects/contacts":
            email = body["properties"]["email"]
            for cid, contact in self.contacts.items():
                if contact["email"] == email:
                    return httpx.Response(
                        409, json={"message": f"Contact already exists. Existing ID: {cid}"}
                    )
            cid = str(100 + len(self.contacts))
            self.contacts[cid] = dict(body["properties"])
            return httpx.Response(201, json={"id": cid, "properties": body["properties"]})
        if request.method == "PATCH":
            cid = request.url.path.rsplit("/", 1)[1]
            self.contacts[cid].update(body["properties"])
            return httpx.Response(200, json={"id": cid})
        if key == "POST /crm/v3/objects/companies/search":
            return httpx.Response(200, json={"total": 0, "results": []})
        if key == "POST /crm/v3/objects/companies":
            return httpx.Response(201, json={"id": "7"})
        if request.method == "PUT":
            parts = request.url.path.split("/")
            self.associations.add((parts[5], parts[-1]))
            return httpx.Response(200, json={})
        if key == "POST /crm/v3/objects/notes":
            self.notes.append(body)
            return httpx.Response(201, json={"id": "n1"})
        return httpx.Response(404)


def client(
    fake: FakeCrm, records: list[tuple[Any, ...]] | None = None, attempts: int = 3
) -> HubSpotStyleClient:
    sleeps: list[float] = []
    c = HubSpotStyleClient(
        "http://crm.test",
        "token-123",
        transport=httpx.MockTransport(fake),
        retry=RetryPolicy(attempts, 0.5, 4.0),
        record=(lambda *a: records.append(a)) if records is not None else None,
        sleep=sleeps.append,
    )
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


def test_create_then_update_never_duplicates() -> None:
    fake = FakeCrm()
    c = client(fake)
    assert c.find_contact("maya@harborview.example") is None
    cid = c.create_contact({"email": "maya@harborview.example", "firstname": "Maya"})
    assert c.find_contact("maya@harborview.example") == cid
    c.update_contact(cid, {"jobtitle": "Ops"})
    assert len(fake.contacts) == 1
    assert fake.contacts[cid]["jobtitle"] == "Ops"


def test_create_conflict_resolves_to_the_existing_contact() -> None:
    fake = FakeCrm()
    fake.contacts["555"] = {"email": "maya@harborview.example"}
    cid = client(fake).create_contact({"email": "maya@harborview.example", "lastname": "Chen"})
    assert cid == "555"
    assert len(fake.contacts) == 1 and fake.contacts["555"]["lastname"] == "Chen"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_errors_are_retried_with_bounded_backoff(status: int) -> None:
    fake = FakeCrm()
    fake.failures["POST /crm/v3/objects/contacts/search"] = [status, status]
    records: list[tuple[Any, ...]] = []
    c = client(fake, records)
    assert c.find_contact("maya@harborview.example") is None
    outcomes = [r[1] for r in records]
    assert outcomes == ["transient", "transient", "success"]
    sleeps = c.sleeps  # type: ignore[attr-defined]
    assert len(sleeps) == 2 and all(0 <= s <= 4.0 for s in sleeps)
    if status == 429:
        assert sleeps == [1.0, 1.0]  # Retry-After honoured


def test_transient_errors_beyond_the_budget_raise_transient() -> None:
    fake = FakeCrm()
    fake.failures["POST /crm/v3/objects/contacts/search"] = [503] * 5
    with pytest.raises(CrmError) as exc:
        client(fake).find_contact("x@y.example")
    assert exc.value.transient and exc.value.code == "server_error"
    assert len(fake.calls) == 3


@pytest.mark.parametrize(
    ("status", "code"), [(400, "rejected"), (401, "unauthorized"), (403, "forbidden")]
)
def test_permanent_errors_are_not_retried(status: int, code: str) -> None:
    fake = FakeCrm()
    fake.failures["POST /crm/v3/objects/contacts"] = [status]
    with pytest.raises(CrmError) as exc:
        client(fake).create_contact({"email": "x@y.example"})
    assert not exc.value.transient and exc.value.code == code
    assert len(fake.calls) == 1


def test_timeouts_are_transient() -> None:
    def timeout(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow")

    c = HubSpotStyleClient(
        "http://crm.test",
        "t",
        transport=httpx.MockTransport(timeout),
        retry=RetryPolicy(2, 0, 0),
        sleep=lambda _s: None,
    )
    with pytest.raises(CrmError) as exc:
        c.find_contact("x@y.example")
    assert exc.value.transient and exc.value.code == "timeout"


def test_company_association_and_note_payloads() -> None:
    fake = FakeCrm()
    c = client(fake)
    cid = c.create_contact({"email": "maya@harborview.example"})
    company = c.find_company("harborview.example") or c.create_company(
        "Harborview", "harborview.example"
    )
    c.associate(cid, company)
    c.add_note(cid, "Qualified", "2026-11-19T10:00:00+00:00")
    assert (cid, "7") in fake.associations
    assoc = fake.notes[0]["associations"][0]
    assert assoc["types"][0]["associationTypeId"] == NOTE_TO_CONTACT


def test_backoff_delay_is_capped_and_jittered() -> None:
    policy = RetryPolicy(10, 0.5, 4.0)
    delays = [policy.delay(attempt) for attempt in range(1, 10) for _ in range(20)]
    assert all(0 <= d <= 4.0 for d in delays)
    assert policy.delay(1, retry_after=30) == 4.0


def test_contact_mapping_uses_configured_names_and_skips_empty_values() -> None:
    lead = Lead(
        reference=1001,
        email_normalized="maya@harborview.example",
        first_name="Maya",
        last_name="Chen",
        company="Harborview",
        job_title=None,
        website=None,
        country="Canada",
        interest="crm_integration",
        decision=Decision.QUALIFIED,
        score=85,
        intent_category="new_project",
    )
    props = contact_properties(lead)
    assert props["email"] == "maya@harborview.example"
    assert props["lq_reference"] == "L-1001" and props["lq_score"] == "85"
    assert "jobtitle" not in props
    assert props["lifecyclestage"] == DEFAULT_MAP["constants"]["lifecyclestage"]


def test_note_contains_the_assessment_summary_not_the_raw_message() -> None:
    lead = Lead(reference=1001, message="secret internal details")
    assessment = Assessment(
        provider="ollama",
        model="llama3.1:8b",
        status=AssessmentStatus.VALID,
        parsed={
            "intent_category": "new_project",
            "service_fit": "strong",
            "urgency": "high",
            "reasoning_summary": "Clear need.",
        },
        rules={"decision": "qualified", "score": 85, "reasons": []},
    )
    body = note_body(lead, assessment)
    assert "Decision: qualified (score 85/100)" in body
    assert "secret internal details" not in body
