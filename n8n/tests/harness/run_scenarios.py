"""n8n workflow harness: run the exported workflows in a disposable n8n and check what they do.

This container plays the dispatcher: it signs each event exactly like the real one (HS256
token bound to the event id and the SHA-256 of the payload) and posts it to n8n's webhook.
The workflows call the scriptable stub API (stub_api.py), which records every call. Each
scenario then checks the calls, the outcome the workflow reported, the operator
notification in the mail sink, and the execution status n8n itself persisted.

    n8n/tests/harness/run.sh       # builds nothing; starts the disposable stack, runs this

Writes /out/n8n-harness-report.json and exits non-zero if any check failed.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

N8N = os.environ.get("HARNESS_N8N_URL", "http://n8n:5678")
STUB = os.environ.get("HARNESS_STUB_URL", "http://api:8000")
MAILPIT = os.environ.get("HARNESS_MAILPIT_URL", "http://mailpit:8025")
OWNER = os.environ.get("HARNESS_OWNER_EMAIL", "owner@example.com")
OUT = Path(os.environ.get("HARNESS_OUT", "/out"))
TIMEOUT_S = int(os.environ.get("HARNESS_TIMEOUT_S", "240"))
BROWSER_ID = str(uuid.uuid4())


def secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


def http(
    method: str,
    url: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
) -> tuple[int, Any, dict[str, str]]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(  # noqa: S310 - fixed harness URLs
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            text, status, hdrs = resp.read().decode(), resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        text, status, hdrs = exc.read().decode(), exc.code, dict(exc.headers.items())
    try:
        return status, json.loads(text) if text.strip() else None, hdrs
    except json.JSONDecodeError:
        return status, text, hdrs


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token(claims: dict[str, Any], key: str) -> str:
    head = b64(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b64(json.dumps(claims).encode())
    sig = hmac.new(key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{b64(sig)}"


class Event:
    """One outbox event, as the dispatcher would deliver it."""

    def __init__(self, event_type: str, lead_id: str, data: dict[str, Any] | None = None) -> None:
        self.id = str(uuid.uuid4())
        self.envelope = {
            "event_id": self.id,
            "event_type": event_type,
            "lead_id": lead_id,
            "correlation_id": f"harness-{self.id[:8]}",
            "attempt": 1,
            "created_at": datetime.now(UTC).isoformat(),
            "data": data or {},
        }
        self.path = "follow-up-events" if event_type == "follow_up.approved" else "lead-events"

    def send(self, *, key: str | None = None, exp_offset: int = 300, tamper: bool = False) -> int:
        payload = json.dumps(self.envelope, separators=(",", ":"))
        now = int(time.time())
        claims = {
            "iss": "lead-automation-dispatcher",
            "aud": "n8n",
            "iat": now,
            "exp": now + exp_offset,
            "eid": self.id,
            "payload_sha256": hashlib.sha256(payload.encode()).hexdigest(),
        }
        sent = payload.replace('"attempt":1', '"attempt":2') if tamper else payload
        status, _, _ = http(
            "POST",
            f"{N8N}/webhook/{self.path}",
            {"payload": sent},
            {"Authorization": f"Bearer {token(claims, key or secret('dispatch_secret'))}"},
        )
        return status


def stub(path: str, body: Any = None) -> Any:
    return http("POST" if body is not None else "GET", f"{STUB}/_stub/{path}", body)[1]


def script(step: str, key: str, *responses: dict[str, Any]) -> None:
    stub("script", {"step": step, "key": key, "responses": list(responses)})


def calls(step: str | None = None, key: str | None = None) -> list[dict[str, Any]]:
    return [
        c
        for c in stub("calls")["calls"]
        if (step is None or c["step"] == step) and (key is None or c["key"] == key)
    ]


def wait_for_end(event: Event) -> dict[str, Any]:
    """The run's reported outcome, or the error workflow's report for its execution."""
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline:
        outcome = calls("outcome", event.id)
        started = calls("started", event.id)
        execution = started[-1]["body"]["execution_id"] if started else None
        errors = [
            c
            for c in calls("workflow_error")
            if execution and c["body"].get("execution_id") == execution
        ]
        if outcome or errors:
            return {
                "execution_id": execution,
                "outcome": outcome[-1]["body"] if outcome else None,
                "workflow_error": errors[-1]["body"] if errors else None,
            }
        time.sleep(1)
    return {"execution_id": None, "outcome": None, "workflow_error": None, "timed_out": True}


class N8nApi:
    """n8n's own record of an execution (the editor's REST API, signed in as the owner)."""

    def __init__(self) -> None:
        status, _, headers = http(
            "POST",
            f"{N8N}/rest/login",
            {"emailOrLdapLoginId": OWNER, "password": secret("n8n_owner_password")},
            {"browser-id": BROWSER_ID},
        )
        cookie = headers.get("Set-Cookie") or headers.get("set-cookie") or ""
        self.cookie = cookie.split(";", 1)[0]
        self.ok = status == 200 and self.cookie.startswith("n8n-auth=")

    def status(self, execution_id: str | None) -> str | None:
        if not (self.ok and execution_id):
            return None
        for _ in range(30):
            code, body, _ = http(
                "GET",
                f"{N8N}/rest/executions/{execution_id}",
                headers={"Cookie": self.cookie, "browser-id": BROWSER_ID},
            )
            data = (body or {}).get("data", body) if isinstance(body, dict) else {}
            if (
                code == 200
                and data.get("finished") is not None
                and data.get("status") not in (None, "running", "new", "waiting")
            ):
                return str(data.get("status"))
            time.sleep(1)
        return None


def notifications(reference: str) -> int:
    query = urllib.parse.quote(f'subject:"{reference}"')
    status, body, _ = http("GET", f"{MAILPIT}/api/v1/search?query={query}")
    return int((body or {}).get("messages_count", 0)) if status == 200 else -1


# --- scenarios ------------------------------------------------------------------------------
Result = dict[str, Any]


def happy_path(ctx: dict[str, Any]) -> Result:
    lead = ctx["lead_a"] = str(uuid.uuid4())
    event = ctx["event_a"] = Event("lead.received", lead)
    accepted = event.send()
    end = wait_for_end(event)
    reference = stub("calls")["leads"][lead]["reference"]
    time.sleep(2)  # the notification is sent just before the report
    return {
        "end": end,
        "checks": {
            "webhook_accepted_202": accepted == 202,
            "reported_success": (end["outcome"] or {}).get("status") == "success",
            "qualified_once": len(calls("qualify", lead)) == 1,
            "crm_synced_once": len(calls("crm_sync", lead)) == 1,
            "draft_created_once": len(calls("draft", lead)) == 1,
            "operator_notified_once": notifications(reference) == 1,
            "n8n_execution_success": ctx["n8n"].status(end["execution_id"]) == "success",
        },
    }


def replay(ctx: dict[str, Any]) -> Result:
    lead, event = ctx["lead_a"], ctx["event_a"]
    before = {s: len(calls(s, lead)) for s in ("qualify", "crm_sync", "draft")}
    accepted = event.send()  # the same event again, with a fresh token
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline and len(calls("outcome", event.id)) < 2:
        time.sleep(1)
    reports = calls("outcome", event.id)
    reference = stub("calls")["leads"][lead]["reference"]
    return {
        "checks": {
            "webhook_accepted_202": accepted == 202,
            "second_run_reported": len(reports) == 2,
            "second_run_stopped_at_next_step": len(reports) == 2
            and reports[-1]["body"]["status"] == "stopped"
            and reports[-1]["body"]["step"] == "next_step",
            "no_new_step_calls": all(len(calls(s, lead)) == n for s, n in before.items()),
            "still_one_crm_contact": stub("calls")["leads"][lead]["crm_contact"] is not None
            and sum(
                1 for c in calls("crm_sync", lead) if c["response"].get("detail", {}).get("created")
            )
            == 1,
            "still_one_notification": notifications(reference) == 1,
        }
    }


def crm_rate_limited(ctx: dict[str, Any]) -> Result:  # noqa: ARG001
    lead = str(uuid.uuid4())
    script(
        "crm_sync",
        lead,
        {
            "status": 429,
            "headers": {"Retry-After": "4"},
            "body": {"error": {"code": "crm_unavailable"}},
        },
    )
    event = Event("lead.received", lead)
    event.send()
    end = wait_for_end(event)
    crm = calls("crm_sync", lead)
    gap = crm[1]["ts"] - crm[0]["ts"] if len(crm) == 2 else 0
    return {
        "end": end,
        "observed": {"seconds_between_crm_calls": round(gap, 1)},
        "checks": {
            "reported_success": (end["outcome"] or {}).get("status") == "success",
            "crm_called_twice": len(crm) == 2,
            "retry_after_honoured": gap >= 4,
            "one_contact_created": sum(
                1 for c in crm if c["response"].get("detail", {}).get("created")
            )
            == 1,
            "draft_created_once": len(calls("draft", lead)) == 1,
        },
    }


def crm_outage(ctx: dict[str, Any]) -> Result:  # noqa: ARG001
    lead = str(uuid.uuid4())
    script(
        "crm_sync",
        lead,
        {"status": 503, "sticky": True, "body": {"error": {"code": "crm_unavailable"}}},
    )
    event = Event("lead.received", lead)
    event.send()
    end = wait_for_end(event)
    report = end["outcome"] or {}
    return {
        "end": end,
        "checks": {
            "three_attempts_then_stop": len(calls("crm_sync", lead)) == 3,
            "reported_error_for_redelivery": report.get("status") == "error"
            and report.get("step") == "crm_sync",
            "no_draft_without_crm": calls("draft", lead) == [],
        },
    }


def crm_permanent(ctx: dict[str, Any]) -> Result:  # noqa: ARG001
    lead = str(uuid.uuid4())
    script("crm_sync", lead, {"status": 422, "body": {"error": {"code": "crm_rejected"}}})
    event = Event("lead.received", lead)
    event.send()
    end = wait_for_end(event)
    report = end["outcome"] or {}
    return {
        "end": end,
        "checks": {
            "not_retried": len(calls("crm_sync", lead)) == 1,
            "reported_stopped_with_error": report.get("status") == "stopped"
            and report.get("error") == "crm_rejected",
            "no_draft": calls("draft", lead) == [],
        },
    }


def model_unavailable(ctx: dict[str, Any]) -> Result:  # noqa: ARG001
    lead = str(uuid.uuid4())
    script(
        "qualify",
        lead,
        {
            "status": 503,
            "headers": {"Retry-After": "2"},
            "body": {"error": {"code": "model_unavailable"}},
        },
    )
    event = Event("lead.received", lead)
    event.send()
    end = wait_for_end(event)
    return {
        "end": end,
        "checks": {
            "reported_success": (end["outcome"] or {}).get("status") == "success",
            "qualify_called_twice": len(calls("qualify", lead)) == 2,
            "crm_synced_once": len(calls("crm_sync", lead)) == 1,
        },
    }


def invalid_model_output(ctx: dict[str, Any]) -> Result:
    lead = str(uuid.uuid4())
    # The API validates the model's JSON; invalid output is stored and the lead held for review.
    script(
        "qualify",
        lead,
        {
            "status": 200,
            "body": {
                "lead_id": lead,
                "status": "needs_review",
                "next": "stop",
                "detail": {"assessment_status": "invalid_output", "decision": "needs_review"},
            },
        },
    )
    event = Event("lead.received", lead)
    event.send()
    end = wait_for_end(event)
    return {
        "end": end,
        "checks": {
            "reported_stopped": (end["outcome"] or {}).get("status") == "stopped",
            "no_crm_sync": calls("crm_sync", lead) == [],
            "no_draft": calls("draft", lead) == [],
            "n8n_execution_success": ctx["n8n"].status(end["execution_id"]) == "success",
        },
    }


def draft_retry(ctx: dict[str, Any]) -> Result:  # noqa: ARG001
    lead = str(uuid.uuid4())
    script("draft", lead, {"status": 503, "body": {"error": {"code": "model_unavailable"}}})
    event = Event("lead.received", lead)
    event.send()
    end = wait_for_end(event)
    drafts = calls("draft", lead)
    return {
        "end": end,
        "checks": {
            "reported_success": (end["outcome"] or {}).get("status") == "success",
            "draft_called_twice": len(drafts) == 2,
            "one_draft_created": sum(
                1 for c in drafts if c["response"].get("detail", {}).get("created")
            )
            == 1,
        },
    }


def rejected_tokens(ctx: dict[str, Any]) -> Result:  # noqa: ARG001
    lead = str(uuid.uuid4())
    wrong_key, expired, tampered = (Event("lead.received", lead) for _ in range(3))
    statuses = {
        "wrong_key": wrong_key.send(key="not-the-dispatch-secret-not-the-dispatch-secret"),
        "expired": expired.send(exp_offset=-60),
        "tampered_payload": tampered.send(tamper=True),
    }
    time.sleep(3)
    ids = {wrong_key.id, expired.id, tampered.id}
    return {
        "observed": statuses,
        "checks": {
            "wrong_key_refused": statuses["wrong_key"] in (401, 403),
            "expired_refused": statuses["expired"] in (401, 403),
            "tampered_payload_refused_400": statuses["tampered_payload"] == 400,
            "no_api_calls_for_refused_events": not [
                c for c in stub("calls")["calls"] if c["key"] in ids or c["key"] == lead
            ],
        },
    }


def unexpected_failure(ctx: dict[str, Any]) -> Result:
    lead = str(uuid.uuid4())
    script(
        "next_step",
        lead,
        {"status": 500, "sticky": True, "body": {"error": {"code": "internal_error"}}},
    )
    event = Event("lead.received", lead)
    event.send()
    end = wait_for_end(event)
    err = end["workflow_error"] or {}
    return {
        "end": end,
        "checks": {
            "error_workflow_reported": bool(err),
            "error_names_the_failed_node": err.get("node") == "Next step",
            "error_report_has_no_lead_data": set(err)
            <= {"execution_id", "workflow", "node", "message"},
            "no_outcome_reported": end["outcome"] is None,
            "n8n_execution_error": ctx["n8n"].status(end["execution_id"]) == "error",
        },
    }


def delivery(ctx: dict[str, Any]) -> Result:
    draft_id = stub("calls")["leads"][ctx["lead_a"]]["draft_id"]
    script("deliver", draft_id, {"status": 503, "body": {"error": {"code": "smtp_unavailable"}}})
    event = ctx["event_d"] = Event("follow_up.approved", ctx["lead_a"], {"draft_id": draft_id})
    event.send()
    end = wait_for_end(event)
    deliveries = calls("deliver", draft_id)
    return {
        "end": end,
        "checks": {
            "reported_success": (end["outcome"] or {}).get("status") == "success",
            "retried_after_503": len(deliveries) == 2,
            "delivered_once": stub("calls")["drafts"][draft_id]["delivered"] == 1,
        },
    }


def delivery_replay(ctx: dict[str, Any]) -> Result:
    event: Event = ctx["event_d"]
    draft_id = event.envelope["data"]["draft_id"]
    event.send()
    deadline = time.time() + TIMEOUT_S
    while time.time() < deadline and len(calls("outcome", event.id)) < 2:
        time.sleep(1)
    last = calls("deliver", draft_id)[-1]
    return {
        "checks": {
            "second_run_reported": len(calls("outcome", event.id)) == 2,
            "replay_did_not_send_again": last["response"]["detail"]["delivered_now"] is False,
            "still_delivered_once": stub("calls")["drafts"][draft_id]["delivered"] == 1,
        }
    }


SCENARIOS: list[tuple[str, Callable[[dict[str, Any]], Result]]] = [
    ("pipeline: new lead runs qualification, CRM sync and draft once", happy_path),
    ("pipeline: replayed event resumes at the lead's state, no duplicates", replay),
    ("pipeline: CRM 429 with Retry-After is waited out, one contact", crm_rate_limited),
    ("pipeline: CRM outage exhausts 3 attempts, reported for redelivery", crm_outage),
    ("pipeline: CRM permanent error stops without retry", crm_permanent),
    ("pipeline: model unavailable once, retried with backoff", model_unavailable),
    ("pipeline: invalid model output stops for review, no CRM, no draft", invalid_model_output),
    ("pipeline: draft step retried without creating a second draft", draft_retry),
    ("webhook: wrong key, expired token and tampered payload refused", rejected_tokens),
    ("error workflow: unexpected failure reported with ids only", unexpected_failure),
    ("delivery: approved draft delivered once after a 503", delivery),
    ("delivery: replayed approval never sends twice", delivery_replay),
]


def main() -> int:
    deadline = time.time() + 180
    while time.time() < deadline:
        try:
            if http("GET", f"{N8N}/healthz/readiness")[0] == 200 and stub("calls") is not None:
                break
        except OSError:
            pass
        time.sleep(2)
    stub("reset", {})
    ctx: dict[str, Any] = {"n8n": N8nApi()}
    results = []
    only = os.environ.get("HARNESS_ONLY", "")
    for title, scenario in SCENARIOS:
        if only and only not in title:
            continue
        started = time.time()
        try:
            result = scenario(ctx)
        except Exception as exc:
            result = {"checks": {"ran_without_exception": False}, "error": repr(exc)}
        result.update(
            title=title,
            seconds=round(time.time() - started, 1),
            passed=all(result["checks"].values()),
        )
        results.append(result)
        mark = "PASS" if result["passed"] else "FAIL"
        print(f"{mark} {title} ({result['seconds']} s)", flush=True)
        for name, ok in result["checks"].items():
            print(f"     {'ok ' if ok else 'BAD'} {name}", flush=True)
    report = {
        "n8n_rest_login": ctx["n8n"].ok,
        "scenarios": len(results),
        "passed": sum(1 for r in results if r["passed"]),
        "checks": sum(len(r["checks"]) for r in results),
        "checks_passed": sum(sum(1 for v in r["checks"].values() if v) for r in results),
        "results": results,
        "finished_at": datetime.now(UTC).isoformat(),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "n8n-harness-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"{report['passed']}/{report['scenarios']} scenarios, "
        f"{report['checks_passed']}/{report['checks']} checks passed",
        flush=True,
    )
    return 0 if report["passed"] == report["scenarios"] else 1


if __name__ == "__main__":
    sys.exit(main())
