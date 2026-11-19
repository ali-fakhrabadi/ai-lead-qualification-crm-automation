"""Intake, duplicates, idempotency, webhooks, authentication and roles (real PostgreSQL)."""

from __future__ import annotations

import json
import time
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, text

from app.services.webhooks import sign
from tests.integration.conftest import INTERNAL, lead_payload, login

pytestmark = pytest.mark.integration
SECRET = "partner-secret-for-tests"


def count(engine: Engine, sql: str) -> int:
    with engine.connect() as conn:
        return int(conn.execute(text(sql)).scalar_one())


def submit(api: TestClient, **overrides: Any) -> Any:
    return api.post("/api/v1/public/leads", json={**lead_payload(), **overrides})


def webhook(
    api: TestClient,
    body: dict[str, Any],
    *,
    secret: str = SECRET,
    ts: int | None = None,
    source: str = "partner-demo",
) -> Any:
    raw = json.dumps(body).encode()
    ts = int(time.time()) if ts is None else ts
    return api.post(
        f"/api/v1/webhooks/leads/{source}",
        content=raw,
        headers={
            "Content-Type": "application/json",
            "X-Lead-Timestamp": str(ts),
            "X-Lead-Signature": sign(secret, ts, raw),
        },
    )


# --- form intake --------------------------------------------------------------------------------
def test_form_creates_lead_event_and_outbox_in_one_transaction(
    api: TestClient, clean_db: Engine
) -> None:
    response = submit(api, submission_id="a" * 32)
    assert response.status_code == 202
    body = response.json()
    assert body == {
        "status": "accepted",
        "outcome": "created",
        "lead_reference": "L-1001",
        "correlation_id": body["correlation_id"],
        "replayed": False,
    }
    assert count(clean_db, "SELECT count(*) FROM leads WHERE status = 'new'") == 1
    assert count(clean_db, "SELECT count(*) FROM lead_events") == 1
    assert (
        count(clean_db, "SELECT count(*) FROM outbox_events WHERE event_type = 'lead.received'")
        == 1
    )
    with clean_db.connect() as conn:
        lead = conn.execute(
            text("SELECT email_normalized, company_domain, name_key FROM leads")
        ).one()
    assert lead == (
        "maya.chen@harborview-logistics.example",
        "harborview-logistics.example",
        "maya chen",
    )


def test_double_submit_with_the_same_submission_id_is_one_lead(
    api: TestClient, clean_db: Engine
) -> None:
    first = submit(api, submission_id="b" * 32)
    second = submit(api, submission_id="b" * 32)
    assert second.status_code == 202
    assert second.json()["status"] == "duplicate" and second.json()["replayed"] is True
    assert second.json()["lead_reference"] == first.json()["lead_reference"]
    assert count(clean_db, "SELECT count(*) FROM leads") == 1
    assert count(clean_db, "SELECT count(*) FROM outbox_events") == 1
    assert count(clean_db, "SELECT replay_count FROM lead_events") == 1


def test_same_email_identical_content_is_recorded_and_ignored(
    api: TestClient, clean_db: Engine
) -> None:
    submit(api)
    again = submit(api, email="  MAYA.CHEN@harborview-logistics.EXAMPLE ", submission_id="c" * 32)
    assert again.json()["outcome"] == "duplicate_ignored"
    assert count(clean_db, "SELECT count(*) FROM leads") == 1
    assert count(clean_db, "SELECT count(*) FROM lead_events") == 2
    assert count(clean_db, "SELECT count(*) FROM outbox_events") == 1


def test_same_email_new_inquiry_updates_and_reprocesses(api: TestClient, clean_db: Engine) -> None:
    submit(api)
    update = submit(
        api,
        message="Also interested in an AI assistant for our support inbox, starting next quarter.",
        interest="ai_assistant",
        submission_id="d" * 32,
    )
    assert update.json()["outcome"] == "updated_existing"
    with clean_db.connect() as conn:
        assert conn.execute(text("SELECT interest FROM leads")).scalar_one() == "ai_assistant"
    assert count(clean_db, "SELECT count(*) FROM outbox_events") == 2


def test_same_email_conflicting_fields_go_to_review_without_overwriting(
    api: TestClient, clean_db: Engine
) -> None:
    submit(api)
    response = submit(
        api,
        company="Totally Different Ltd",
        job_title="CFO",
        message="Different request: we need a data migration from our old CRM.",
        submission_id="e" * 32,
    )
    assert response.json()["outcome"] == "needs_review"
    with clean_db.connect() as conn:
        lead = conn.execute(text("SELECT company, job_title, review_reasons FROM leads")).one()
        review = conn.execute(text("SELECT kind, proposed FROM duplicate_reviews")).one()
    assert lead[0] == "Harborview Logistics" and lead[1] == "Operations Manager"
    assert "same_email_conflict" in lead[2]
    assert review[0] == "same_email_conflict"
    assert review[1] == {"company": "Totally Different Ltd", "job_title": "CFO"}


def test_possible_duplicate_person_is_flagged_never_merged(
    api: TestClient, clean_db: Engine
) -> None:
    submit(api)
    other = submit(api, email="m.chen@harborview-logistics.example", submission_id="f" * 32)
    assert other.json()["outcome"] == "created"
    assert count(clean_db, "SELECT count(*) FROM leads") == 2
    with clean_db.connect() as conn:
        review = conn.execute(text("SELECT kind, reasons, status FROM duplicate_reviews")).one()
    assert review == ("possible_same_person", ["same_name", "same_company_domain"], "open")


def test_similar_but_distinct_people_are_not_linked(api: TestClient, clean_db: Engine) -> None:
    submit(api)
    submit(
        api, first_name="Mia", email="mia.chen@harborview-logistics.example", submission_id="g" * 32
    )
    submit(
        api,
        email="maya.chen@another-company.example",
        company="Another Co",
        website="another-company.example",
        submission_id="h" * 32,
    )
    assert count(clean_db, "SELECT count(*) FROM leads") == 3
    assert count(clean_db, "SELECT count(*) FROM duplicate_reviews") == 0


def test_plus_tag_variant_is_flagged_as_possible_duplicate(
    api: TestClient, clean_db: Engine
) -> None:
    submit(api, email="maya@gmail.com", company="Independent", website=None)
    submit(
        api,
        email="maya+leads@gmail.com",
        company="Independent",
        website=None,
        first_name="M",
        submission_id="i" * 32,
    )
    with clean_db.connect() as conn:
        reasons = conn.execute(text("SELECT reasons FROM duplicate_reviews")).scalar_one()
    assert reasons == ["same_mailbox_different_plus_tag"]


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"email": "not-an-email"}, "email"),
        ({"consent_contact": False}, "consent_contact"),
        ({"interest": "space_travel"}, "interest"),
        ({"company": ""}, "company"),
        ({"website": "javascript:alert(1)"}, "website"),
        ({"first_name": "<script>x</script>"}, "first_name"),
        ({"message": "too short"}, "message"),
    ],
)
def test_invalid_input_is_rejected_with_the_field_and_nothing_stored(
    api: TestClient, clean_db: Engine, overrides: dict[str, Any], field: str
) -> None:
    response = submit(api, **overrides)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert field in json.dumps(error["details"])
    assert "not-an-email" not in response.text  # values are never echoed
    assert count(clean_db, "SELECT count(*) FROM leads") == 0


def test_unknown_fields_are_refused(api: TestClient) -> None:
    assert submit(api, is_admin=True).status_code == 422


def test_honeypot_submission_looks_accepted_but_is_not_stored(
    api: TestClient, clean_db: Engine
) -> None:
    response = submit(api, nickname="bot-filled")
    assert response.status_code == 202 and response.json()["lead_reference"] == "L-pending"
    assert count(clean_db, "SELECT count(*) FROM leads") == 0
    assert (
        count(clean_db, "SELECT count(*) FROM audit_events WHERE action = 'intake.spam_dropped'")
        == 1
    )


def test_oversized_body_is_refused(api: TestClient) -> None:
    response = api.post(
        "/api/v1/public/leads", content=b"x" * 40_000, headers={"Content-Type": "application/json"}
    )
    assert response.status_code == 413


def test_public_form_rate_limit(api: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.core.config import get_settings

    monkeypatch.setenv("LEAD_RATE_LIMIT_PUBLIC_PER_MINUTE", "2")
    get_settings.cache_clear()
    statuses = [submit(api, submission_id=f"{i:032d}").status_code for i in range(3)]
    assert statuses[:2] == [202, 202] and statuses[2] == 429
    get_settings.cache_clear()


def test_form_config_lists_interests_and_required_fields(api: TestClient) -> None:
    config = api.get("/api/v1/public/form-config").json()
    assert {"value": "crm_integration", "label": "CRM integration"} in config["interests"]
    assert "company" in config["required_fields"]


# --- signed webhooks ------------------------------------------------------------------------------
def event(event_id: str = "evt_partner_0001", **lead: Any) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "occurred_at": "2026-11-19T09:00:00Z",
        "lead": lead_payload(**lead),
    }


def test_signed_webhook_is_accepted(api: TestClient, clean_db: Engine) -> None:
    response = webhook(api, event())
    assert response.status_code == 202 and response.json()["outcome"] == "created"
    with clean_db.connect() as conn:
        row = conn.execute(text("SELECT source, channel, idempotency_key FROM lead_events")).one()
    assert row == ("partner-demo", "webhook", "evt_partner_0001")


def test_webhook_redelivery_is_deduplicated(api: TestClient, clean_db: Engine) -> None:
    webhook(api, event())
    again = webhook(api, event())
    assert again.status_code == 202 and again.json()["replayed"] is True
    assert count(clean_db, "SELECT count(*) FROM leads") == 1
    assert count(clean_db, "SELECT count(*) FROM outbox_events") == 1


def test_same_event_id_with_different_content_is_a_conflict(api: TestClient) -> None:
    webhook(api, event())
    response = webhook(api, event(company="Changed Inc"))
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "idempotency_conflict"


def test_bad_signature_expired_timestamp_and_unknown_source(
    api: TestClient, clean_db: Engine
) -> None:
    assert webhook(api, event(), secret="wrong").status_code == 401
    expired = webhook(api, event(), ts=int(time.time()) - 3600)
    assert expired.status_code == 401
    assert expired.json()["error"]["code"] == "expired_signature"
    assert webhook(api, event(), source="nobody").status_code == 404
    unsigned = api.post("/api/v1/webhooks/leads/partner-demo", json=event())
    assert unsigned.status_code == 401
    assert count(clean_db, "SELECT count(*) FROM leads") == 0
    assert (
        count(clean_db, "SELECT count(*) FROM audit_events WHERE action = 'webhook.rejected'") == 4
    )


def test_signed_but_invalid_payload_is_422(api: TestClient) -> None:
    assert webhook(api, {"event_id": "evt_partner_0002", "lead": {"email": "x"}}).status_code == 422
    assert webhook(api, event(email="nope")).status_code == 422


# --- authentication and roles
def test_login_refresh_rotation_and_reuse_detection(api: TestClient, make_user: Any) -> None:
    make_user("olivia@ops.example", "operator")
    first = api.post(
        "/api/v1/auth/login",
        json={"email": "olivia@ops.example", "password": "integration-password-1"},
    ).json()
    rotated = api.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert rotated.status_code == 200
    reuse = api.post("/api/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    assert reuse.status_code == 401
    newest = rotated.json()["refresh_token"]
    assert api.post("/api/v1/auth/refresh", json={"refresh_token": newest}).status_code == 401


def test_wrong_password_and_unknown_user_look_the_same(api: TestClient, make_user: Any) -> None:
    make_user("olivia@ops.example", "operator")
    a = api.post("/api/v1/auth/login", json={"email": "olivia@ops.example", "password": "nope"})
    b = api.post("/api/v1/auth/login", json={"email": "ghost@ops.example", "password": "nope"})
    assert a.status_code == b.status_code == 401
    assert a.json()["error"]["message"] == b.json()["error"]["message"]


def test_operator_endpoints_need_authentication(api: TestClient) -> None:
    for method, path in [
        ("GET", "/api/v1/leads"),
        ("GET", "/api/v1/stats"),
        ("GET", "/api/v1/audit-events"),
        ("GET", "/api/v1/duplicates"),
    ]:
        assert api.request(method, path).status_code == 401


def test_viewer_can_read_but_not_decide(
    api: TestClient, viewer: dict[str, str], clean_db: Engine
) -> None:
    submit(api)
    lead_id = api.get("/api/v1/leads", headers=viewer).json()["items"][0]["id"]
    assert api.get(f"/api/v1/leads/{lead_id}", headers=viewer).status_code == 200
    denied = [
        api.post(
            f"/api/v1/leads/{lead_id}/override",
            headers=viewer,
            json={"decision": "qualified", "reason": "looks good"},
        ),
        api.post(f"/api/v1/leads/{lead_id}/requalify", headers=viewer, json={"reason": "again"}),
        api.post(f"/api/v1/leads/{lead_id}/retry", headers=viewer),
        api.delete(f"/api/v1/leads/{lead_id}", headers=viewer),
        api.get("/api/v1/users", headers=viewer),
    ]
    assert [r.status_code for r in denied] == [403] * 5
    assert count(clean_db, "SELECT count(*) FROM audit_events WHERE action = 'authz.denied'") == 5


def test_operator_cannot_manage_users_or_delete(api: TestClient, operator: dict[str, str]) -> None:
    submit(api)
    lead_id = api.get("/api/v1/leads", headers=operator).json()["items"][0]["id"]
    assert api.delete(f"/api/v1/leads/{lead_id}", headers=operator).status_code == 403
    assert (
        api.post(
            "/api/v1/users",
            headers=operator,
            json={"email": "x@ops.example", "password": "long-enough-pass", "role": "admin"},
        ).status_code
        == 403
    )


def test_deactivated_user_loses_access_immediately(
    api: TestClient, admin: dict[str, str], make_user: Any
) -> None:
    make_user("olivia@ops.example", "operator")
    headers = login(api, "olivia@ops.example")
    users = api.get("/api/v1/users", headers=admin).json()
    olivia = next(u for u in users if u["email"] == "olivia@ops.example")
    api.patch(f"/api/v1/users/{olivia['id']}", headers=admin, json={"is_active": False})
    assert api.get("/api/v1/leads", headers=headers).status_code == 401


def test_internal_endpoints_need_the_service_token(
    api: TestClient, operator: dict[str, str]
) -> None:
    submit(api)
    lead_id = api.get("/api/v1/leads", headers=operator).json()["items"][0]["id"]
    path = f"/internal/v1/leads/{lead_id}/next-step"
    assert api.get(path).status_code == 401
    assert api.get(path, headers=operator).status_code == 401  # a user token is not enough
    assert api.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401
    assert api.get(path, headers=INTERNAL).json()["next"] == "qualify"


def test_admin_deletes_a_lead_and_its_data(
    api: TestClient, admin: dict[str, str], clean_db: Engine
) -> None:
    submit(api)
    lead_id = api.get("/api/v1/leads", headers=admin).json()["items"][0]["id"]
    assert api.delete(f"/api/v1/leads/{lead_id}", headers=admin).status_code == 204
    for table in ("leads", "lead_events", "outbox_events"):
        assert count(clean_db, f"SELECT count(*) FROM {table}") == 0  # noqa: S608
    with clean_db.connect() as conn:
        details = conn.execute(
            text("SELECT details FROM audit_events WHERE action = 'lead.deleted'")
        ).scalar_one()
    assert details == {"reference": "L-1001"}


def test_health_and_readiness(api: TestClient) -> None:
    assert api.get("/api/health").json()["status"] == "ok"
    assert api.get("/api/ready").json()["database"] == "ok"
