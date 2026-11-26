# Progress log

## 2026-11-19: inspection and plan

- Both lab host identifiers resolved through the existing SSH configuration; host keys verified
  against `known_hosts` (no host-key workarounds). Resources, Docker versions, listening ports
  and existing workloads inspected read-only on both; nothing changed.
- `24.176` runs a Kubernetes control plane with unrelated services and the shared Ollama model
  server; `24.179` runs other lab stacks and has the n8n 2.45.6 images already loaded.
- Topology decided: application and data on `24.179`, model inference on `24.176`
  (see `implementation-plan.md`).
- GitHub authenticated as `ali-fakhrabadi`; repository name `ai-lead-qualification-crm-automation`
  is free.
- Checked current documentation: n8n stable is 2.46.7 (2.45.6 used, see plan), HubSpot CRM v3
  contacts search/create/update and batch upsert, Mailpit 1.29.6.

## 2026-11-19: build

- Backend, demo CRM, n8n workflows and setup service, frontend, Compose stack built; first real
  lead through the whole pipeline on the lab hosts (qualification 73.9 s with `llama3.1` on CPU).
- Unit and integration tests against real PostgreSQL; Code node tests.

## 2026-11-19: verification and fixes

Each of these was found by a test run, fixed, and covered by a test:

- End-to-end run 1: the public form rendered blank over plain HTTP (`crypto.randomUUID` exists
  only in secure contexts); the e2e client's token expired during long waits.
- End-to-end run 2: a race in the test (lead reached the draft before n8n reported the run);
  draft fields labelled by wrapping, so their accessible name included their own text.
- Benchmark preparation: the model sometimes repeated "missing information" items or listed the
  company although it was given; both lowered the score. The rules now de-duplicate them and
  ignore items the form already answers.
- Approval while a duplicate/conflict review was open is now refused (`409
  duplicate_review_open`).
- Workflow harness: the error workflow never ran (n8n 2.x skips unpublished error workflows), and
  its Code node read the Settings node's output instead of the trigger's. Both fixed; 12/12
  harness scenarios pass.
- Stack start: the API refused an empty `LEAD_LLM_NUM_THREAD` from Compose; `up --wait` failed
  because the dispatcher had no health check (now a heartbeat).
- Shared model host: another lab application calls the same Ollama with different runner
  options, which forced a model reload on every switch (3-5 minutes per call). This deployment
  now sends the same options.

## 2026-11-19: benchmark

- 128 scenarios through the deployed stack with the real model, 17:12-21:28 UTC, one resume
  after the dataset fix (nine leads without the required company). 126 passed every check.
- Failures: S098 (prompt injection auto-qualified: synced and drafted, nothing sent) and S046
  (a 14-character message refused by the form's 20-character minimum; the label was wrong).
- Fix for S098: the pattern check covers addressing the model, bypassing rules or review, fake
  system markers and destructive orders; ordinary automation wording stays unflagged (unit
  tests, plus a test over the whole dataset). Re-run of the 8 injection scenarios on the rebuilt
  stack with new addresses: 8 / 8 held and flagged, no CRM record, no draft, no message.
- Metrics fix: operator notifications ("draft waiting") had been counted as messages to leads.
- Report: `docs/evaluation/benchmark-report.md`.

## 2026-11-26: integration and CI/CD documentation

- Two documentation pages for portfolio buyers who look for API integration and CI/CD work,
  written from the code and existing measurements only (no new runs):
  `docs/integration-case-study.md` and `docs/ci-cd-and-deployment.md`; linked from the README.
- CI durations quoted from the GitHub Actions run of `3318a41` (all jobs green, about 13 minutes
  from push to the last job).
- A second connector is proposed, not built; it will be built against a local mock with tests
  once chosen.
