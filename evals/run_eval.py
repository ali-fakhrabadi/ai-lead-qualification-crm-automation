"""Run the benchmark dataset against a running stack and record what actually happened.

    evals/run.sh                       # wraps this in a container on the stack network

Scenarios run one after another (one lead in flight at a time, so the timings are per-lead
and not queueing artefacts). For each scenario the harness sends the submissions through the
public form or a signed partner webhook, waits until the lead has settled (a terminal status
and no workflow event still open), and checks the guarantees for that scenario kind. At the
end it collects every lead's record, audit trail and workflow runs, the demo CRM's request
log and the mail sink. Everything written to ``raw.json`` was observed; nothing is estimated.
Metrics are computed from ``raw.json`` by ``evals/metrics.py``.
"""

from __future__ import annotations

import json
import os
import sys
import time
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import client as c

DATASET = Path(os.environ.get("EVAL_DATASET", "/data/lead-evaluation-dataset.jsonl"))
OUT = Path(os.environ.get("EVAL_OUT", "/out"))
LEAD_TIMEOUT_S = int(os.environ.get("EVAL_LEAD_TIMEOUT_S", "1800"))
ONLY = {s for s in os.environ.get("EVAL_ONLY", "").split(",") if s}
# Resume an interrupted run on the same stack: keep the earlier records, run the rest, and
# re-run the listed ids (e.g. scenarios fixed in the dataset after they ran).
RESUME_FROM = os.environ.get("EVAL_RESUME_FROM", "")
RERUN = {s for s in os.environ.get("EVAL_RERUN", "").split(",") if s}
# Run selected scenarios again on a stack that already has them (e.g. after a fix): the tag is
# put in front of every email domain and website so the leads are new to the stack.
RUN_TAG = os.environ.get("EVAL_RUN_TAG", "")


def key(rec: dict[str, Any]) -> str:
    """Scenario part of submission and event ids (tagged runs need new idempotency keys)."""
    return f"{RUN_TAG}_{rec['id']}" if RUN_TAG else rec["id"]


TERMINAL = {
    "follow_up_draft_ready",
    "needs_review",
    "failed",
    "follow_up_sent",
    "follow_up_rejected",
    "closed",
    "unqualified",
}
OPEN_EVENTS = {"pending", "dispatched", "failed"}  # failed = waiting for its redelivery
EDIT_MARKER = "P.S. We can share a short example of a similar workflow on the call."


def now() -> str:
    return datetime.now(UTC).isoformat()


def log(message: str) -> None:
    print(f"{datetime.now(UTC):%H:%M:%S} {message}", flush=True)


class Run:
    def __init__(self) -> None:
        self.op = c.Operator()
        self.records: list[dict[str, Any]] = []
        self.by_id: dict[str, dict[str, Any]] = {}

    # --- helpers --------------------------------------------------------------------------------
    @staticmethod
    def note(rec: dict[str, Any], endpoint: str, attempts: list[c.Response]) -> c.Response:
        for r in attempts:
            body = r.body if isinstance(r.body, dict) else {}
            rec["requests"].append(
                {
                    "endpoint": endpoint,
                    "status": r.status,
                    "ms": round(r.ms, 2),
                    "replayed": body.get("replayed"),
                    "outcome": body.get("outcome"),
                    "error_code": (body.get("error") or {}).get("code"),
                }
            )
        return attempts[-1]

    def form(
        self, rec: dict[str, Any], lead: dict[str, Any], n: int, sub: str | None = None
    ) -> c.Response:
        return self.note(rec, "form", c.submit_form(lead, sub or c.submission_id(key(rec), n)))

    def webhook(
        self, rec: dict[str, Any], lead: dict[str, Any], event_id: str, **kw: Any
    ) -> c.Response:
        event = {"event_id": event_id, "occurred_at": now(), "lead": lead}
        return self.note(rec, "webhook", c.send_webhook(event, **kw))

    def send(self, rec: dict[str, Any], lead: dict[str, Any], n: int = 1) -> c.Response:
        if rec["channel"] == "webhook":
            return self.webhook(rec, lead, f"evt_{key(rec).lower()}_{n:02d}")
        return self.form(rec, lead, n)

    def settle(self, email: str) -> dict[str, Any] | None:
        deadline = time.time() + LEAD_TIMEOUT_S
        while time.time() < deadline:
            lead = self.op.lead_by_email(email)
            if lead is not None and lead["status"] in TERMINAL:
                detail = self.op.detail(lead["id"])
                if not any(w["status"] in OPEN_EVENTS for w in detail["workflow_events"]):
                    return detail
            time.sleep(3)
        return None

    def settle_into(self, rec: dict[str, Any], email: str) -> dict[str, Any] | None:
        if not any(r["status"] == 202 for r in rec["requests"]):
            # Refused at intake (validation, auth): there is no lead to wait for.
            rec["observed"]["refused_at_intake"] = True
            return None
        detail = self.settle(email)
        if detail is None:
            rec["timed_out"] = True
        return detail

    @staticmethod
    def routing_ok(detail: dict[str, Any]) -> bool:
        """Qualified leads get a CRM record and a draft; leads held for review get neither."""
        if detail["decision"] == "qualified" and detail["status"] == "follow_up_draft_ready":
            return bool(detail["crm"] and detail["crm"]["status"] == "synced" and detail["drafts"])
        if detail["status"] == "needs_review":
            return not detail["drafts"] and (
                detail["crm"] is None or detail["crm"]["status"] != "synced"
            )
        return True

    @staticmethod
    def lead_received_events(detail: dict[str, Any]) -> int:
        return sum(1 for w in detail["workflow_events"] if w["event_type"] == "lead.received")

    # --- scenario kinds -------------------------------------------------------------------------
    def new_lead(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        r = self.send(rec, s["lead"])
        rec["checks"]["accepted"] = r.status == 202
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["settled"] = d is not None
        if d:
            rec["checks"]["routing_consistent"] = self.routing_ok(d)

    def webhook_replay(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        event_id = f"evt_{key(rec).lower()}_01"
        responses = [self.webhook(rec, s["lead"], event_id) for _ in range(s["deliveries"])]
        rec["checks"]["all_acknowledged"] = all(r.status == 202 for r in responses)
        rec["checks"]["replays_flagged"] = all(r.body.get("replayed") for r in responses[1:])
        rec["checks"]["same_reference"] = (
            len({r.body.get("lead_reference") for r in responses}) == 1
        )
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["one_lead"] = len(self.op.leads_matching(s["lead"]["email"])) == 1
        rec["checks"]["one_crm_contact_max"] = len(c.crm_contacts(s["lead"]["email"])) <= 1
        if d:
            rec["checks"]["one_workflow_run"] = self.lead_received_events(d) == 1
            rec["checks"]["routing_consistent"] = self.routing_ok(d)

    def webhook_key_reuse(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        event_id = f"evt_{key(rec).lower()}_01"
        first = self.webhook(rec, s["lead"], event_id)
        changed = {**s["lead"], "message": s["lead"]["message"] + " (edited by the partner)"}
        second = self.webhook(rec, changed, event_id)
        rec["checks"]["first_accepted"] = first.status == 202
        rec["checks"]["changed_payload_refused"] = second.status == 409
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["one_lead"] = len(self.op.leads_matching(s["lead"]["email"])) == 1
        if d:
            rec["checks"]["original_kept"] = d["message"] == s["lead"]["message"]

    def exact_duplicate_email(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        first = self.send(rec, s["lead"], 1)
        second = self.send(rec, s["lead"], 2)  # a new submission id, identical content
        rec["checks"]["first_created"] = first.body.get("outcome") == "created"
        rec["checks"]["second_ignored"] = second.body.get("outcome") == "duplicate_ignored"
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["one_lead"] = len(self.op.leads_matching(s["lead"]["email"])) == 1
        rec["checks"]["one_crm_contact_max"] = len(c.crm_contacts(s["lead"]["email"])) <= 1
        if d:
            rec["checks"]["one_workflow_run"] = self.lead_received_events(d) == 1
            rec["checks"]["routing_consistent"] = self.routing_ok(d)

    def form_double_submit(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        sub = c.submission_id(key(rec), 1)
        first = self.form(rec, s["lead"], 1, sub)
        second = self.form(rec, s["lead"], 1, sub)
        rec["checks"]["second_replayed"] = bool(second.body.get("replayed"))
        rec["checks"]["same_reference"] = first.body.get("lead_reference") == second.body.get(
            "lead_reference"
        )
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["one_lead"] = len(self.op.leads_matching(s["lead"]["email"])) == 1
        if d:
            rec["checks"]["one_workflow_run"] = self.lead_received_events(d) == 1

    def same_email_new_inquiry(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        self.send(rec, s["lead"], 1)
        self.settle_into(rec, s["lead"]["email"])
        second = self.send(rec, {**s["lead"], "message": s["second_message"]}, 2)
        rec["checks"]["second_updates_existing"] = second.body.get("outcome") == "updated_existing"
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["one_lead"] = len(self.op.leads_matching(s["lead"]["email"])) == 1
        rec["checks"]["one_crm_contact_max"] = len(c.crm_contacts(s["lead"]["email"])) <= 1
        if d:
            rec["observed"]["review_reasons"] = d["review_reasons"]

    def same_email_conflict(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        self.send(rec, s["lead"], 1)
        self.settle_into(rec, s["lead"]["email"])
        second = self.send(rec, {**s["lead"], **s["changes"]}, 2)
        rec["checks"]["second_needs_review"] = second.body.get("outcome") == "needs_review"
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["one_lead"] = len(self.op.leads_matching(s["lead"]["email"])) == 1
        if d:
            rec["checks"]["company_not_overwritten"] = d["company"] == s["lead"].get("company")
            rec["checks"]["review_opened"] = any(
                x["kind"] == "same_email_conflict" and x["status"] == "open"
                for x in d["duplicates"]
            )

    def similar_contacts(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        self.send(rec, s["lead"], 1)
        self.send(rec, s["second_lead"], 2)
        a = self.settle_into(rec, s["lead"]["email"])
        b = self.settle_into(rec, s["second_lead"]["email"])
        rec["checks"]["two_leads"] = bool(a and b and a["id"] != b["id"])
        if a and b:
            linked = any(
                x["kind"] == "possible_same_person"
                and {x["lead_id"], x["candidate_lead_id"]} == {a["id"], b["id"]}
                for x in b["duplicates"]
            )
            rec["checks"]["flagged_as_expected"] = (
                linked == s["expect"]["possible_duplicate_review"]
            )
            rec["checks"]["not_merged"] = a["email"] != b["email"]

    def invalid_input(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        r = self.form(rec, s["lead"], 1)
        rec["checks"]["status_as_expected"] = r.status == s["expect"]["status"]
        if r.status == 422:
            rec["observed"]["error_fields"] = [
                d.get("field") for d in (r.body.get("error") or {}).get("details", [])
            ]
        rec["checks"]["no_lead_created"] = not self.op.leads_matching(s["lead"]["email"] or "@@")

    def webhook_auth(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        event_id = f"evt_{key(rec).lower()}_01"
        kw: dict[str, Any] = {}
        if s["case"] == "bad_signature":
            kw["key"] = "not-the-shared-secret-for-this-partner"
        elif s["case"] == "expired_signature":
            kw["ts"] = int(time.time()) - 3600
        else:
            kw["source"] = "unknown-partner"
        r = self.webhook(rec, s["lead"], event_id, **kw)
        rec["checks"]["status_as_expected"] = r.status == s["expect"]["status"]
        rec["checks"]["no_lead_created"] = not self.op.leads_matching(s["lead"]["email"])

    def prompt_injection(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        self.send(rec, s["lead"])
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["settled"] = d is not None
        if d:
            rec["checks"]["held_for_review"] = d["status"] == "needs_review"
            # Detection by the pattern check is measured, not required: the guarantee is that
            # the lead is held for a person with no CRM record and no draft.
            rec["observed"]["flagged_by_pattern"] = "instruction_like_text" in d["review_reasons"]
            rec["observed"]["review_reasons"] = d["review_reasons"]
            rec["checks"]["no_crm_record"] = not c.crm_contacts(s["lead"]["email"])
            rec["checks"]["no_draft"] = not d["drafts"]

    def crm_failure(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        f = s["fault"]
        c.crm_fault(s["lead"]["email"], f["operation"], f["status"], f["remaining"])
        self.send(rec, s["lead"])
        d = self.settle_into(rec, s["lead"]["email"])
        if d is None:
            return
        rec["observed"]["status_before_operator"] = d["status"]
        rec["observed"]["crm_error_before_operator"] = (d["crm"] or {}).get("last_error_code")
        rec["observed"]["operator_retries"] = 0
        if d["status"] == "failed":
            r = self.op.call("POST", f"/api/v1/leads/{d['id']}/retry")
            rec["observed"]["operator_retries"] = 1
            rec["checks"]["operator_retry_accepted"] = r.status == 202
            d = self.settle_into(rec, s["lead"]["email"]) or d
        rec["observed"]["exercised"] = bool(d["crm"])
        rec["checks"]["settled"] = not rec["timed_out"]
        if d["crm"]:
            rec["checks"]["synced_in_the_end"] = d["crm"]["status"] == "synced"
            rec["checks"]["exactly_one_crm_contact"] = len(c.crm_contacts(s["lead"]["email"])) == 1
            if s["kind"] == "crm_permanent":
                rec["checks"]["stopped_for_operator"] = (
                    rec["observed"]["status_before_operator"] == "failed"
                )
            else:
                rec["checks"]["recovered_without_operator"] = (
                    rec["observed"]["operator_retries"] == 0
                )

    def llm_failure(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        f = s["fault"]
        c.llm_fault(s["lead"]["email"], f["mode"], f["remaining"])
        self.send(rec, s["lead"])
        d = self.settle_into(rec, s["lead"]["email"])
        rec["checks"]["settled"] = d is not None
        if d:
            final = d["assessments"][0]["status"] if d["assessments"] else None
            rec["observed"]["assessment_statuses"] = [a["status"] for a in d["assessments"]]
            rec["checks"]["final_assessment_as_expected"] = final == s["expect"]["final_assessment"]
            if final == "invalid_output":
                rec["checks"]["held_for_review"] = d["status"] == "needs_review"
                rec["checks"]["no_crm_record"] = not c.crm_contacts(s["lead"]["email"])
            rec["checks"]["lead_kept"] = True

    def human_approval(self, rec: dict[str, Any], s: dict[str, Any]) -> None:
        ref = self.by_id.get(s["ref"])
        if ref is None:
            rec["observed"]["skipped"] = "referenced scenario did not run"
            return
        email = ref["emails"][0]
        rec["emails"] = [email]
        lead = self.op.lead_by_email(email)
        detail = self.op.detail(lead["id"]) if lead else None
        drafts = [x for x in (detail or {}).get("drafts", []) if x["status"] == "draft"]
        if not drafts:
            rec["observed"]["skipped"] = "no draft (the lead was not qualified)"
            return
        draft = drafts[0]
        rec["checks"]["nothing_sent_before_decision"] = c.mail_to(email) == []
        if s["edit"]:
            r = self.op.call(
                "PATCH",
                f"/api/v1/follow-ups/{draft['id']}",
                {"subject": draft["subject"], "body": draft["body"] + "\n\n" + EDIT_MARKER},
            )
            rec["checks"]["edit_saved"] = r.status == 200
        action = s["action"]
        r = self.op.call(
            "POST", f"/api/v1/follow-ups/{draft['id']}/{action}", {"note": f"benchmark {action}"}
        )
        rec["checks"]["decision_recorded"] = r.status == 200
        if action == "approve":
            deadline = time.time() + 300
            while time.time() < deadline and not c.mail_to(email):
                time.sleep(2)
            mails = c.mail_to(email)
            rec["checks"]["delivered_once_to_sink"] = len(mails) == 1
            if s["edit"] and mails:
                text = c.request("GET", f"{c.MAILPIT_URL}/api/v1/message/{mails[0]['ID']}").body
                rec["checks"]["edited_text_delivered"] = EDIT_MARKER in (text or {}).get("Text", "")
        else:
            time.sleep(15)
            rec["checks"]["nothing_sent_after_reject"] = c.mail_to(email) == []

    # --- driver ---------------------------------------------------------------------------------
    def run(self, scenarios: list[dict[str, Any]]) -> None:
        handlers: dict[str, Callable[[dict[str, Any], dict[str, Any]], None]] = {
            "new_lead": self.new_lead,
            "webhook_replay": self.webhook_replay,
            "webhook_key_reuse": self.webhook_key_reuse,
            "exact_duplicate_email": self.exact_duplicate_email,
            "form_double_submit": self.form_double_submit,
            "same_email_new_inquiry": self.same_email_new_inquiry,
            "same_email_conflict": self.same_email_conflict,
            "similar_contacts": self.similar_contacts,
            "invalid_input": self.invalid_input,
            "webhook_auth": self.webhook_auth,
            "prompt_injection": self.prompt_injection,
            "crm_transient": self.crm_failure,
            "crm_permanent": self.crm_failure,
            "llm_malformed": self.llm_failure,
            "llm_unavailable": self.llm_failure,
            "human_approval": self.human_approval,
        }
        for i, s in enumerate(scenarios, 1):
            emails = [x["email"] for x in (s.get("lead"), s.get("second_lead")) if x]
            rec: dict[str, Any] = {
                "id": s["id"],
                "kind": s["kind"],
                "case": s.get("case"),
                "channel": s.get("channel"),
                "emails": emails,
                "expect": s.get("expect", {}),
                "started_at": now(),
                "requests": [],
                "checks": {},
                "observed": {},
                "timed_out": False,
                "error": None,
            }
            log(f"[{i}/{len(scenarios)}] {s['id']} {s['kind']} {s.get('case') or ''}")
            try:
                handlers[s["kind"]](rec, s)
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
                traceback.print_exc()
            rec["finished_at"] = now()
            rec["passed"] = (
                bool(rec["checks"])
                and all(rec["checks"].values())
                and not (rec["error"] or rec["timed_out"])
            )
            if not rec["checks"] and rec["observed"].get("skipped"):
                rec["passed"] = None
            log(
                f"    -> {'PASS' if rec['passed'] else 'n/a' if rec['passed'] is None else 'FAIL'}"
                f" {json.dumps(rec['checks'])}"
            )
            self.records.append(rec)
            self.by_id[s["id"]] = rec
            self.save(partial=True)

    def collect(self) -> dict[str, Any]:
        """Every lead the scenarios produced, with its audit trail, plus system-wide counters."""
        leads: dict[str, Any] = {}
        for rec in self.records:
            for email in rec["emails"]:
                for lead in self.op.leads_matching(email):
                    detail = self.op.detail(lead["id"])
                    detail["history"] = self.op.history(lead["id"])
                    detail["crm_contacts"] = len(c.crm_contacts(email))
                    detail["mails"] = len(c.mail_to(email))
                    detail.pop("message", None)
                    for e in detail["events"]:
                        e.pop("payload", None)
                    leads[lead["id"]] = detail
        stats = self.op.call("GET", "/api/v1/stats").body
        config = self.op.call("GET", "/api/v1/settings/qualification").body
        config.pop("rubric", None)
        return {
            "leads": leads,
            "stats": stats,
            "config": config,
            "crm": c.crm_stats(),
            "mail_total": c.mail_total(),
        }

    def save(self, *, partial: bool, extra: dict[str, Any] | None = None) -> None:
        OUT.mkdir(parents=True, exist_ok=True)
        data = {
            "finished": not partial,
            "saved_at": now(),
            "scenarios": self.records,
            **(extra or {}),
        }
        tmp = OUT / "raw.json.tmp"
        tmp.write_text(json.dumps(data, indent=1, default=str), encoding="utf-8")
        tmp.replace(OUT / "raw.json")


def main() -> int:
    scenarios = [
        json.loads(line)
        for line in DATASET.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if ONLY:
        scenarios = [s for s in scenarios if s["id"] in ONLY or s["kind"] in ONLY]
    if RUN_TAG:
        for s in scenarios:
            for lead in (s.get("lead"), s.get("second_lead")):
                if lead and "@" in str(lead.get("email") or ""):
                    local, domain = lead["email"].split("@", 1)
                    lead["email"] = f"{local}@{RUN_TAG}-{domain}"
                    if lead.get("website"):
                        lead["website"] = f"{RUN_TAG}-{lead['website']}"
    run = Run()
    existing = run.op.call("GET", "/api/v1/leads?limit=1").body["total"]
    if existing and not os.environ.get("EVAL_ALLOW_EXISTING"):
        print(
            f"refusing to run: the stack already has {existing} leads (use a fresh stack)",
            file=sys.stderr,
        )
        return 2
    started = now()
    resumed: dict[str, Any] = {}
    if RESUME_FROM:
        previous = json.loads(Path(RESUME_FROM).read_text(encoding="utf-8"))
        kept = [r for r in previous["scenarios"] if r["id"] not in RERUN]
        run.records = kept
        run.by_id = {r["id"]: r for r in kept}
        started = previous.get("started_at") or previous["scenarios"][0]["started_at"]
        scenarios = [s for s in scenarios if s["id"] not in run.by_id]
        resumed = {
            "resumed": {"kept": len(kept), "rerun": sorted(RERUN), "remaining": len(scenarios)}
        }
        log(f"resuming: {len(kept)} kept, {len(scenarios)} to run (re-run: {sorted(RERUN)})")
    run.run(scenarios)
    run.records.sort(key=lambda r: r["id"])
    finished = now()
    log("collecting lead records, audit trails, CRM and mail counters")
    run.save(
        partial=False,
        extra={
            "started_at": started,
            "finished_at": finished,
            "dataset": DATASET.name,
            "scenario_count": len(run.records),
            **({"run_tag": RUN_TAG} if RUN_TAG else {}),
            **resumed,
            **run.collect(),
        },
    )
    failed = [r["id"] for r in run.records if r["passed"] is False]
    log(f"done: {len(run.records)} scenarios, {len(failed)} with a failed check {failed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
