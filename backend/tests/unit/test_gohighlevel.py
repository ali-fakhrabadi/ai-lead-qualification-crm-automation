"""GoHighLevel connector against the local mock of its API (tests/mocks/gohighlevel.py).

The mock enforces the bearer token, the Version header and the location; it is not the real
service, so these tests prove the connector's behaviour against the published request shapes,
not against a live account.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings
from app.crm.base import CrmError, RetryPolicy
from app.crm.client import HubSpotStyleClient
from app.crm.gohighlevel import BUSINESS_PAGE, DEFAULT_BASE_URL, GoHighLevelClient
from app.crm.mapping import GOHIGHLEVEL_MAP, contact_properties
from app.db.models import Decision, Lead
from app.services import crm_sync
from tests.mocks.gohighlevel import create_app

TOKEN, LOCATION = "pit-test-token", "loc-test-1"


@pytest.fixture
def mock() -> Iterator[TestClient]:
    with TestClient(create_app(token=TOKEN, location_id=LOCATION)) as client:
        yield client


def connector(
    mock: TestClient,
    records: list[tuple[Any, ...]] | None = None,
    *,
    token: str = TOKEN,
    location: str = LOCATION,
) -> GoHighLevelClient:
    sleeps: list[float] = []
    c = GoHighLevelClient(
        "http://ghl.test",
        token,
        location_id=location,
        transport=mock._transport,
        retry=RetryPolicy(3, 0.5, 4.0),
        record=(lambda *r: records.append(r)) if records is not None else None,
        sleep=sleeps.append,
    )
    c.sleeps = sleeps  # type: ignore[attr-defined]
    return c


def fault(mock: TestClient, operation: str, status: int, remaining: int, **extra: Any) -> None:
    mock.post(
        "/_admin/faults",
        json={"operation": operation, "status": status, "remaining": remaining, **extra},
    )


def stats(mock: TestClient) -> dict[str, Any]:
    return mock.get("/_admin/stats").json()  # type: ignore[no-any-return]


def state(mock: TestClient) -> Any:
    return mock.app.state  # type: ignore[attr-defined]


def test_create_then_find_then_update_keeps_one_contact(mock: TestClient) -> None:
    c = connector(mock)
    assert c.find_contact("maya@harborview.example") is None
    cid = c.create_contact({"email": "maya@harborview.example", "firstName": "Maya"})
    assert c.find_contact("maya@harborview.example") == cid
    c.update_contact(cid, {"lastName": "Chen", "lq_score": "85"})
    contact = state(mock).contacts[cid]
    assert contact["lastName"] == "Chen" and contact["customFields"]["lq_score"] == "85"
    assert stats(mock)["contacts"] == 1


def test_a_create_racing_another_writer_resolves_to_the_existing_contact(
    mock: TestClient,
) -> None:
    # Another writer created the contact after our search: the upsert matches it by email.
    first = connector(mock).create_contact({"email": "maya@harborview.example"})
    second = connector(mock).create_contact(
        {"email": "Maya@Harborview.example", "lastName": "Chen"}
    )
    assert second == first
    assert stats(mock)["contacts"] == 1 and stats(mock)["duplicate_emails"] == 0


def test_standard_fields_top_level_and_others_as_custom_fields_by_key() -> None:
    body = GoHighLevelClient._contact_body(
        {"email": "a@b.example", "companyName": "B", "source": "x", "lq_decision": "qualified"}
    )
    assert body == {
        "email": "a@b.example",
        "companyName": "B",
        "source": "x",
        "customFields": [{"key": "lq_decision", "field_value": "qualified"}],
    }


def test_business_found_by_website_domain_across_pages(mock: TestClient) -> None:
    c = connector(mock)
    for i in range(BUSINESS_PAGE + 5):
        c.create_company(f"Filler {i}", f"filler{i}.example")
    target = c.create_company("Harborview", "harborview.example")
    state(mock).businesses[target]["website"] = "https://www.Harborview.example/about"
    assert c.find_company("harborview.example") == target
    assert c.find_company("unknown.example") is None
    assert stats(mock)["requests"]["search_company"] == 4  # two pages per search


def test_association_and_note(mock: TestClient) -> None:
    c = connector(mock)
    cid = c.create_contact({"email": "maya@harborview.example"})
    company = c.find_company("harborview.example") or c.create_company(
        "Harborview", "harborview.example"
    )
    c.associate(cid, company)
    note_id = c.add_note(cid, "Qualified", "2026-11-26T10:00:00+00:00")
    assert state(mock).contacts[cid]["businessId"] == company
    assert state(mock).notes == [{"id": note_id, "contactId": cid, "body": "Qualified"}]


def test_rate_limit_is_retried_honouring_retry_after(mock: TestClient) -> None:
    fault(mock, "search_contact", 429, 2, retry_after=2)
    records: list[tuple[Any, ...]] = []
    c = connector(mock, records)
    assert c.find_contact("x@y.example") is None
    assert [r[1] for r in records] == ["transient", "transient", "success"]
    assert c.sleeps == [2.0, 2.0]  # type: ignore[attr-defined]


def test_outage_beyond_the_budget_is_transient(mock: TestClient) -> None:
    fault(mock, "upsert_contact", 503, 5)
    with pytest.raises(CrmError) as exc:
        connector(mock).create_contact({"email": "x@y.example"})
    assert exc.value.transient and exc.value.code == "server_error"
    assert stats(mock)["requests"]["upsert_contact"] == 3


@pytest.mark.parametrize(
    ("kwargs", "status", "code"),
    [({"token": "wrong"}, 401, "unauthorized"), ({"location": "other"}, 403, "forbidden")],
)
def test_auth_and_location_errors_are_permanent(
    mock: TestClient, kwargs: dict[str, str], status: int, code: str
) -> None:
    records: list[tuple[Any, ...]] = []
    with pytest.raises(CrmError) as exc:
        connector(mock, records, **kwargs).create_contact({"email": "x@y.example"})
    assert not exc.value.transient and exc.value.code == code
    assert exc.value.http_status == status and len(records) == 1


def test_missing_version_header_is_refused_by_the_mock(mock: TestClient) -> None:
    response = mock.get(
        "/contacts/search/duplicate",
        params={"locationId": LOCATION, "email": "x@y.example"},
        headers={"Authorization": f"Bearer {TOKEN}"},
    )
    assert response.status_code == 400


def test_an_answer_without_an_id_is_a_permanent_error() -> None:
    c = GoHighLevelClient(
        "http://ghl.test",
        TOKEN,
        location_id=LOCATION,
        transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={"new": True})),
        sleep=lambda _s: None,
    )
    with pytest.raises(CrmError) as exc:
        c.create_contact({"email": "x@y.example"})
    assert not exc.value.transient and exc.value.code == "unexpected_response"


def test_gohighlevel_mapping() -> None:
    lead = Lead(
        reference=1001,
        email_normalized="maya@harborview.example",
        first_name="Maya",
        last_name="Chen",
        company="Harborview",
        country="Canada",
        decision=Decision.QUALIFIED,
        score=85,
    )
    props = contact_properties(lead, "gohighlevel")
    assert props["firstName"] == "Maya" and props["companyName"] == "Harborview"
    assert props["lq_reference"] == "L-1001" and props["source"] == "lead-qualification"
    assert "country" not in props and "lifecyclestage" not in props
    assert set(GOHIGHLEVEL_MAP["custom"]) == {
        "lq_reference",
        "lq_decision",
        "lq_score",
        "lq_intent",
        "lq_interest",
    }


def test_settings_require_a_location_id() -> None:
    with pytest.raises(ValueError, match="LEAD_CRM_LOCATION_ID"):
        Settings(crm_provider="gohighlevel", crm_base_url="", environment="test")


@pytest.mark.parametrize(
    ("provider", "kind", "url"),
    [
        ("gohighlevel", GoHighLevelClient, DEFAULT_BASE_URL),
        ("hubspot", HubSpotStyleClient, "https://api.hubapi.com"),
        ("demo", HubSpotStyleClient, "http://crm-demo:8100"),
    ],
)
def test_make_client_picks_the_connector_and_its_default_url(
    provider: str, kind: type, url: str
) -> None:
    settings = Settings(
        crm_provider=provider,
        crm_base_url="",
        crm_location_id=LOCATION,
        environment="test",
    )
    client = crm_sync.make_client(settings, None)
    try:
        assert isinstance(client, kind)
        assert str(client.http.base_url) == url  # type: ignore[attr-defined]
        if provider == "gohighlevel":
            assert client.http.headers["Version"] == "2021-07-28"  # type: ignore[attr-defined]
    finally:
        client.close()
