#!/usr/bin/env python3
"""Build the n8n workflow exports in n8n/workflows/ from this definition and n8n/code/*.js.

The Code nodes' JavaScript lives in n8n/code/ (tested with `node --test n8n/tests`); this
script embeds it. Node ids are derived from names, so the output is the same every time and a
CI check fails when the committed JSON is out of date. No credential values are ever written:
nodes refer to credentials by id and name; n8n/setup/setup.mjs creates them from secrets.

    python3 scripts/build_workflows.py [--check]
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
CODE = ROOT / "n8n" / "code"
OUT = ROOT / "n8n" / "workflows"

PIPELINE_ID = "leadPipeline0001"
DELIVERY_ID = "leadFollowDeliv1"
ERRORS_ID = "leadErrorHandl01"
LEAD_PATH = "lead-events"
FOLLOW_UP_PATH = "follow-up-events"
DEFAULT_API_URL = "http://api:8000"

JWT = {"id": "leadDispatchJwt1", "name": "Lead dispatcher signature"}
API = {"id": "leadApiToken0001", "name": "Lead API service token"}
SMTP = {"id": "leadMailSink0001", "name": "Local mail sink (Mailpit)"}

_NAMESPACE = uuid.UUID("2b6c1f0e-8d4a-4c5e-9a71-0c3e5b7d9f21")
SETTINGS = "$('Settings').first().json"
EVENT = "$('Verify event').first().json"
TRUE = {"type": "boolean", "operation": "true", "singleValue": True}


def _id(name: str) -> str:
    return str(uuid.uuid5(_NAMESPACE, name))


def node(name: str, kind: str, version: float, position: tuple[int, int],
         parameters: dict[str, Any], **extra: Any) -> dict[str, Any]:
    return {
        "parameters": parameters,
        "id": _id(name),
        "name": name,
        "type": f"n8n-nodes-base.{kind}",
        "typeVersion": version,
        "position": list(position),
        **extra,
    }


def note(name: str, position: tuple[int, int], size: tuple[int, int], text: str,
         color: int = 7) -> dict[str, Any]:
    return node(name, "stickyNote", 1, position,
                {"content": text, "width": size[0], "height": size[1], "color": color})


def code(file: str, **replace: Any) -> str:
    text = (CODE / file).read_text(encoding="utf-8")
    for key, value in replace.items():
        text = text.replace(f"__{key}__", value if isinstance(value, str) else json.dumps(value))
    return text


def condition(name: str, left: str, operator: dict[str, Any], right: Any = "") -> dict[str, Any]:
    return {
        "options": {"caseSensitive": True, "leftValue": "", "typeValidation": "strict",
                    "version": 2},
        "conditions": [{"id": _id(name), "leftValue": left, "rightValue": right,
                        "operator": operator}],
        "combinator": "and",
    }


def switch(name: str, position: tuple[int, int], field: str, outputs: list[str]) -> dict[str, Any]:
    rules = [
        {
            "conditions": condition(f"{name}-{out}", f"={{{{ $json.{field} }}}}",
                                    {"type": "string", "operation": "equals"}, out),
            "renameOutput": True,
            "outputKey": out,
        }
        for out in outputs
    ]
    return node(name, "switch", 3.2, position,
                {"rules": {"values": rules}, "options": {"fallbackOutput": "extra"}})


def settings(position: tuple[int, int]) -> dict[str, Any]:
    values = {
        "api_url": DEFAULT_API_URL,
        "operator_email": "operators@northwind-automation.example",
        "mail_from": "lead-automation@northwind-automation.example",
        "dashboard_url": "http://localhost:8080",
    }
    return node("Settings", "set", 3.4, position, {
        "assignments": {"assignments": [
            {"id": _id(f"setting-{k}"), "name": k, "value": v, "type": "string"}
            for k, v in values.items()
        ]},
        "options": {},
    })


def api_call(name: str, position: tuple[int, int], method: str, path: str, body: str | None,
             *, timeout_ms: int, full_response: bool, retries: int = 0) -> dict[str, Any]:
    """HTTP call to the FastAPI internal API with the service token credential."""
    params: dict[str, Any] = {
        "method": method,
        "url": f"={{{{ {SETTINGS}.api_url }}}}{path}",
        "authentication": "genericCredentialType",
        "genericAuthType": "httpHeaderAuth",
        "options": {"timeout": timeout_ms},
    }
    if body is not None:
        params.update({"sendBody": True, "specifyBody": "json", "jsonBody": body})
    extra: dict[str, Any] = {"credentials": {"httpHeaderAuth": API}}
    if full_response:
        # The answer is classified by the next Code node: no exception on HTTP errors.
        params["options"]["response"] = {"response": {"fullResponse": True, "neverError": True}}
        extra["onError"] = "continueRegularOutput"
    if retries:
        extra.update({"retryOnFail": True, "maxTries": retries, "waitBetweenTries": 2000})
    return node(name, "httpRequest", 4.2, position, params, **extra)


def wait(name: str, position: tuple[int, int]) -> dict[str, Any]:
    return node(name, "wait", 1.1, position,
                {"resume": "timeInterval", "amount": "={{ $json.wait_s }}", "unit": "seconds"},
                webhookId=_id(f"{name}-webhook"))


def entry_nodes(prefix: str, webhook: str, path: str, event_types: list[str],
                y: int) -> list[dict[str, Any]]:
    return [
        node(webhook, "webhook", 2.1, (0, y), {
            "httpMethod": "POST", "path": path, "authentication": "jwtAuth",
            "responseMode": "responseNode", "options": {},
        }, webhookId=_id(f"{prefix}-webhook-id"), credentials={"jwtAuth": JWT}),
        settings((220, y)),
        node("Verify event", "code", 2, (440, y),
             {"jsCode": code("verify_event.js", WEBHOOK_NODE=webhook, EVENT_TYPES=event_types)}),
        node("Valid?", "if", 2.2, (660, y),
             {"conditions": condition(f"{prefix}-valid", "={{ $json.valid }}", TRUE),
              "options": {}}),
        node("Accept", "respondToWebhook", 1.1, (880, y - 100), {
            "respondWith": "json", "responseBody": '{"accepted": true}',
            "options": {"responseCode": 202},
        }),
        node("Reject", "respondToWebhook", 1.1, (880, y + 120), {
            "respondWith": "json",
            "responseBody": "={{ JSON.stringify({ error: $json.reason }) }}",
            "options": {"responseCode": 400},
        }),
        api_call("Link execution", (1100, y - 100), "POST",
                 f"/internal/v1/events/{{{{ {EVENT}.event_id }}}}/started",
                 "={{ JSON.stringify({ execution_id: String($execution.id) }) }}",
                 timeout_ms=10000, full_response=False, retries=3),
    ]


def step_nodes(label: str, http_name: str, plan: str, outcome: str, waiter: str,
               method_path: str, timeout_ms: int, max_attempts: int, base_wait: int,
               x: int, y: int, body: str = "={}") -> list[dict[str, Any]]:
    return [
        api_call(http_name, (x, y), "POST", method_path, body, timeout_ms=timeout_ms,
                 full_response=True),
        node(plan, "code", 2, (x + 220, y), {"jsCode": code(
            "plan_retry.js", STEP=label, MAX_ATTEMPTS=str(max_attempts),
            BASE_WAIT_S=str(base_wait))}),
        switch(outcome, (x + 440, y), "route", ["ok", "retry"]),
        wait(waiter, (x + 440, y + 220)),
    ]


def report_nodes(stages: list[str], x: int, y: int) -> list[dict[str, Any]]:
    return [
        node("Summarize run", "code", 2, (x, y), {"jsCode": code("summarize_run.js",
                                                                 STAGES=stages)}),
        api_call("Report outcome", (x + 220, y), "POST",
                 "/internal/v1/events/{{ $json.event_id }}/outcome",
                 "={{ JSON.stringify($json.report) }}", timeout_ms=10000, full_response=False,
                 retries=5),
    ]


def _to(*targets: str) -> list[dict[str, Any]]:
    return [{"node": t, "type": "main", "index": 0} for t in targets]


# --- 1. lead pipeline ---------------------------------------------------------------------------
def pipeline() -> dict[str, Any]:
    lead = f"{{{{ {EVENT}.lead_id }}}}"
    nodes = [
        note("Note: intake", (-40, -240), (1320, 220),
             "## 1. Lead intake orchestration\nFastAPI stores each lead and an outbox event in "
             "one transaction; the dispatcher posts the event here with a short-lived signed "
             "token. **Verify event** checks the token was issued for exactly this payload. "
             "The run is linked to the event, then the API says which step the lead needs "
             "next, so replays and resumed events continue where the lead stands."),
        *entry_nodes("pipeline", "Lead event webhook", LEAD_PATH,
                     ["lead.received", "lead.resume"], 200),
        api_call("Next step", (1320, 100), "GET",
                 f"/internal/v1/leads/{lead}/next-step", None, timeout_ms=10000,
                 full_response=False, retries=3),
        switch("Route next step", (1540, 100), "next",
               ["qualify", "crm_sync", "follow_up_draft"]),
        note("Note: qualification", (1760, -420), (1100, 230),
             "## 2. AI qualification\nFastAPI calls the configured model (Ollama by default) "
             "with business fields only, validates the JSON against a strict schema and "
             "applies deterministic rules: the model never decides alone. 503 (model busy or "
             "down) is retried with exponential backoff and jitter; the lead is never lost."),
        *step_nodes("qualify", "Qualify lead", "Plan qualification retry",
                    "Qualification outcome", "Wait before qualification retry",
                    f"/internal/v1/leads/{lead}/qualify", 600000, 3, 20, 1760, -160,
                    body=f"={{{{ JSON.stringify({{ force: {EVENT}.data.force === true }}) }}}}"),
        switch("After qualification", (2420, -260), "next", ["crm_sync", "follow_up_draft"]),
        note("Note: crm", (1760, 240), (1100, 200),
             "## 3. CRM upsert\nCreate or update the contact (found by email first, so no "
             "duplicates), company and qualification note. Transient CRM errors (429/5xx) "
             "are retried; permanent ones (4xx) stop and show the lead to an operator."),
        *step_nodes("crm_sync", "Sync CRM contact", "Plan CRM retry", "CRM outcome",
                    "Wait before CRM retry", f"/internal/v1/leads/{lead}/crm-sync", 120000, 3,
                    10, 1760, 480),
        switch("After CRM", (2420, 380), "next", ["follow_up_draft"]),
        note("Note: draft", (2640, -40), (1100, 200),
             "## 4. Follow-up draft and human approval\nA draft reply is generated and waits "
             "for a person to edit, approve or reject it. Nothing is sent here: n8n only "
             "notifies the operators' mailbox (the local Mailpit sink in the demo)."),
        *step_nodes("follow_up_draft", "Create follow-up draft", "Plan draft retry",
                    "Draft outcome", "Wait before draft retry",
                    f"/internal/v1/leads/{lead}/follow-up-draft", 600000, 3, 20, 2640, 200),
        node("New draft?", "if", 2.2, (3300, 120), {
            "conditions": condition("new-draft", "={{ $json.detail.created }}", TRUE),
            "options": {},
        }),
        node("Notify operators", "emailSend", 2.1, (3520, 40), {
            "fromEmail": f"={{{{ {SETTINGS}.mail_from }}}}",
            "toEmail": f"={{{{ {SETTINGS}.operator_email }}}}",
            "subject": "=Follow-up draft ready for review: {{ $json.detail.reference }}",
            "emailFormat": "text",
            "text": (
                "=A follow-up draft for lead {{ $json.detail.reference }} is waiting for "
                f"review.\n\nOpen the dashboard: {{{{ {SETTINGS}.dashboard_url }}}}/leads/"
                f"{{{{ {EVENT}.lead_id }}}}\n\nNothing has been sent to the lead."
            ),
            "options": {"appendAttribution": False},
        }, credentials={"smtp": SMTP}, onError="continueRegularOutput"),
        note("Note: report", (3740, 260), (520, 200),
             "## 5. Report\nThe outcome goes back to the API: completed, stopped on purpose "
             "(review needed, permanent error) or failed after retries (the event is "
             "delivered again later). Unexpected failures go to the error workflow."),
        *report_nodes(["Plan draft retry", "Plan CRM retry", "Plan qualification retry"],
                      3760, 520),
    ]
    summarize = _to("Summarize run")
    connections = {
        "Lead event webhook": {"main": [_to("Settings")]},
        "Settings": {"main": [_to("Verify event")]},
        "Verify event": {"main": [_to("Valid?")]},
        "Valid?": {"main": [_to("Accept"), _to("Reject")]},
        "Accept": {"main": [_to("Link execution")]},
        "Link execution": {"main": [_to("Next step")]},
        "Next step": {"main": [_to("Route next step")]},
        "Route next step": {"main": [_to("Qualify lead"), _to("Sync CRM contact"),
                                     _to("Create follow-up draft"), summarize]},
        "Qualify lead": {"main": [_to("Plan qualification retry")]},
        "Plan qualification retry": {"main": [_to("Qualification outcome")]},
        "Qualification outcome": {"main": [_to("After qualification"),
                                           _to("Wait before qualification retry"), summarize]},
        "Wait before qualification retry": {"main": [_to("Qualify lead")]},
        "After qualification": {"main": [_to("Sync CRM contact"), _to("Create follow-up draft"),
                                         summarize]},
        "Sync CRM contact": {"main": [_to("Plan CRM retry")]},
        "Plan CRM retry": {"main": [_to("CRM outcome")]},
        "CRM outcome": {"main": [_to("After CRM"), _to("Wait before CRM retry"), summarize]},
        "Wait before CRM retry": {"main": [_to("Sync CRM contact")]},
        "After CRM": {"main": [_to("Create follow-up draft"), summarize]},
        "Create follow-up draft": {"main": [_to("Plan draft retry")]},
        "Plan draft retry": {"main": [_to("Draft outcome")]},
        "Draft outcome": {"main": [_to("New draft?"), _to("Wait before draft retry"), summarize]},
        "Wait before draft retry": {"main": [_to("Create follow-up draft")]},
        "New draft?": {"main": [_to("Notify operators"), summarize]},
        "Notify operators": {"main": [summarize]},
        "Summarize run": {"main": [_to("Report outcome")]},
    }
    return _workflow(PIPELINE_ID, "Lead pipeline: intake, AI qualification, CRM, follow-up draft",
                     nodes, connections)


# --- 2. follow-up delivery ----------------------------------------------------------------------
def delivery() -> dict[str, Any]:
    nodes = [
        note("Note: delivery", (-40, -240), (1540, 220),
             "## Follow-up delivery (after human approval)\nRuns only when an operator has "
             "approved a draft. FastAPI delivers it to the configured channel: the local "
             "Mailpit sink unless real sending is explicitly enabled. Delivery is idempotent: "
             "a replayed event never sends twice. Transient failures are retried."),
        *entry_nodes("delivery", "Follow-up event webhook", FOLLOW_UP_PATH,
                     ["follow_up.approved"], 200),
        *step_nodes("deliver", "Deliver follow-up", "Plan delivery retry", "Delivery outcome",
                    "Wait before delivery retry",
                    f"/internal/v1/follow-ups/{{{{ {EVENT}.data.draft_id }}}}/deliver", 60000,
                    3, 10, 1320, 100),
        *report_nodes(["Plan delivery retry"], 2000, 100),
    ]
    summarize = _to("Summarize run")
    connections = {
        "Follow-up event webhook": {"main": [_to("Settings")]},
        "Settings": {"main": [_to("Verify event")]},
        "Verify event": {"main": [_to("Valid?")]},
        "Valid?": {"main": [_to("Accept"), _to("Reject")]},
        "Accept": {"main": [_to("Link execution")]},
        "Link execution": {"main": [_to("Deliver follow-up")]},
        "Deliver follow-up": {"main": [_to("Plan delivery retry")]},
        "Plan delivery retry": {"main": [_to("Delivery outcome")]},
        "Delivery outcome": {"main": [summarize, _to("Wait before delivery retry"), summarize]},
        "Wait before delivery retry": {"main": [_to("Deliver follow-up")]},
        "Summarize run": {"main": [_to("Report outcome")]},
    }
    return _workflow(DELIVERY_ID, "Follow-up delivery after approval", nodes, connections)


# --- 3. error handler ---------------------------------------------------------------------------
def errors() -> dict[str, Any]:
    nodes = [
        note("Note: errors", (-40, -220), (900, 200),
             "## Error handling and replay\nAny unexpected failure of the two workflows lands "
             "here. The API maps the execution to its event, marks the lead `retry_pending` "
             "at the stage it reached and schedules the event again with backoff (dead "
             "after the maximum attempts). Operators can also replay from the dashboard."),
        node("Workflow failed", "errorTrigger", 1, (0, 100), {}),
        settings((220, 100)),
        node("Error details", "code", 2, (440, 100), {"jsCode": code("error_details.js")}),
        api_call("Report workflow error", (660, 100), "POST", "/internal/v1/workflow-errors",
                 "={{ JSON.stringify($('Error details').first().json) }}", timeout_ms=10000,
                 full_response=False, retries=5),
    ]
    connections = {
        "Workflow failed": {"main": [_to("Settings")]},
        "Settings": {"main": [_to("Error details")]},
        "Error details": {"main": [_to("Report workflow error")]},
    }
    workflow = _workflow(ERRORS_ID, "Error handler: record and schedule retry", nodes,
                         connections)
    workflow["settings"].pop("errorWorkflow")
    return workflow


def _workflow(wid: str, name: str, nodes: list[dict[str, Any]],
              connections: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": wid,
        "name": name,
        "nodes": nodes,
        "connections": connections,
        "active": False,
        "settings": {
            "executionOrder": "v1",
            "executionTimeout": 1800,
            "errorWorkflow": ERRORS_ID,
            "saveDataSuccessExecution": "all",
            "saveDataErrorExecution": "all",
            "saveManualExecutions": True,
        },
        "pinData": {},
        "tags": [],
    }


WORKFLOWS = {
    "lead-pipeline.json": pipeline,
    "follow-up-delivery.json": delivery,
    "error-handler.json": errors,
}


def render(builder: Any) -> str:
    return json.dumps(builder(), indent=2, ensure_ascii=False) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--check", action="store_true", help="fail if the JSON is out of date")
    args = parser.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    stale = []
    for file, builder in WORKFLOWS.items():
        text = render(builder)
        target = OUT / file
        if args.check:
            if not target.exists() or target.read_text(encoding="utf-8") != text:
                stale.append(file)
        else:
            target.write_text(text, encoding="utf-8", newline="\n")
            print(f"wrote {target.relative_to(ROOT)}")
    if stale:
        print(f"out of date: {', '.join(stale)} (run scripts/build_workflows.py)",
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
