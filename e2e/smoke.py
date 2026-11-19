"""Post-deployment smoke test with one synthetic lead (production configuration, no test hooks).

    e2e/smoke.sh

Submits a lead through the public form, waits for the pipeline, submits the same form again
(same submission id) and a second, identical submission, then checks: one lead, one workflow
run, at most one CRM contact, and no message in the mail sink (nothing is sent before a person
approves). Prints a short JSON report; exits non-zero if a check fails.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from typing import Any

from conftest import Api, crm_contacts, http, mail_to

BASE = "http://web:8080"


def main() -> int:
    stamp = time.strftime("%Y%m%d%H%M%S")
    email = f"smoke.{stamp}@deploy-check.example"
    lead: dict[str, Any] = {
        "first_name": "Smoke",
        "last_name": f"Test {stamp}",
        "email": email,
        "company": "Deploy Check Ltd",
        "website": "deploy-check.example",
        "interest": "crm_integration",
        "consent_contact": True,
        "message": (
            "Synthetic deployment check: we want our website inquiries synced to our CRM "
            "automatically with duplicates avoided, within the next month."
        ),
        "submission_id": secrets.token_hex(16),
    }
    report: dict[str, Any] = {"email": email}
    first = http("POST", f"{BASE}/api/v1/public/leads", lead)
    replay = http("POST", f"{BASE}/api/v1/public/leads", lead)
    again = http(
        "POST", f"{BASE}/api/v1/public/leads", {**lead, "submission_id": secrets.token_hex(16)}
    )
    report["submissions"] = [first[0], replay[0], again[0]]
    report["replayed"] = bool(replay[1].get("replayed"))
    report["second_outcome"] = again[1].get("outcome")
    api = Api("operator")
    deadline = time.time() + 1800
    detail: dict[str, Any] = {}
    while time.time() < deadline:
        found = api.lead_by_email(email)
        if found and found["status"] in ("follow_up_draft_ready", "needs_review", "failed"):
            detail = api.get(f"/api/v1/leads/{found['id']}")[1]
            if all(w["status"] in ("completed", "dead") for w in detail["workflow_events"]):
                break
        time.sleep(5)
    report["status"] = detail.get("status")
    report["decision"] = detail.get("decision")
    report["leads_with_this_email"] = len(
        [x for x in api.get(f"/api/v1/leads?q={email}")[1]["items"] if x["email"] == email]
    )
    report["workflow_runs"] = sum(
        1 for w in detail.get("workflow_events", []) if w["event_type"] == "lead.received"
    )
    report["crm_contacts"] = len(crm_contacts(email))
    report["mails_before_approval"] = len(mail_to(email))
    checks = {
        "accepted": report["submissions"] == [202, 202, 202],
        "replay_recognised": report["replayed"],
        "identical_resubmission_ignored": report["second_outcome"] == "duplicate_ignored",
        "settled": report["status"] in ("follow_up_draft_ready", "needs_review"),
        "one_lead": report["leads_with_this_email"] == 1,
        "one_workflow_run": report["workflow_runs"] == 1,
        "at_most_one_crm_contact": report["crm_contacts"] <= 1,
        "nothing_sent": report["mails_before_approval"] == 0,
    }
    report["checks"] = checks
    print(json.dumps(report, indent=2))
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
