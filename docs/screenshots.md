# Screenshots

All screenshots were taken by the end-to-end suite (`e2e/test_scenarios.py`,
`e2e/test_screenshots.py`) from the running stack on the lab host, with the real model and
synthetic leads. Nothing is mocked or edited.

## Application

| | |
|---|---|
| ![Sign-in](../assets/screenshots/01-login.png) | **01 · Sign-in.** Operator accounts with roles (viewer, operator, admin); Argon2id passwords, short-lived tokens. |
| ![Pipeline dashboard](../assets/screenshots/02-lead-dashboard.png) | **02 · Pipeline dashboard.** Leads by stage, what needs attention (reviews, drafts, failed or dead workflow events). |
| ![Lead form](../assets/screenshots/03-lead-intake-form.png) | **03 · Public lead form.** Validation, required consent with a versioned text, hidden honeypot. |
| ![Qualification](../assets/screenshots/04-lead-detail-qualification.png) | **04 · AI qualification.** Score and decision from deterministic rules; the model's intent, fit, urgency and summary are advisory; evidence quotes are checked against the message. |
| ![Duplicate review](../assets/screenshots/05-duplicate-detection.png) | **05 · Duplicate review.** Same name at the same company domain, or the same email with conflicting details: held for a person, never merged automatically. |
| ![CRM sync](../assets/screenshots/06-crm-sync-status.png) | **06 · CRM sync.** Contact and company ids, attempts (including the injected outage that was retried), last error. |
| ![Human approval](../assets/screenshots/07-follow-up-human-approval.png) | **07 · Follow-up approval.** The operator edits the draft and approves or rejects it; nothing is sent before that. |
| ![Audit log](../assets/screenshots/08-audit-history.png) | **08 · Audit log.** Decisions, edits, approvals, deliveries, retries, rejected webhooks, denied access. |
| ![Mobile](../assets/screenshots/12-responsive-mobile-view.png) | **12 · Mobile layout.** The dashboard at 390 px wide. |

## n8n

| | |
|---|---|
| ![Workflow canvas](../assets/screenshots/09-n8n-workflow-canvas.png) | **09 · Lead pipeline workflow.** Verify the signed event, ask the API for the next step, qualification / CRM / draft with retry loops (Wait nodes), notify, report the outcome. |
| ![Successful execution](../assets/screenshots/10-n8n-successful-execution.png) | **10 · A successful execution** of a real lead. |
| ![Retry execution](../assets/screenshots/11-n8n-retry-error-handling.png) | **11 · Retry handling.** The CRM was made to fail (503) for the first calls: the run waited with backoff and called again, ending with exactly one contact. |

## Test evidence

See [assets/test-evidence/](../assets/test-evidence/) for the recorded test runs (unit and
integration tests with coverage, workflow harness, end-to-end scenarios, benchmark summary).
