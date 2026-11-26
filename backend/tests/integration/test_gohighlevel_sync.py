"""The CRM sync step with LEAD_CRM_PROVIDER=gohighlevel, against the local GoHighLevel mock.

Same API calls n8n makes, real PostgreSQL; only the CRM is the mock (tests/mocks/gohighlevel.py).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from tests.integration.test_pipeline import create_lead, one, step
from tests.mocks.gohighlevel import create_app

pytestmark = pytest.mark.integration
LOCATION = "loc-integration-1"


@pytest.fixture
def ghl(clean_db: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    from app.core.config import get_settings
    from app.services import crm_sync

    settings = get_settings()
    monkeypatch.setattr(settings, "crm_provider", "gohighlevel")
    monkeypatch.setattr(settings, "crm_location_id", LOCATION)
    mock = TestClient(create_app(token=settings.crm_token.get_secret_value(), location_id=LOCATION))
    original = crm_sync.make_client

    def make_client(s: Any, record: Any, _transport: Any = None) -> Any:
        return original(s, record, mock._transport)

    monkeypatch.setattr(crm_sync, "make_client", make_client)
    with mock:
        yield mock


def stats(mock: TestClient) -> dict[str, Any]:
    return mock.get("/_admin/stats").json()  # type: ignore[no-any-return]


def test_sync_creates_contact_business_link_and_note_once(
    api: TestClient, clean_db: Engine, ghl: TestClient
) -> None:
    lead_id = create_lead(api)
    step(api, lead_id, "qualify")
    first = step(api, lead_id, "crm-sync").json()
    assert first["status"] == "synced_to_crm" and first["detail"]["changed"] is True
    again = step(api, lead_id, "crm-sync").json()
    assert again["detail"]["changed"] is False
    counts = stats(ghl)
    assert counts["contacts"] == 1 and counts["businesses"] == 1 and counts["notes"] == 1
    assert counts["duplicate_emails"] == 0
    contact = next(iter(ghl.app.state.contacts.values()))  # type: ignore[attr-defined]
    assert contact["businessId"] == next(iter(ghl.app.state.businesses))  # type: ignore[attr-defined]
    assert contact["customFields"]["lq_decision"] == "qualified"
    assert contact["source"] == "lead-qualification"
    assert one(clean_db, "SELECT provider, status, remote_contact_id FROM crm_syncs") == (
        "gohighlevel",
        "synced",
        contact["id"],
    )


def test_requalification_updates_the_same_contact_and_adds_a_note(
    api: TestClient, clean_db: Engine, ghl: TestClient
) -> None:
    lead_id = create_lead(api)
    step(api, lead_id, "qualify")
    step(api, lead_id, "crm-sync")
    step(api, lead_id, "qualify", force=True)
    step(api, lead_id, "crm-sync")
    counts = stats(ghl)
    assert counts["contacts"] == 1 and counts["notes"] == 2 and counts["businesses"] == 1


def test_rate_limited_then_recovers_without_an_operator(
    api: TestClient, clean_db: Engine, ghl: TestClient
) -> None:
    lead_id = create_lead(api)
    step(api, lead_id, "qualify")
    ghl.post(
        "/_admin/faults",
        json={"operation": "upsert_contact", "status": 429, "remaining": 2, "retry_after": 0},
    )
    body = step(api, lead_id, "crm-sync").json()
    assert body["status"] == "synced_to_crm"
    assert one(clean_db, "SELECT count(*) FROM crm_attempts WHERE outcome = 'transient'")[0] == 2


def test_outage_answers_503_and_the_retry_creates_no_duplicate(
    api: TestClient, clean_db: Engine, ghl: TestClient
) -> None:
    lead_id = create_lead(api)
    step(api, lead_id, "qualify")
    ghl.post("/_admin/faults", json={"operation": "create_note", "status": 502, "remaining": 3})
    failed = step(api, lead_id, "crm-sync")
    assert failed.status_code == 503 and failed.json()["error"]["code"] == "crm_unavailable"
    assert one(clean_db, "SELECT status, retry_stage FROM leads") == ("retry_pending", "crm_sync")
    retried = step(api, lead_id, "crm-sync").json()
    assert retried["status"] == "synced_to_crm"
    counts = stats(ghl)
    assert counts["contacts"] == 1 and counts["notes"] == 1 and counts["duplicate_emails"] == 0


def test_wrong_location_stops_for_an_operator(
    api: TestClient, clean_db: Engine, ghl: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "crm_location_id", "another-location")
    lead_id = create_lead(api)
    step(api, lead_id, "qualify")
    response = step(api, lead_id, "crm-sync")
    assert response.status_code == 422
    assert response.json()["error"]["details"]["crm_error"] == "forbidden"
    assert one(clean_db, "SELECT status, retry_stage FROM leads") == ("failed", "crm_sync")
    assert stats(ghl)["contacts"] == 0
