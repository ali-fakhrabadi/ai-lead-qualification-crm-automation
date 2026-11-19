# Implementation plan

AI-assisted lead intake, qualification, deduplication and CRM automation with FastAPI and
self-hosted n8n. Independent reference project; synthetic data only.

## Environment (inspected 2026-11-19)

| | Lab host `24.179` | Lab host `24.176` |
|---|---|---|
| Resolved through | existing `~/.ssh/config` entry and `known_hosts` key (strict host-key checking) | same |
| OS / CPU / RAM | Debian 12, 10 vCPU, 9.7 GiB (about 7 GiB available) | Debian 12, 12 vCPU, 15.6 GiB |
| Docker | 27.3, Compose 2.29; account has sudo | 27.3, Compose 2.29; no sudo |
| Already running | other lab stacks (RAG, AIOps, monitoring) | Kubernetes control plane with unrelated workloads (ports 3000, 5432, 6379, 8000 in use); Ollama with `llama3.1` 8B on the LAN |
| Images present | n8n 2.45.6 + runners, Postgres 17.11, Python 3.12, Node 22, Playwright, k6 | Postgres 16, Python 3.12 |

The developer workstation only edits code and talks to GitHub; builds, tests and runs happen on
the lab hosts.

## Deployment topology (decision)

- **`24.179`: application and data plane.** nginx + web app, FastAPI API, outbox dispatcher,
  n8n (+ task runners), PostgreSQL (application database and a separate n8n database with
  separate users), demo CRM, Mailpit. PostgreSQL, n8n editor, Mailpit and the demo CRM are not
  published; only the web entry point is published on the LAN interface.
- **`24.176`: model inference plane.** The existing Ollama service (`llama3.1` 8B, CPU) answers
  qualification and drafting requests over the private LAN.
- **Why not the preferred split (app on `24.176`, data on `24.179`):** `24.176` is a Kubernetes
  control plane that already runs unrelated services on the ports this stack would need, and the
  available account cannot inspect or manage its firewall. Putting PostgreSQL on a LAN port only
  to cross hosts would add exposure without benefit at this scale. The two-host guide documents
  the split variant (PostgreSQL on a private interface with `hostssl` + scram, `pg_hba` limited to
  the app host) for dedicated machines.
- No domain exists, so there is no public HTTPS; the deployment is LAN-only and documented as
  such.

## Architecture

- **FastAPI modular monolith** (`backend/app`): API + webhook layer, auth (Argon2id, JWT access +
  rotating refresh tokens, roles `admin` / `operator` / `viewer`), intake and validation,
  normalization, duplicate detection, lifecycle state machine, qualification service (LLM provider
  + deterministic rules), CRM adapters, follow-up drafts and approval, outbox, audit, PostgreSQL
  rate limits. SQLAlchemy 2 + Alembic.
- **No Redis:** n8n runs in regular mode (no queue), the outbox is polled with
  `FOR UPDATE SKIP LOCKED`, and rate limits are PostgreSQL counters. One fewer stateful service.
- **Outbox dispatcher** (`python -m app.dispatcher`): delivers `lead.received` and
  `follow_up.approved` events to n8n webhooks with a signed JWT (HS256, body hash), bounded
  exponential backoff with jitter, dead-lettering. At-least-once delivery; every downstream step
  is idempotent.
- **n8n workflows** (built by `scripts/build_workflows.py`, Code nodes tested with `node --test`):
  1. Lead intake orchestration (webhook, envelope check, runs 2-4, reports the outcome).
  2. AI qualification (calls FastAPI, which calls the model, validates the schema and applies the
     rules; retries transient failures).
  3. CRM upsert (transient vs permanent errors, bounded retries).
  4. Follow-up draft (creates the draft, notifies the operator mailbox in Mailpit).
  5. Follow-up delivery (after approval; FastAPI sends only to the configured sink).
  6. Error handler (Error Trigger → FastAPI marks the lead `retry_pending` with the execution id).
- **LLM providers:** Ollama (default), OpenAI (optional), deterministic `fixture` mode for
  infrastructure tests. Strict Pydantic schema; lead text is fenced and treated as data; the model
  has no tools.
- **CRM:** adapter interface; `demo` adapter talks to a small HubSpot-like demo CRM service
  (separate container and database, real HTTP, optional fault injection for tests); `hubspot`
  adapter for the CRM v3 API (search by email, create/update, company association, note), with
  contract tests; marked externally unverified unless a real test account is used.
- **Email:** follow-ups are drafts; delivery goes to Mailpit only, unless a real SMTP adapter is
  explicitly configured and enabled.
- **Frontend:** React + TypeScript + Vite behind unprivileged nginx: public lead form, login,
  pipeline dashboard, lead list/filter/search, lead detail (qualification, duplicates, CRM sync,
  draft editor, history), duplicate review queue, workflow/outbox status, audit log.

## Lead lifecycle

`new → pending_qualification → qualified | needs_review → synced_to_crm →
follow_up_draft_ready → follow_up_approved | follow_up_rejected → closed`, plus `retry_pending`,
`failed` and `unqualified` (human decision only). Transitions are defined in one table and tested.

## Duplicates and idempotency

- Webhook: `Idempotency-Key` (or event id) unique per source; a replay returns the original
  result and never reprocesses. Signed requests carry a timestamp (5-minute tolerance).
- Same normalized email → same lead: the inquiry is attached as a new event; empty fields are
  filled, conflicting fields create a review item instead of overwriting.
- Same name + same company domain, different email → possible duplicate for review, never merged
  automatically. Similar but distinct people stay separate.
- Downstream: one assessment per lead input hash, CRM upsert keyed by email/remote id, one open
  draft per lead.

## Evaluation

At least 100 synthetic scenarios (`docs/evaluation/lead-evaluation-dataset.jsonl`, 128 built by `evals/build_dataset.py`): valid leads across intents and fit
levels, webhook replays, same-email updates, similar-but-distinct people, invalid input, prompt
injection, CRM transient/permanent failures (fault injection in the demo CRM), malformed model
output (fault injection in the provider wrapper, test mode only). Run through the deployed stack
with the real model; metrics, denominators and raw results in `docs/evaluation/`. Fixture-mode
runs are labeled as such and never reported as model performance.

## Tests

Unit (pytest), integration against real PostgreSQL, n8n workflow harness (import into a
disposable n8n, run recorded events, verify executions and side effects), browser e2e
(Playwright: the 15 required scenarios, screenshots, walkthrough video), k6 for API latency.
CI: lint, types, unit + integration with coverage, frontend checks, workflow build check + Code
node tests, pip/npm audit, gitleaks, Docker builds, Trivy, Compose stack with n8n + browser tests
in fixture mode.

## Versions

Python 3.12, FastAPI / Pydantic v2 / SQLAlchemy 2 / Alembic (versions as in the lock files),
PostgreSQL 17.11, n8n 2.45.6 + task runners 2.45.6 (current stable is 2.46.7; 2.45.6 is already
tested in this lab and loaded on the host, where Docker Hub downloads are slow; upgrading is a
one-line change), Mailpit 1.29, React 19 + Vite, nginx-unprivileged 1.30, Ollama `llama3.1` 8B.

## Order of work

1. Backend foundation: models, migrations, auth, intake, normalization, dedupe, lifecycle, outbox.
2. Qualification (providers, prompt, schema, rules), CRM adapters + demo CRM, drafts, delivery.
3. n8n workflows + setup service + dispatcher; Compose stack on `24.179`.
4. Frontend.
5. Tests, workflow harness, browser e2e, evaluation with the real model, k6.
6. CI, documentation, deployment record, branding (series style audit first), screenshots,
   test evidence, walkthrough video.
7. GitHub (public), Upwork portfolio item 08, Project Catalog **draft only**.
