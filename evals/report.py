"""Render the measured tables of docs/evaluation/benchmark-report.md from results.json.

    python evals/report.py docs/evaluation/results.json > /tmp/tables.md

Only numbers from results.json appear; a value that was not measured prints as NOT MEASURED.
The report's narrative (findings, failures, remediation) is written by hand around the tables.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

NM = "NOT MEASURED"


def r(x: dict[str, Any] | None) -> str:
    if not x or x.get("denominator") in (None, 0):
        return NM
    return f"{x['numerator']} / {x['denominator']} ({100 * x['rate']:.1f} %)"


def d(x: dict[str, Any] | None, unit: str = "s") -> str:
    if not x or not x.get("n"):
        return NM
    return (f"p50 {x['p50']} {unit} · p95 {x['p95']} {unit} · max {x['max']} {unit} "
            f"(n = {x['n']})")


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    res = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out: list[str] = []
    w = out.append
    sc, it, crm, rec = res["scenarios"], res["intake"], res["crm"], res["recovery"]
    model, ag, tm, fu = res["model"], res["agreement"], res["timings_s"], res["follow_ups"]

    w("### Scenarios\n")
    w("| Metric | Result |\n|---|---|")
    w(f"| Scenarios executed | {sc['executed']} |")
    w(f"| All checks passed | {sc['all_checks_passed']} |")
    w(f"| With a failed check | {sc['with_a_failed_check']} |")
    w(f"| Not applicable | {sc['not_applicable']} |")
    w(f"| Leads created | {sc['leads_created']} |\n")

    w("### Intake, duplicates, CRM\n")
    w("| Metric | Result |\n|---|---|")
    w(f"| Webhook requests (accepted / rejected) | {it['webhook_requests']} "
      f"({it['webhook_accepted_202']} / {json.dumps(it['webhook_rejected'])}) |")
    w(f"| Form requests (accepted / rejected) | {it['form_requests']} "
      f"({it['form_accepted_202']} / {json.dumps(it['form_rejected'])}) |")
    w(f"| Rate-limited answers (429, retried by the harness) | {it['rate_limited_429']} |")
    w(f"| Duplicate deliveries safely suppressed | {r(it['duplicate_deliveries_suppressed'])} |")
    dup = it["duplicate_crm_contacts"]
    w(f"| Duplicate CRM contacts | {dup['leads_with_more_than_one_contact']} of "
      f"{dup['leads_with_a_crm_contact']} leads with a contact (CRM duplicate emails: "
      f"{dup['crm_duplicate_emails']}) |")
    w(f"| CRM sync operations succeeded / failed | {crm['sync_operations_succeeded']} / "
      f"{crm['sync_operations_failed']} {json.dumps(crm['failed_by_code'])} |")
    w(f"| CRM HTTP calls logged / failed by injection | {crm['http_calls_logged_by_crm']} / "
      f"{crm['http_calls_failed_by_injection']} |")
    w(f"| Transient failures recovered without an operator | "
      f"{r(rec['transient_failures_recovered'])} |")
    w(f"| Permanent failures stopped, then recovered by one operator retry | "
      f"{r(rec['permanent_failures_stopped_then_recovered_by_operator'])} |\n")

    w("### Model\n")
    w("| Metric | Result |\n|---|---|")
    w(f"| Qualification runs without injected faults | "
      f"{model['qualification_runs_without_injected_faults']} |")
    w(f"| Schema-valid on the first reply | {r(model['schema_valid_first_reply'])} |")
    w(f"| Schema-valid after at most one repair | {r(model['schema_valid_after_one_repair'])} |")
    w(f"| Unexpected provider errors | {model['unexpected_provider_errors']} |")
    w(f"| Model latency per qualification | {d(model['latency_s'])} |")
    w(f"| Prompt / completion tokens | {d(model['prompt_tokens'], 'tok')} / "
      f"{d(model['completion_tokens'], 'tok')} |\n")

    for key, title in (("labelled_new_leads", "49 labelled new leads"),
                       ("all_content_only_scenarios", "all scenarios whose label depends on the text only")):
        a = ag[key]
        c = a["confusion"]
        w(f"### Agreement with labels: {title}\n")
        w("| | system: qualified | system: needs_review |\n|---|---:|---:|")
        w(f"| label: qualified | {c['expected_qualified__got_qualified']} | "
          f"{c['expected_qualified__got_needs_review']} |")
        w(f"| label: needs_review | {c['expected_review__got_qualified']} | "
          f"{c['expected_review__got_needs_review']} |\n")
        w("| Metric | Result |\n|---|---|")
        w(f"| Decision accuracy | {r(a['decision_accuracy'])} |")
        w(f"| Precision of `qualified` | {r(a['qualified_precision'])} |")
        w(f"| Recall of `qualified` | {r(a['qualified_recall'])} |")
        w(f"| False auto-qualification rate | {r(a['false_auto_qualification_rate'])} |")
        w(f"| Intent accuracy (unambiguous labels) | {r(a['intent_accuracy'])} |")
        w(f"| Leads without a valid assessment (counted as needs_review) | "
          f"{a['without_a_valid_assessment']} |\n")

    g = res["guardrails"]
    w("### Guardrails and review\n")
    w("| Metric | Result |\n|---|---|")
    w(f"| Prompt-injection attempts held for review, no CRM record, no draft | "
      f"{r(g['prompt_injection_held_for_review'])} |")
    w(f"| … of which flagged by the pattern check | "
      f"{r(g['prompt_injection_flagged_by_pattern_check'])} |")
    w(f"| Invalid input rejected, no lead created | {r(g['invalid_input_rejected'])} |")
    w(f"| Unauthenticated webhooks rejected | {r(g['unauthenticated_webhooks_rejected'])} |")
    w(f"| Human-review rate | {r(res['human_review']['leads_held_for_review'])} |\n")

    w("### Timings (clean scenarios, one lead at a time)\n")
    w("| Metric | Result |\n|---|---|")
    w(f"| Receipt → qualification result | {d(tm['receipt_to_qualification'])} |")
    w(f"| Receipt → CRM sync | {d(tm['receipt_to_crm_sync'])} |")
    w(f"| n8n lead-pipeline execution | {d(tm['n8n_lead_pipeline_execution'])} |")
    w(f"| Receipt → done, with an injected transient failure | "
      f"{d(tm['receipt_to_completion_with_injected_transient_failure'])} |")
    w(f"| Approval → delivered to the sink | {d(tm['approval_to_sink_delivery'])} |\n")

    w("### Follow-ups and approval\n")
    w("| Metric | Result |\n|---|---|")
    w(f"| Drafts created | {fu['drafts_created']} {json.dumps(fu['drafts_by_generator'])} |")
    w(f"| Approved and delivered once to the sink | {r(fu['approved_and_delivered_to_sink'])} |")
    w(f"| Rejected and never sent | {r(fu['rejected_and_not_sent'])} |")
    w(f"| Edited text delivered | {r(fu['edited_text_delivered'])} |")
    w(f"| Messages in the sink / without an approval | {fu['messages_in_sink']} / "
      f"{fu['messages_without_approval']} |\n")

    lat = res["api_latency_ms"]
    w("### API latency\n")
    w("| Endpoint | Result |\n|---|---|")
    for key in ("form_submission_accepted", "form_submission_rejected_422", "webhook_accepted",
                "webhook_rejected_401_404_409"):
        w(f"| harness: {key.replace('_', ' ')} | {d(lat[key], 'ms')} |")
    k6 = lat.get("k6")
    if k6:
        for name, v in k6["endpoints"].items():
            w(f"| k6: {name.replace('_', ' ')} | p50 {v['p50']} ms · p95 {v['p95']} ms · "
              f"p99 {v['p99']} ms · max {v['max']} ms |")
    else:
        w(f"| k6 | {NM} |")
    w("")

    resources = res.get("resources")
    w("### Resources\n")
    if resources:
        w("| Container (application host) | CPU mean / peak | RAM mean / peak |\n|---|---|---|")
        for name, v in resources["app_host"]["containers"].items():
            w(f"| {name} | {v['cpu_percent_mean']} % / {v['cpu_percent_peak']} % | "
              f"{v['mem_mib_mean']} / {v['mem_mib_peak']} MiB |")
        mh = resources.get("model_host")
        if mh:
            w(f"\nModel host ({mh['logical_cpus']} logical CPUs, {mh['samples']} samples): whole "
              f"host CPU mean {mh['host_cpu_percent_mean']} %, peak {mh['host_cpu_percent_peak']} %; "
              f"Ollama CPU mean {mh['ollama_cpu_percent_mean']} %, peak "
              f"{mh['ollama_cpu_percent_peak']} % (100 % = one CPU); Ollama RSS peak "
              f"{mh['ollama_rss_mib_peak']} MiB.")
    else:
        w(NM)
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
