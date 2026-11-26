# Changelog

## Unreleased

### Added

- `docs/integration-case-study.md`: the integration layer as built (signed webhooks,
  idempotency, retries in three layers, transient versus permanent errors, the CRM adapter and
  how a second connector plugs in), with the benchmark's measured numbers.
- `docs/ci-cd-and-deployment.md`: the CI jobs and a measured run, the image builds, Compose
  deployment, backup/restore and rollback with the lab deployment's results.

## 0.1.0 - 2026-11-19

First public version of the reference project.

### Added

- FastAPI backend: public lead form and HMAC-signed partner webhooks, validation and
  normalisation, idempotency and duplicate rules, lead lifecycle state machine, transactional
  outbox and dispatcher, AI qualification (Ollama / OpenAI / fixture) with a strict schema and
  deterministic rules, HubSpot-style CRM adapter and demo CRM, follow-up drafts with human
  approval and sink-only delivery, audit log, Argon2id + JWT auth with refresh rotation and
  roles, PostgreSQL rate limits, retention purge.
- n8n workflows (lead pipeline, follow-up delivery, error handler) generated from one
  definition, with tested Code nodes, a setup service that imports credentials from secrets, and
  external task runners.
- React operator dashboard: pipeline overview, leads, lead detail (qualification, CRM sync,
  duplicates, draft editor, workflow runs, history), duplicate review, follow-up approvals,
  workflow runs, audit log; public demo form.
- Docker Compose deployment with hardened containers, least-privilege database roles, internal
  network, evaluation overlay for fault injection.
- Tests: unit and integration (PostgreSQL), Code node tests, workflow harness (disposable n8n),
  15 end-to-end scenarios with Playwright, 128-scenario benchmark with the real model, k6.
- CI: lint, types, tests with coverage, frontend checks, workflow checks and harness, pip/npm
  audit, gitleaks, dependency review, Docker builds, Trivy, full-stack end-to-end in fixture mode.

### Fixed during verification

- Trivy in CI found 6 fixed HIGH/CRITICAL findings (3 + 3) in the web image's nginx 1.30.6
  packages; nginx is now upgraded to 1.30.7 from nginx.org's signed repository and the unused dynamic modules removed.
- The public form crashed over plain HTTP (`crypto.randomUUID` needs a secure context).
- The n8n error workflow never ran: n8n 2.x skips unpublished error workflows; and its Code node
  read the Settings output instead of the trigger's.
- Repeated or already-answered "missing information" items from the model lowered the score.
- A follow-up could be approved while a duplicate/conflict review was open on the lead.
- Deleting a lead failed on its events (ORM cascade).
- The API refused to start when `LEAD_LLM_NUM_THREAD` was left unset (Compose passed an empty
  string).
- `docker compose up --wait` failed because the dispatcher had no health check; it now reports
  a heartbeat written by each completed loop round.
- The benchmark found a prompt-injection attempt worded as an order to "the AI model" that the
  pattern check missed and that was auto-qualified (synced and drafted, not sent). The check was
  extended without flagging ordinary automation requests; all 8 attempts are held in a re-run.
- Benchmark tooling: nine dataset leads lacked the required company; the harness waited for
  leads that intake had refused; the metrics counted operator notifications as messages to leads.
