"""Create or update the lead's CRM contact, idempotently.

- The contact is found by email before anything is created, and a create that races with
  another one resolves to the existing id (409 handling), so retries and replays never create a
  second contact.
- Unchanged data is not sent again (payload hash).
- A note is added once per assessment.
- Transient failures leave the lead in ``retry_pending`` (stage ``crm_sync``) and answer 503 so
  n8n retries; permanent failures stop with 422 and need an operator.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.errors import AppError, NotFound, ServiceUnavailable
from app.crm.client import CrmError, HubSpotStyleClient, RetryPolicy
from app.crm.mapping import contact_properties, note_body
from app.db.models import (
    Assessment,
    CrmAttempt,
    CrmSync,
    Lead,
    LeadStatus,
    Stage,
    SyncStatus,
)
from app.services import audit, lifecycle

S = LeadStatus
SYNCABLE = frozenset({S.QUALIFIED, S.SYNCED_TO_CRM, S.RETRY_PENDING, S.FAILED})


def make_client(settings: Settings, record: Any, transport: Any = None) -> HubSpotStyleClient:
    base = (
        settings.crm_base_url
        if settings.crm_provider == "demo"
        else (settings.crm_base_url or "https://api.hubapi.com")
    )
    return HubSpotStyleClient(
        base,
        settings.crm_token.get_secret_value(),
        timeout_s=settings.crm_timeout_s,
        retry=RetryPolicy(
            settings.crm_max_attempts, settings.crm_backoff_base_s, settings.crm_backoff_cap_s
        ),
        record=record,
        transport=transport,
    )


def _hash(props: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(props, sort_keys=True).encode()).hexdigest()


def sync(
    db: Session,
    settings: Settings,
    lead_id: uuid.UUID,
    *,
    correlation_id: str,
    transport: Any = None,
) -> tuple[CrmSync, bool]:
    """Returns (sync record, changed)."""
    lead = db.get(Lead, lead_id, with_for_update=True)
    if lead is None:
        raise NotFound("Lead not found")
    if lead.status not in SYNCABLE or (
        lead.status in (S.RETRY_PENDING, S.FAILED) and lead.retry_stage is not Stage.CRM_SYNC
    ):
        raise AppError(
            f"A lead in status {lead.status.value} is not waiting for CRM sync",
            code="not_syncable",
            status_code=409,
        )
    provider = settings.crm_provider
    record = db.scalar(
        select(CrmSync).where(CrmSync.lead_id == lead.id, CrmSync.provider == provider)
    )
    if record is None:
        record = CrmSync(id=uuid.uuid4(), lead_id=lead.id, provider=provider)
        db.add(record)
        db.flush()
    props = contact_properties(lead)
    phash = _hash(props)
    needs_note = record.noted_assessment_id != lead.current_assessment_id
    if (
        record.status is SyncStatus.SYNCED
        and record.payload_hash == phash
        and record.remote_contact_id
        and not needs_note
    ):
        if lead.status is not S.SYNCED_TO_CRM:
            lifecycle.transition(lead, S.SYNCED_TO_CRM)
        return record, False

    assessment = (
        db.get(Assessment, lead.current_assessment_id) if lead.current_assessment_id else None
    )
    note = note_body(lead, assessment)
    email, company, domain = lead.email_normalized, lead.company, lead.company_domain
    known_contact, known_company = record.remote_contact_id, record.remote_company_id
    sync_id = record.id
    db.commit()  # release the lock during network calls

    attempts: list[CrmAttempt] = []

    def remember(op: str, outcome: str, status: int | None, ms: int, err: str | None) -> None:
        attempts.append(
            CrmAttempt(
                sync_id=sync_id,
                operation=op,
                outcome=outcome,
                http_status=status,
                duration_ms=ms,
                error=err,
            )
        )

    client = make_client(settings, remember, transport)
    contact_id, company_id = known_contact, known_company
    failure: CrmError | None = None
    try:
        contact_id = contact_id or client.find_contact(email)
        if contact_id:
            client.update_contact(contact_id, props)
        else:
            contact_id = client.create_contact(props)
        if domain:
            company_id = (
                company_id
                or client.find_company(domain)
                or client.create_company(company or domain, domain)
            )
            client.associate(contact_id, company_id)
        if needs_note:
            client.add_note(contact_id, note, datetime.now(UTC).isoformat())
    except CrmError as exc:
        failure = exc
    finally:
        client.close()

    lead = db.get(Lead, lead_id, with_for_update=True)
    record = db.get(CrmSync, sync_id)
    assert lead is not None
    assert record is not None
    db.add_all(attempts)
    record.attempts += len(attempts)
    record.remote_contact_id = contact_id or record.remote_contact_id
    record.remote_company_id = company_id or record.remote_company_id
    if failure is not None:
        record.status = (
            SyncStatus.RETRY_PENDING if failure.transient else SyncStatus.FAILED_PERMANENT
        )
        record.last_error_code = failure.code
        record.last_error = str(failure)[:300]
        lifecycle.transition(
            lead, S.RETRY_PENDING if failure.transient else S.FAILED, retry_stage=Stage.CRM_SYNC
        )
        audit.record(
            db,
            "crm.sync_failed",
            outcome="failure",
            lead_id=lead.id,
            correlation_id=correlation_id,
            details={
                "provider": provider,
                "code": failure.code,
                "transient": failure.transient,
                "http_status": failure.http_status,
                "attempts": len(attempts),
            },
        )
        db.commit()
        if failure.transient:
            raise ServiceUnavailable(
                "The CRM is temporarily unavailable; the sync will be retried",
                code="crm_unavailable",
                headers={"Retry-After": "15"},
            )
        raise AppError(
            "The CRM refused the record; an operator needs to check the configuration",
            code="crm_rejected",
            status_code=422,
            details={"crm_error": failure.code},
        )
    record.status = SyncStatus.SYNCED
    record.payload_hash = phash
    record.noted_assessment_id = lead.current_assessment_id
    record.last_error_code = record.last_error = None
    record.synced_at = datetime.now(UTC)
    if lead.status is not S.SYNCED_TO_CRM:
        lifecycle.transition(lead, S.SYNCED_TO_CRM)
    lead.synced_at = record.synced_at
    audit.record(
        db,
        "crm.synced",
        lead_id=lead.id,
        correlation_id=correlation_id,
        details={
            "provider": provider,
            "contact_id": record.remote_contact_id,
            "company_id": record.remote_company_id,
            "attempts": len(attempts),
        },
    )
    db.flush()
    return record, True
