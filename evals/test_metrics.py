"""Tests of the benchmark metric definitions (pytest evals/test_metrics.py)."""

from __future__ import annotations

from typing import Any

import metrics
import report


def lead(
    email: str,
    decision: str,
    intent: str = "new_project",
    faults: list[str] | None = None,
    received: str = "10:00:00",
    qualified: str = "10:01:30",
    synced: str | None = "10:01:40",
) -> dict[str, Any]:
    history = [
        {
            "action": "qualification.completed",
            "ts": f"2026-11-19T{qualified}+00:00",
            "details": {
                "status": "valid",
                "repair_attempted": False,
                "injected_faults": faults or [],
                "latency_ms": 80000,
                "prompt_tokens": 540,
                "completion_tokens": 130,
            },
        }
    ]
    if synced:
        history.append({"action": "crm.synced", "ts": f"2026-11-19T{synced}+00:00", "details": {}})
    return {
        "id": email,
        "email": email,
        "status": "follow_up_draft_ready",
        "decision": decision,
        "events": [{"received_at": f"2026-11-19T{received}+00:00"}],
        "assessments": [
            {
                "status": "valid",
                "decision": decision,
                "created_at": f"2026-11-19T{qualified}+00:00",
                "parsed": {"intent_category": intent},
            }
        ],
        "history": history,
        "workflow_events": [{"event_type": "lead.received", "workflow_duration_ms": 95000}],
        "crm": {"remote_contact_id": "1", "status": "synced"} if synced else None,
        "crm_contacts": 1 if synced else 0,
        "drafts": [],
    }


def scenario(
    sid: str, kind: str, email: str, expect: dict[str, Any], passed: bool = True, **extra: Any
) -> dict[str, Any]:
    return {
        "id": sid,
        "kind": kind,
        "case": None,
        "emails": [email],
        "expect": expect,
        "requests": extra.pop(
            "requests",
            [
                {
                    "endpoint": "form",
                    "status": 202,
                    "ms": 40.0,
                    "replayed": False,
                    "outcome": "created",
                }
            ],
        ),
        "checks": extra.pop("checks", {"ok": passed}),
        "observed": extra.pop("observed", {}),
        "timed_out": False,
        "error": None,
        "passed": passed,
    }


def raw() -> dict[str, Any]:
    leads = {
        "a": lead("a@x.example", "qualified"),
        "b": lead("b@x.example", "qualified"),  # labelled for review: a false auto-qualification
        "c": lead("c@x.example", "needs_review", "job_application", synced=None),
        "d": lead("d@x.example", "qualified", faults=["unavailable"], qualified="10:04:00"),
    }
    scenarios = [
        scenario(
            "S001", "new_lead", "a@x.example", {"decision": "qualified", "intent": "new_project"}
        ),
        scenario("S002", "new_lead", "b@x.example", {"decision": "needs_review", "intent": None}),
        scenario(
            "S003",
            "new_lead",
            "c@x.example",
            {"decision": "needs_review", "intent": "job_application"},
        ),
        scenario(
            "S004",
            "webhook_replay",
            "a@x.example",
            {"decision": "qualified"},
            requests=[
                {
                    "endpoint": "webhook",
                    "status": 202,
                    "ms": 30.0,
                    "replayed": r,
                    "outcome": "created",
                }
                for r in (False, True, True)
            ],
            checks={"one_lead": True, "one_workflow_run": True},
        ),
        scenario("S005", "llm_unavailable", "d@x.example", {"decision": "qualified"}),
    ]
    return {
        "scenarios": scenarios,
        "leads": leads,
        "crm": {"requests": 12, "faulted_requests": 0, "duplicate_emails": 0},
        "mail_total": 0,
        "stats": {},
        "config": {},
    }


def test_agreement_counts_the_confusion_matrix_and_false_auto_qualification() -> None:
    res = metrics.compute(raw(), {})
    a = res["agreement"]["labelled_new_leads"]
    assert a["confusion"] == {
        "expected_qualified__got_qualified": 1,
        "expected_qualified__got_needs_review": 0,
        "expected_review__got_qualified": 1,
        "expected_review__got_needs_review": 1,
    }
    assert a["false_auto_qualification_rate"]["rate"] == 0.5
    assert a["intent_accuracy"] == {"numerator": 2, "denominator": 2, "rate": 1.0}


def test_replays_count_as_suppressed_duplicates() -> None:
    sup = metrics.compute(raw(), {})["intake"]["duplicate_deliveries_suppressed"]
    assert (sup["numerator"], sup["denominator"]) == (2, 2)


def test_runs_with_injected_faults_are_left_out_of_model_and_clean_timings() -> None:
    res = metrics.compute(raw(), {})
    assert res["model"]["qualification_runs_without_injected_faults"] == 3
    assert res["timings_s"]["receipt_to_qualification"]["p50"] == 90.0
    assert res["recovery"]["transient_failures_recovered"]["denominator"] == 1


def test_percentiles_use_nearest_rank() -> None:
    assert metrics.pct([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 95) == 10
    assert metrics.pct([1, 2, 3, 4], 50) == 2
    assert metrics.pct([], 50) is None


def test_report_renders_without_unmeasured_crashes(tmp_path: Any, capsys: Any) -> None:
    import json
    import sys

    path = tmp_path / "results.json"
    path.write_text(json.dumps(metrics.compute(raw(), {})), encoding="utf-8")
    sys.argv = ["report.py", str(path)]
    assert report.main() == 0
    out = capsys.readouterr().out
    assert "| Scenarios executed | 5 |" in out
    assert "NOT MEASURED" in out  # k6 and resources were not given
