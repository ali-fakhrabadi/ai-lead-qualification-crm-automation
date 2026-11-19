# Idempotency, duplicates and retries

Delivery is **at-least-once** everywhere (partner webhooks, the outbox, n8n retries), so every
step is written to be safe when it runs twice.

## Intake: the same thing sent again

| Situation | Rule | Answer |
|---|---|---|
| Same partner event delivered again (same `source` + `event_id`, same payload) | Unique `(source, idempotency_key)`; the stored result is returned | `202`, `replayed: true`, same reference, no new event, no new workflow run |
| Same event id, **different** payload | Refused: an id must never mean two things | `409` |
| Same form submission (double click, browser retry) | The form sends a random `submission_id` per filled-in form; it is the idempotency key | `202`, `replayed: true` |
| Same email, identical content, new submission | Recorded as a lead event, nothing else changes | `202`, outcome `duplicate_ignored` |
| Same email, new message, no conflicting fields | Message updated, empty optional fields filled in; reopens a finished lead, never restarts one mid-pipeline | outcome `updated_existing` |
| Same email, conflicting fields (name, company, job title, website, country) | **Not overwritten.** A `same_email_conflict` review item holds the proposed values | outcome `needs_review` |
| Same name + same company domain, different mailbox; or `name+tag@` of an existing mailbox | Flagged `possible_same_person` for review, **never merged** | separate lead, `possible_duplicate` reason |
| Same name, different company | Independent people | separate lead, no review |

Email normalization lowercases and trims, keeps dots and plus tags (they can be different
mailboxes; the plus-tag rule above only flags), and refuses IP-literal domains. Free-mail
domains never count as a company domain.

While a duplicate review is open, the lead is held at `needs_review` and **its follow-up cannot
be approved** (`409 duplicate_review_open`): the same person may have written again with
different details, or two records may be one person.

Signed webhooks carry `X-Lead-Timestamp` and `X-Lead-Signature: v1=<hex HMAC-SHA256 of
"timestamp.body">`; requests older or newer than 300 s are refused (`expired_signature`), so a
captured request cannot be replayed later, and a replay inside the window is answered from the
idempotency record.

## Downstream: the same step run twice

| Step | Why a second run is harmless |
|---|---|
| Dispatcher → n8n | The token binds the event id and the SHA-256 of the exact payload; n8n asks the API for the lead's **next step**, so a replayed event resumes where the lead stands |
| Qualification | One valid assessment per lead input hash (lead fields + rubric version + provider + model); a repeat reuses it unless an operator forces re-qualification |
| CRM sync | Contact searched by email first; create → `409 Contact already exists` is resolved to the existing id; company by domain; a payload hash skips unchanged updates; one note per assessment |
| Draft | At most one open draft per lead (partial unique index) |
| Approval | Only a `draft` can be approved, once; approval writes one outbox event |
| Delivery | A delivered draft is never sent again; delivery goes to the configured sink only |
| Outcome reports | Keyed by event id and execution id |

## Retries

| Layer | Transient (retried) | Permanent (stops) | Policy |
|---|---|---|---|
| CRM adapter (per HTTP call) | timeout, connection error, 429, 5xx | other 4xx | up to 3 attempts, full jitter from 0.5 s, cap 4 s, honours `Retry-After`; every attempt is stored in `crm_attempts` |
| n8n step (per API call) | connection error, 429, 5xx (e.g. `crm_unavailable`, `model_unavailable`) | 4xx (e.g. `crm_rejected`) | 3 attempts per run; wait = full jitter on `base × 2^(attempt-1)`, at least `Retry-After`, at most 60 s; base 20 s for model steps, 10 s for CRM and delivery |
| Workflow run | attempts exhausted → reported `error` | permanent → reported `stopped` | the API re-schedules the event (`failed`, backoff) up to the dispatcher's limit |
| Dispatcher (per event) | n8n unreachable or non-2xx | - | up to 8 attempts, exponential backoff with jitter from 2 s, cap 300 s, then `dead` (visible in the dashboard) |
| Lost run | an event dispatched but never reported on | - | re-delivered after 15 minutes |
| Unexpected workflow failure | the error workflow reports the execution id | - | the lead goes to `retry_pending` at its stage, the event is re-scheduled |

A permanent failure is **never retried automatically**: the lead shows `failed` with the error
code, and an operator retries it from the dashboard after fixing the cause.

## How it is verified

- Unit and integration tests (`backend/tests`): idempotency keys, conflicting payloads, same-email
  rules, possible duplicates, outbox claims, CRM retries and `Retry-After`, one draft per lead,
  delivery once.
- Workflow harness (`n8n/tests/harness`): the real workflows in a disposable n8n against a
  scriptable API: replays, CRM 429 with `Retry-After`, CRM outage exhausting the attempts,
  permanent CRM error, model outage, invalid model output, draft retry, refused tokens, the error
  workflow, delivery replay.
- End-to-end (`e2e/`) and the benchmark (`evals/`): replays, duplicate submissions, injected CRM
  and model failures through the deployed stack; duplicate CRM contacts are counted afterwards.
