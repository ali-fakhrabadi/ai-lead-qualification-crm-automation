"""Internal endpoints called by n8n (service token). Not routed by the public proxy.

Every step is idempotent and answers with the next step, so n8n's retries, the dispatcher's
redelivery and operator replays never duplicate side effects. Status codes tell n8n what to do:
503 = transient, retry with backoff; 409/422 = permanent, stop and report.
"""

from __future__ import annotations

import hmac
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Body, Depends, Header
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.core.deps import correlation_id, get_db, require_internal
from app.core.errors import NotFound
from app.db.models import FaultRule, Lead, OutboxEvent, OutboxStatus, Stage
from app.dispatcher.main import backoff
from app.schemas import (
    ExecutionStarted,
    FaultRuleIn,
    OutcomeReport,
    StepResult,
    WorkflowError,
)
from app.services import audit, crm_sync, followups, lifecycle, pipeline, qualification

router = APIRouter(dependencies=[Depends(require_internal)], include_in_schema=True)
Db = Annotated[Session, Depends(get_db)]
Cid = Annotated[str, Depends(correlation_id)]


def _lead(db: Session, lead_id: uuid.UUID) -> Lead:
    lead = db.get(Lead, lead_id)
    if lead is None:
        raise NotFound("Lead not found")
    return lead


def _result(lead: Lead, settings: Settings, **detail: object) -> StepResult:
    step = pipeline.next_step(lead, settings)
    nxt = "stop" if step == "qualify" else step
    return StepResult(
        lead_id=lead.id,
        status=lead.status.value,
        next=nxt,
        detail={"reference": f"L-{lead.reference}", **detail},
    )


@router.get("/leads/{lead_id}/next-step")
def next_step(
    lead_id: uuid.UUID, db: Db, settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    lead = _lead(db, lead_id)
    return {
        "lead_id": str(lead.id),
        "status": lead.status.value,
        "next": pipeline.next_step(lead, settings),
        "reference": f"L-{lead.reference}",
    }


@router.post("/leads/{lead_id}/qualify", response_model=StepResult)
def qualify(
    lead_id: uuid.UUID,
    db: Db,
    cid: Cid,
    force: Annotated[bool, Body(embed=True)] = False,
    settings: Settings = Depends(get_settings),
) -> StepResult:
    outcome = qualification.qualify(db, settings, lead_id, correlation_id=cid, force=force)
    a = outcome.assessment
    return _result(
        outcome.lead,
        settings,
        reused=outcome.reused,
        decision=a.decision,
        score=a.score,
        assessment_status=a.status.value,
        provider=a.provider,
    )


@router.post("/leads/{lead_id}/crm-sync", response_model=StepResult)
def sync_crm(
    lead_id: uuid.UUID, db: Db, cid: Cid, settings: Settings = Depends(get_settings)
) -> StepResult:
    record, changed = crm_sync.sync(db, settings, lead_id, correlation_id=cid)
    lead = _lead(db, lead_id)
    return _result(lead, settings, changed=changed, remote_contact_id=record.remote_contact_id)


@router.post("/leads/{lead_id}/follow-up-draft", response_model=StepResult)
def follow_up_draft(
    lead_id: uuid.UUID, db: Db, cid: Cid, settings: Settings = Depends(get_settings)
) -> StepResult:
    draft, created = followups.create_draft(db, settings, lead_id, correlation_id=cid)
    lead = _lead(db, lead_id)
    result = _result(
        lead, settings, created=created, draft_id=str(draft.id), generated_by=draft.generated_by
    )
    result.next = "done"
    return result


@router.post("/follow-ups/{draft_id}/deliver", response_model=StepResult)
def deliver(
    draft_id: uuid.UUID, db: Db, cid: Cid, settings: Settings = Depends(get_settings)
) -> StepResult:
    draft, delivered = followups.deliver(db, settings, draft_id, cid)
    lead = _lead(db, draft.lead_id)
    result = _result(lead, settings, delivered_now=delivered, channel=draft.delivery_channel)
    result.next = "done"
    return result


@router.post("/events/{event_id}/outcome")
def report_outcome(
    event_id: uuid.UUID, body: OutcomeReport, db: Db, settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    event = db.get(OutboxEvent, event_id, with_for_update=True)
    if event is None:
        raise NotFound("Event not found")
    now = datetime.now(UTC)
    event.workflow_execution_id = body.execution_id
    event.workflow_status = body.status
    event.workflow_duration_ms = body.duration_ms
    if body.status in ("success", "stopped"):
        event.status = OutboxStatus.COMPLETED
        event.completed_at = now
    elif event.attempts >= settings.dispatch_max_attempts:
        event.status = OutboxStatus.DEAD
        event.last_error = (body.error or "workflow failed")[:300]
    else:
        # Retry the whole run later; finished steps are skipped thanks to idempotency.
        event.status = OutboxStatus.FAILED
        event.last_error = (body.error or "workflow failed")[:300]
        event.next_attempt_at = now + max(backoff(settings, event.attempts), timedelta(seconds=30))
    audit.record(
        db,
        f"workflow.{body.status}",
        actor_type="n8n",
        lead_id=event.lead_id,
        correlation_id=event.correlation_id,
        details={
            "event_id": str(event.id),
            "execution_id": body.execution_id,
            "step": body.step,
            "duration_ms": body.duration_ms,
        },
    )
    return {"event_status": event.status.value}


@router.post("/events/{event_id}/started")
def event_started(event_id: uuid.UUID, body: ExecutionStarted, db: Db) -> dict[str, str]:
    """n8n links its execution to the event first, so failures can be traced back."""
    event = db.get(OutboxEvent, event_id, with_for_update=True)
    if event is None:
        raise NotFound("Event not found")
    event.workflow_execution_id = body.execution_id
    event.workflow_status = "running"
    return {"event_status": event.status.value}


@router.post("/workflow-errors")
def workflow_error(
    body: WorkflowError, db: Db, settings: Settings = Depends(get_settings)
) -> dict[str, str]:
    """Called by the n8n error workflow when an execution fails outside the normal paths.

    The failed execution's event is scheduled for another delivery (with backoff) and its lead
    is marked ``retry_pending`` at the stage it had reached.
    """
    event = None
    if body.execution_id:
        event = db.scalar(
            select(OutboxEvent)
            .where(OutboxEvent.workflow_execution_id == body.execution_id)
            .with_for_update()
        )
    if event is not None and event.status in (OutboxStatus.DISPATCHED, OutboxStatus.PENDING):
        now = datetime.now(UTC)
        event.workflow_status = "error"
        event.last_error = (body.message or "workflow error")[:300]
        if event.attempts >= settings.dispatch_max_attempts:
            event.status = OutboxStatus.DEAD
        else:
            event.status = OutboxStatus.FAILED
            event.next_attempt_at = now + max(
                backoff(settings, event.attempts), timedelta(seconds=30)
            )
    lead_id = body.lead_id or (event.lead_id if event is not None else None)
    lead = db.get(Lead, lead_id, with_for_update=True) if lead_id else None
    if lead is not None and lifecycle.can_transition(lead.status, lead.status.RETRY_PENDING):
        stage = {
            "qualify": Stage.QUALIFICATION,
            "crm_sync": Stage.CRM_SYNC,
            "follow_up_draft": Stage.FOLLOW_UP_DRAFT,
        }.get(pipeline.next_step(lead, get_settings()))
        if stage is not None:
            lifecycle.transition(lead, lead.status.RETRY_PENDING, retry_stage=stage)
    audit.record(
        db,
        "workflow.error",
        outcome="failure",
        actor_type="n8n",
        lead_id=lead.id if lead else None,
        details={
            "execution_id": body.execution_id,
            "workflow": body.workflow,
            "node": body.node,
            "message": (body.message or "")[:300],
            "event_id": str(event.id) if event is not None else None,
        },
    )
    return {"recorded": "true"}


# --- evaluation/test only -------------------------------------------------------------------
def _fault_admin(settings: Settings, token: str | None) -> None:
    expected = settings.fault_admin_token.get_secret_value()
    if (
        not settings.fault_injection_enabled
        or not expected
        or not hmac.compare_digest((token or "").encode(), expected.encode())
    ):
        raise NotFound("Not found")


@router.post("/test/fault-rules", status_code=201, include_in_schema=False)
def add_fault(
    body: FaultRuleIn,
    db: Db,
    settings: Settings = Depends(get_settings),
    token: str | None = Header(None, alias="X-Fault-Admin-Token"),
) -> dict[str, str]:
    _fault_admin(settings, token)
    rule = FaultRule(
        target=body.target,
        mode=body.mode,
        email_normalized=str(body.email).strip().lower(),
        remaining=body.remaining,
    )
    db.add(rule)
    db.flush()
    return {"id": str(rule.id)}


@router.delete("/test/fault-rules", status_code=204, include_in_schema=False)
def clear_faults(
    db: Db,
    settings: Settings = Depends(get_settings),
    token: str | None = Header(None, alias="X-Fault-Admin-Token"),
) -> None:
    _fault_admin(settings, token)
    db.execute(delete(FaultRule))
