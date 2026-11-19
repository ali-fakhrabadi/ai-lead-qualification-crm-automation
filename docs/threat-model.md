# Threat model

Scope: the Compose deployment (web, API, dispatcher, n8n, runners, PostgreSQL, demo CRM,
Mailpit) and the model host. Assets: lead personal data, CRM records, the ability to send email
in the company's name, credentials (database, CRM token, n8n encryption key, service tokens),
and the integrity of qualification decisions.

## Trust boundaries

1. Internet/LAN → nginx (public form, webhooks, operator dashboard).
2. nginx → API (proxy headers trusted only from nginx).
3. API ↔ PostgreSQL (internal network, least-privilege roles).
4. Dispatcher → n8n webhooks; n8n → internal API (internal network, signed tokens / service token).
5. API → model host (LAN) and → CRM (demo internally, or HubSpot over HTTPS).
6. Lead text → model prompt (untrusted data inside a trusted instruction).

## Threats and mitigations (STRIDE)

| # | Threat | Mitigation | Verified by |
|---|---|---|---|
| S1 | Forged partner webhook | HMAC-SHA256 per source, constant-time compare, unknown source 404 | e2e 06, benchmark `webhook_auth`, unit tests |
| S2 | Forged event into n8n (bypassing the outbox) | JWT HS256 with issuer, audience, expiry, event id and payload hash; webhooks on the internal network only | workflow harness (wrong key, expired, tampered payload) |
| S3 | Operator account takeover | Argon2id, login rate limits, short access tokens, refresh rotation with reuse detection | unit/integration tests |
| T1 | Captured webhook replayed later | 300 s timestamp window + idempotency key | e2e 07, benchmark |
| T2 | Payload swapped under a valid dispatcher token | `payload_sha256` claim checked in n8n | workflow harness |
| T3 | Lead text tries to change the decision ("ignore previous instructions…") | fenced untrusted data, pattern check forces review, model has no tools, strict schema, rules decide, evidence grounded | e2e 12, benchmark `prompt_injection` (8 attempts), unit tests |
| T4 | Conflicting resubmission overwrites a contact | conflicting fields never overwrite; review item; approval blocked while open | e2e 03, integration tests, benchmark `same_email_conflict` |
| R1 | Operator denies approving a message | audit with user, time and note; edits are audited with the editor and the size change (the approved text is kept on the draft and is what was delivered) | integration tests, e2e 13 |
| I1 | Personal data sent to a third party | business fields only to the model; local Ollama by default; OpenAI only when configured; no message to the CRM | code review, tests on the prompt builder |
| I2 | Secrets leak via repo, logs or images | Docker secrets, git-ignored `secrets/`, gitleaks in CI, masked logs, credential ids only in exports | CI |
| I3 | Lateral movement from n8n Code nodes | external task runners without credentials or DB access; only `crypto` built-in; SSRF allow-list; env access blocked | configuration, workflow harness |
| I4 | SSRF through lead data | websites stored as host names, never fetched | unit tests |
| I5 | XSS through lead text in the dashboard | React text rendering, strict CSP | frontend tests |
| D1 | Flooding the public form or webhooks | per-IP and per-source rate limits (PostgreSQL counters), body size limits, honeypot | integration tests |
| D2 | Model host slow or down | lead kept, `retry_pending`, n8n backoff, dispatcher redelivery; nothing lost | e2e 10, benchmark `llm_unavailable` |
| D3 | CRM outage or rate limiting | adapter retries with `Retry-After`, n8n step retries, redelivery | e2e 08, workflow harness, benchmark `crm_transient` |
| E1 | Viewer performs operator actions | role checked from the database on every request; denied attempts audited | e2e 15, integration tests |
| E2 | External access to internal endpoints | nginx 404 for `/internal/`, service token, internal network | e2e 15 |
| E3 | Accidental real email to a prospect | drafts only; human approval; sink by default; real SMTP needs two explicit switches | e2e 13/14, benchmark `human_approval` |

## Residual risks

- No TLS in the LAN lab deployment; credentials and lead data cross the LAN in clear text
  between the browser and nginx, and between the API and the model host. Use HTTPS (a reverse
  proxy with a real certificate) and a private link to the model host for anything beyond a lab.
- The pattern check can miss novel injection phrasing; the structural controls still prevent
  any action, but a cleverly worded message can influence the model's suggested fit or urgency.
  The benchmark showed this once: an attempt addressed to "the AI model" passed the check, the
  model rated it a strong fit, and the lead was qualified, synced to the CRM and given a draft
  (which waited for approval; nothing was sent). The check now covers that wording and a re-run
  of all 8 attempts held every one, but new phrasings remain possible. Operators see the model's
  summary as advisory, the grounded evidence, and the raw text; the approval step is the last
  control.
- A single API token per partner source; no per-partner IP allow-list.
- Backups contain personal data and the n8n encryption key; store them encrypted and access
  controlled.
