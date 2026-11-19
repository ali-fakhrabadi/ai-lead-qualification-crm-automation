"""Compute the benchmark metrics from what the harness recorded (``raw.json``).

    python evals/metrics.py evals/out/raw.json --resources evals/out/resources.json \
        --k6 evals/out/k6-summary.json --environment evals/out/environment.json \
        --out docs/evaluation/results.json

Every number is derived from observed data: HTTP responses seen by the harness, the leads'
assessments and audit trails, the workflow-event records written by n8n's reports, the demo
CRM's request log and the mail sink. Each metric carries its definition and denominator.
Percentiles use the nearest-rank method on the listed samples.
"""

from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

# Kinds whose expected decision depends only on the lead's own text (no duplicate or conflict
# rule that holds the lead for review whatever the model says).
CONTENT_ONLY_KINDS = {
    "new_lead",
    "webhook_replay",
    "webhook_key_reuse",
    "exact_duplicate_email",
    "form_double_submit",
    "crm_transient",
    "crm_permanent",
    "llm_unavailable",
    "llm_malformed",
}
CLEAN_KINDS = {
    "new_lead",
    "webhook_replay",
    "webhook_key_reuse",
    "exact_duplicate_email",
    "form_double_submit",
}  # no injected failure: used for the timing metrics


def pct(values: list[float], p: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(p / 100 * len(ordered)))
    return round(ordered[rank - 1], 1)


def dist(values: list[float]) -> dict[str, Any]:
    return {
        "n": len(values),
        "p50": pct(values, 50),
        "p95": pct(values, 95),
        "min": round(min(values), 1) if values else None,
        "max": round(max(values), 1) if values else None,
    }


def rate(numerator: int, denominator: int) -> dict[str, Any]:
    return {
        "numerator": numerator,
        "denominator": denominator,
        "rate": round(numerator / denominator, 4) if denominator else None,
    }


def ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def first_audit(lead: dict[str, Any], action: str) -> dict[str, Any] | None:
    hits = sorted((h for h in lead["history"] if h["action"] == action), key=lambda h: h["ts"])
    return hits[0] if hits else None


def received_at(lead: dict[str, Any]) -> datetime:
    return min(ts(e["received_at"]) for e in lead["events"])


def first_valid(lead: dict[str, Any]) -> dict[str, Any] | None:
    valid = sorted(
        (a for a in lead["assessments"] if a["status"] == "valid"), key=lambda a: a["created_at"]
    )
    return valid[0] if valid else None


def compute(raw: dict[str, Any], extras: dict[str, Any]) -> dict[str, Any]:
    scenarios = raw["scenarios"]
    leads: dict[str, dict[str, Any]] = raw["leads"]
    by_email = {lead["email"].lower(): lead for lead in leads.values()}
    kinds = Counter(s["kind"] for s in scenarios)
    out: dict[str, Any] = {
        "run": {
            "started_at": raw.get("started_at"),
            "finished_at": raw.get("finished_at"),
            "dataset": raw.get("dataset"),
            "config": raw.get("config"),
            **{k: v for k, v in extras.items() if k in ("environment", "model")},
        }
    }

    # --- scenario outcomes ---------------------------------------------------------------------
    passed = [s for s in scenarios if s["passed"] is True]
    failed = [s for s in scenarios if s["passed"] is False]
    skipped = [s for s in scenarios if s["passed"] is None]
    out["scenarios"] = {
        "executed": len(scenarios),
        "by_kind": dict(kinds),
        "all_checks_passed": len(passed),
        "with_a_failed_check": len(failed),
        "not_applicable": len(skipped),
        "failed": [
            {
                "id": s["id"],
                "kind": s["kind"],
                "case": s.get("case"),
                "failed_checks": [k for k, v in s["checks"].items() if not v],
                "timed_out": s["timed_out"],
                "error": s["error"],
            }
            for s in failed
        ],
        "not_applicable_reasons": [
            {"id": s["id"], "reason": s["observed"].get("skipped")} for s in skipped
        ],
        "leads_created": len(leads),
    }

    # --- intake and idempotency ---------------------------------------------------------------
    reqs = [(s, r) for s in scenarios for r in s["requests"]]
    webhook = [r for _, r in reqs if r["endpoint"] == "webhook"]
    form = [r for _, r in reqs if r["endpoint"] == "form"]
    dup_sent = dup_safe = 0
    for s in scenarios:
        rs = [r for r in s["requests"] if r["status"] != 429]
        if s["kind"] in ("webhook_replay", "form_double_submit"):
            extra = rs[1:]
            dup_sent += len(extra)
            ok = s["checks"].get("one_lead") and s["checks"].get("one_workflow_run", True)
            dup_safe += sum(1 for r in extra if r["replayed"] and ok)
        elif s["kind"] == "exact_duplicate_email":
            dup_sent += 1
            dup_safe += int(
                bool(
                    s["checks"].get("second_ignored")
                    and s["checks"].get("one_lead")
                    and s["checks"].get("one_workflow_run", True)
                )
            )
        elif s["kind"] == "webhook_key_reuse":
            dup_sent += 1
            dup_safe += int(
                bool(s["checks"].get("changed_payload_refused") and s["checks"].get("one_lead"))
            )
    synced = [lead for lead in leads.values() if lead["crm"] and lead["crm"]["remote_contact_id"]]
    out["intake"] = {
        "webhook_requests": len(webhook),
        "webhook_accepted_202": sum(1 for r in webhook if r["status"] == 202),
        "webhook_rejected": dict(Counter(str(r["status"]) for r in webhook if r["status"] != 202)),
        "form_requests": len(form),
        "form_accepted_202": sum(1 for r in form if r["status"] == 202),
        "form_rejected": dict(Counter(str(r["status"]) for r in form if r["status"] != 202)),
        "rate_limited_429": sum(1 for _, r in reqs if r["status"] == 429),
        "duplicate_deliveries_suppressed": {
            **rate(dup_safe, dup_sent),
            "definition": "Repeated deliveries (webhook redeliveries, double-submitted forms, "
            "identical re-submissions, reused event ids with a changed payload) "
            "that created no second lead and no second workflow run, over all "
            "repeated deliveries sent.",
        },
        "duplicate_crm_contacts": {
            "leads_with_more_than_one_contact": sum(
                1 for lead in leads.values() if lead["crm_contacts"] > 1
            ),
            "leads_with_a_crm_contact": len(synced),
            "crm_duplicate_emails": raw["crm"].get("duplicate_emails"),
            "definition": "Scenario leads whose email matches more than one contact in the CRM "
            "after the run (the demo CRM, like HubSpot, also refuses a second "
            "contact with the same email; the adapter then updates the existing "
            "one).",
        },
    }

    # --- CRM synchronisation ------------------------------------------------------------------
    sync_ok = sum(
        1 for lead in leads.values() for h in lead["history"] if h["action"] == "crm.synced"
    )
    sync_fail = [
        h for lead in leads.values() for h in lead["history"] if h["action"] == "crm.sync_failed"
    ]
    out["crm"] = {
        "sync_operations_succeeded": sync_ok,
        "sync_operations_failed": len(sync_fail),
        "failed_by_code": dict(Counter(h["details"].get("code") for h in sync_fail)),
        "http_calls_logged_by_crm": raw["crm"].get("requests"),
        "http_calls_failed_by_injection": raw["crm"].get("faulted_requests"),
        "contacts": raw["crm"].get("contacts"),
        "companies": raw["crm"].get("companies"),
        "notes": raw["crm"].get("notes"),
        "definition": "A sync operation is one call of the CRM-sync step (search, create or "
        "update the contact, company, association and note, each HTTP call "
        "retried by the adapter). Failed operations are the ones that still "
        "failed after the adapter's retries and were handed back to n8n.",
    }

    # --- recovery ----------------------------------------------------------------------------
    transient = [
        s
        for s in scenarios
        if (s["kind"] == "crm_transient" and s["observed"].get("exercised"))
        or s["kind"] == "llm_unavailable"
        or (s["kind"] == "llm_malformed" and s["expect"].get("final_assessment") == "valid")
    ]
    recovered = [s for s in transient if s["passed"]]
    permanent = [
        s for s in scenarios if s["kind"] == "crm_permanent" and s["observed"].get("exercised")
    ]
    out["recovery"] = {
        "transient_failures_recovered": {
            **rate(len(recovered), len(transient)),
            "definition": "Scenarios with an injected transient failure (CRM 5xx/429 for 1-4 "
            "consecutive calls; model unavailable 1-2 times; one malformed model "
            "reply) that ended in the expected state with exactly one CRM "
            "contact and no operator action, over those scenarios that reached "
            "the failing step.",
        },
        "permanent_failures_stopped_then_recovered_by_operator": {
            **rate(sum(1 for s in permanent if s["passed"]), len(permanent)),
            "definition": "CRM 400/401/403 scenarios where the lead stopped in `failed` for "
            "an operator and one operator retry then synced exactly one contact.",
        },
        "unrecovered": [s["id"] for s in transient + permanent if not s["passed"]],
    }

    # --- the model ---------------------------------------------------------------------------
    completed = [
        h
        for lead in leads.values()
        for h in lead["history"]
        if h["action"] == "qualification.completed"
    ]
    clean_calls = [h for h in completed if not h["details"].get("injected_faults")]
    first_ok = sum(
        1
        for h in clean_calls
        if h["details"].get("status") == "valid" and not h["details"].get("repair_attempted")
    )
    after_repair = sum(1 for h in clean_calls if h["details"].get("status") == "valid")
    provider_errors = [
        h
        for lead in leads.values()
        for h in lead["history"]
        if h["action"] == "qualification.provider_error" and not h["details"].get("injected_faults")
    ]
    latencies = [
        float(h["details"]["latency_ms"]) / 1000
        for h in clean_calls
        if h["details"].get("latency_ms") is not None
    ]
    tokens_in = [
        float(h["details"]["prompt_tokens"])
        for h in clean_calls
        if h["details"].get("prompt_tokens")
    ]
    tokens_out = [
        float(h["details"]["completion_tokens"])
        for h in clean_calls
        if h["details"].get("completion_tokens")
    ]
    out["model"] = {
        "qualification_runs_without_injected_faults": len(clean_calls),
        "schema_valid_first_reply": {
            **rate(first_ok, len(clean_calls)),
            "definition": "Model replies that passed strict schema "
            "validation without a repair call.",
        },
        "schema_valid_after_one_repair": {
            **rate(after_repair, len(clean_calls)),
            "definition": "Valid after at most one repair call; "
            "the rest were stored as invalid "
            "output and held for review.",
        },
        "unexpected_provider_errors": len(provider_errors),
        "latency_s": dist(latencies),
        "prompt_tokens": dist(tokens_in),
        "completion_tokens": dist(tokens_out),
    }

    # --- agreement with labels ----------------------------------------------------------------
    def labelled(kinds_: set[str]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
        pairs = []
        for s in scenarios:
            if s["kind"] in kinds_ and s["expect"].get("decision") and s["emails"]:
                lead = by_email.get(s["emails"][0].lower())
                if lead is not None:
                    pairs.append((s, lead))
        return pairs

    def agreement(pairs: list[tuple[dict[str, Any], dict[str, Any]]]) -> dict[str, Any]:
        matrix: Counter[tuple[str, str]] = Counter()
        intent_hits = intent_total = 0
        no_valid = 0
        for s, lead in pairs:
            a = first_valid(lead)
            if a is None:
                no_valid += 1
                predicted = "needs_review"  # what the system did: held for a person
            else:
                predicted = a["decision"]
                if s["expect"].get("intent"):
                    intent_total += 1
                    intent_hits += int(
                        (a["parsed"] or {}).get("intent_category") == s["expect"]["intent"]
                    )
            matrix[(s["expect"]["decision"], predicted)] += 1
        tp = matrix[("qualified", "qualified")]
        fp = matrix[("needs_review", "qualified")]
        fn = matrix[("qualified", "needs_review")]
        tn = matrix[("needs_review", "needs_review")]
        n = tp + fp + fn + tn
        return {
            "scenarios": n,
            "without_a_valid_assessment": no_valid,
            "decision_accuracy": rate(tp + tn, n),
            "confusion": {
                "expected_qualified__got_qualified": tp,
                "expected_qualified__got_needs_review": fn,
                "expected_review__got_qualified": fp,
                "expected_review__got_needs_review": tn,
            },
            "qualified_precision": rate(tp, tp + fp),
            "qualified_recall": rate(tp, tp + fn),
            "false_auto_qualification_rate": {
                **rate(fp, fp + tn),
                "definition": "Leads labelled for review that the system auto-qualified "
                "(and therefore synced and drafted) over all leads labelled "
                "for review. The costly error.",
            },
            "intent_accuracy": rate(intent_hits, intent_total),
        }

    out["agreement"] = {
        "definition": "The system's decision from the first valid assessment (model output + "
        "deterministic rules) compared with the label written before the run. "
        "Labels: qualified or needs_review; intent only where unambiguous.",
        "labelled_new_leads": agreement(labelled({"new_lead"})),
        "all_content_only_scenarios": agreement(labelled(CONTENT_ONLY_KINDS)),
    }
    injection = [s for s in scenarios if s["kind"] == "prompt_injection"]
    out["guardrails"] = {
        "prompt_injection_held_for_review": {
            **rate(sum(1 for s in injection if s["passed"]), len(injection)),
            "definition": "Injection attempts held for a person with no CRM record and no draft.",
        },
        "prompt_injection_flagged_by_pattern_check": rate(
            sum(1 for s in injection if s["observed"].get("flagged_by_pattern")), len(injection)
        ),
        "invalid_input_rejected": rate(
            sum(1 for s in scenarios if s["kind"] == "invalid_input" and s["passed"]),
            kinds.get("invalid_input", 0),
        ),
        "unauthenticated_webhooks_rejected": rate(
            sum(1 for s in scenarios if s["kind"] == "webhook_auth" and s["passed"]),
            kinds.get("webhook_auth", 0),
        ),
    }

    # --- review rate --------------------------------------------------------------------------
    decided = [lead for lead in leads.values() if lead["assessments"]]
    review = [
        lead
        for lead in decided
        if (first_valid(lead) or {"decision": "needs_review"})["decision"] == "needs_review"
    ]
    out["human_review"] = {
        "leads_held_for_review": {
            **rate(len(review), len(decided)),
            "definition": "Leads whose first qualification result was "
            "needs_review (including duplicate, conflict, "
            "injection and invalid-output holds), over all "
            "leads that were qualified at least once.",
        },
    }

    # --- timings ------------------------------------------------------------------------------
    clean_emails = {e.lower() for s in scenarios if s["kind"] in CLEAN_KINDS for e in s["emails"]}
    to_qual, to_crm, wf = [], [], []
    for lead in leads.values():
        if lead["email"].lower() not in clean_emails:
            continue
        q = first_audit(lead, "qualification.completed")
        if q:
            to_qual.append((ts(q["ts"]) - received_at(lead)).total_seconds())
        synced_at = first_audit(lead, "crm.synced")
        if synced_at:
            to_crm.append((ts(synced_at["ts"]) - received_at(lead)).total_seconds())
        wf += [
            w["workflow_duration_ms"] / 1000
            for w in lead["workflow_events"]
            if w["event_type"] == "lead.received" and w["workflow_duration_ms"]
        ]
    recovery_times = []
    for s in transient:
        lead = by_email.get(s["emails"][0].lower())
        done = lead and (
            first_audit(lead, "crm.synced") or first_audit(lead, "qualification.completed")
        )
        if lead and done and s["passed"]:
            recovery_times.append((ts(done["ts"]) - received_at(lead)).total_seconds())
    approvals = [s for s in scenarios if s["kind"] == "human_approval" and s["passed"] is not None]
    delivery = []
    for s in approvals:
        lead = by_email.get(s["emails"][0].lower()) if s["emails"] else None
        if lead:
            a, d = first_audit(lead, "follow_up.approved"), first_audit(lead, "follow_up.delivered")
            if a and d:
                delivery.append((ts(d["ts"]) - ts(a["ts"])).total_seconds())
    out["timings_s"] = {
        "definition": "Clean scenarios only (no injected failure), one lead in flight at a "
        "time. Receipt = when the API stored the submission.",
        "receipt_to_qualification": dist(to_qual),
        "receipt_to_crm_sync": dist(to_crm),
        "n8n_lead_pipeline_execution": dist(wf),
        "receipt_to_completion_with_injected_transient_failure": dist(recovery_times),
        "approval_to_sink_delivery": dist(delivery),
    }

    # --- human approval -----------------------------------------------------------------------
    approved = [s for s in approvals if s["checks"].get("delivered_once_to_sink") is not None]
    rejected = [s for s in approvals if "nothing_sent_after_reject" in s["checks"]]
    delivered = sum(1 for s in approved if s["checks"].get("delivered_once_to_sink"))
    out["follow_ups"] = {
        "drafts_created": sum(len(lead["drafts"]) for lead in leads.values()),
        "drafts_by_generator": dict(
            Counter(d["generated_by"] for lead in leads.values() for d in lead["drafts"])
        ),
        "approved_and_delivered_to_sink": rate(delivered, len(approved)),
        "rejected_and_not_sent": rate(sum(1 for s in rejected if s["passed"]), len(rejected)),
        "edited_text_delivered": rate(
            sum(1 for s in approved if s["checks"].get("edited_text_delivered")),
            sum(1 for s in approved if "edited_text_delivered" in s["checks"]),
        ),
        "messages_in_sink": raw.get("mail_total"),
        "messages_without_approval": (raw.get("mail_total") or 0) - delivered,
        "definition": "Every message in the mail sink must belong to an approved draft. No "
        "message ever goes to a real recipient: delivery mode is `sink`.",
    }

    # --- API latency seen by the harness ------------------------------------------------------
    def lat(endpoint: str, statuses: set[int]) -> dict[str, Any]:
        return dist(
            [r["ms"] for _, r in reqs if r["endpoint"] == endpoint and r["status"] in statuses]
        )

    out["api_latency_ms"] = {
        "definition": "Round trip measured by the harness inside the stack network, through "
        "the nginx web container, sequential requests.",
        "form_submission_accepted": lat("form", {202}),
        "form_submission_rejected_422": lat("form", {422}),
        "webhook_accepted": lat("webhook", {202}),
        "webhook_rejected_401_404_409": lat("webhook", {401, 404, 409}),
    }
    if "k6" in extras:
        k6: dict[str, Any] = {}
        for key, metric in extras["k6"].get("metrics", {}).items():
            if key.startswith("http_req_duration{name:"):
                name = key.split(":", 1)[1].rstrip("}")
                values = metric.get("values", metric)
                k6[name] = {
                    "p50": round(values["med"], 1),
                    "p95": round(values["p(95)"], 1),
                    "p99": round(values["p(99)"], 1),
                    "max": round(values["max"], 1),
                }
        checks = extras["k6"].get("metrics", {}).get("checks", {})
        out["api_latency_ms"]["k6"] = {
            "definition": "k6 from a container on the stack network after the benchmark "
            "(the database holds the benchmark's leads); see loadtest/api.js.",
            "endpoints": k6,
            "checks_passed_rate": (checks.get("values", checks) or {}).get("rate"),
        }
    if "resources" in extras:
        out["resources"] = extras["resources"]
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("raw", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    for name in ("resources", "k6", "environment", "model"):
        parser.add_argument(f"--{name}", type=Path)
    args = parser.parse_args()
    raw = json.loads(args.raw.read_text(encoding="utf-8"))
    extras = {
        name: json.loads(getattr(args, name).read_text(encoding="utf-8"))
        for name in ("resources", "k6", "environment", "model")
        if getattr(args, name) and getattr(args, name).exists()
    }
    results = compute(raw, extras)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: results[k] for k in ("scenarios",)}, indent=1)[:2000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
