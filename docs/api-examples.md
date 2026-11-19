# API examples

Base URL: the web entry point, e.g. `http://localhost:8080`. Interactive OpenAPI docs are
disabled in production; the schemas are in `backend/app/schemas.py`. All examples use synthetic
data.

## Public lead form

```bash
curl -s http://localhost:8080/api/v1/public/form-config
```

```bash
curl -s -X POST http://localhost:8080/api/v1/public/leads \
  -H 'Content-Type: application/json' \
  -d '{"first_name":"Maya","last_name":"Chen","email":"maya.chen@harborview-logistics.example",
       "company":"Harborview Logistics","website":"harborview-logistics.example",
       "interest":"crm_integration","consent_contact":true,
       "submission_id":"3f1c9a7e5b2d4c6a8e0f1a2b3c4d5e6f",
       "message":"We copy about 60 web form inquiries a week into HubSpot by hand and want them synced automatically, by the end of next month."}'
```

```json
{"status": "accepted", "outcome": "created", "lead_reference": "L-1001",
 "correlation_id": "4b0d…", "replayed": false}
```

Sending the same body again (same `submission_id`) returns the same reference with
`"replayed": true`. A validation error:

```json
{"error": {"code": "validation_error", "message": "Request validation failed",
  "details": [{"field": "email", "msg": "is not a valid email address"}]}}
```

## Signed partner webhook

`X-Lead-Signature: v1=<hex HMAC-SHA256(secret, "<timestamp>.<raw body>")>`, timestamp within
300 seconds. The secret for `partner-demo` is in `secrets/webhook_secrets`.

```bash
SECRET=$(python3 -c 'import json;print(json.load(open("secrets/webhook_secrets"))["partner-demo"])')
BODY='{"event_id":"evt_partner_000123","lead":{"first_name":"Daniel","last_name":"Okafor","email":"daniel.okafor@brightline-dental.example","company":"Brightline Dental Group","interest":"crm_integration","consent_contact":true,"message":"Four clinics; we want website and ad inquiries in our CRM with a task for the front desk within three weeks."}}'
TS=$(date +%s)
SIG=$(printf '%s.%s' "$TS" "$BODY" | openssl dgst -sha256 -hmac "$SECRET" -hex | sed 's/^.* //')
curl -s -X POST http://localhost:8080/api/v1/webhooks/leads/partner-demo \
  -H 'Content-Type: application/json' -H "X-Lead-Timestamp: $TS" -H "X-Lead-Signature: v1=$SIG" \
  --data-binary "$BODY"
```

`401 invalid_signature` / `401 expired_signature` / `404` (unknown source) / `409` (same
`event_id`, different body) / `429` with `Retry-After`.

## Operator API

```bash
TOKEN=$(curl -s -X POST http://localhost:8080/api/v1/auth/login -H 'Content-Type: application/json' \
  -d "{\"email\":\"olivia.operator@northwind-automation.example\",\"password\":\"$(cat secrets/demo_password)\"}" \
  | python3 -c 'import json,sys;print(json.load(sys.stdin)["access_token"])')
```

```bash
curl -s "http://localhost:8080/api/v1/leads?status=needs_review&limit=20" -H "Authorization: Bearer $TOKEN"
```

| Method and path | Role | Purpose |
|---|---|---|
| `GET /api/v1/leads?status=&decision=&q=&limit=&offset=` | viewer | list and search |
| `GET /api/v1/leads/{id}` | viewer | detail: events, assessments, duplicates, CRM sync, drafts, workflow runs |
| `GET /api/v1/leads/{id}/history` | viewer | audit trail of the lead |
| `POST /api/v1/leads/{id}/override` `{"decision","reason"}` | operator | override the decision (reason required) |
| `POST /api/v1/leads/{id}/requalify` `{"reason"}` | operator | force a new assessment |
| `POST /api/v1/leads/{id}/retry` | operator | resume a `failed` / `retry_pending` lead |
| `DELETE /api/v1/leads/{id}` | admin | erase a lead and its records |
| `GET /api/v1/duplicates` · `POST /api/v1/duplicates/{id}/resolve` | viewer · operator | review queue; `kept_separate`, `same_person`, `update_applied`, `dismissed` |
| `GET /api/v1/follow-ups?status=draft` | viewer | drafts awaiting a decision |
| `PATCH /api/v1/follow-ups/{id}` `{"subject","body"}` | operator | edit |
| `POST /api/v1/follow-ups/{id}/approve` · `/reject` `{"note"}` | operator | decide; approval queues delivery to the sink |
| `GET /api/v1/workflow-events` · `GET /api/v1/audit-events` · `GET /api/v1/stats` | viewer | operations views |
| `GET /api/v1/settings/qualification` | viewer | rubric and provider configuration (no secrets) |
| `GET/POST/PATCH /api/v1/users` | admin | users and roles |
| `POST /api/v1/auth/refresh` · `/logout` | - | rotate / revoke the refresh token |

## Internal API (n8n only)

Reachable only on the internal Docker network with `Authorization: Bearer <internal token>`;
nginx answers `404` for `/internal/` from outside.

| Path | Answer |
|---|---|
| `GET /internal/v1/leads/{id}/next-step` | `{"next": "qualify" \| "crm_sync" \| "follow_up_draft" \| "stop" \| "done"}` |
| `POST /internal/v1/leads/{id}/qualify` `{"force"}` | `200` with the decision and next step; `503 model_unavailable` (retry); `422` (permanent) |
| `POST /internal/v1/leads/{id}/crm-sync` | `200`; `503 crm_unavailable` (retry); `422 crm_rejected` (operator) |
| `POST /internal/v1/leads/{id}/follow-up-draft` | `200` with `created` (false when it already existed) |
| `POST /internal/v1/follow-ups/{id}/deliver` | `200` with `delivered_now`; `409` unless approved |
| `POST /internal/v1/events/{id}/started` · `/outcome` | link the execution; report `success` / `stopped` / `error` |
| `POST /internal/v1/workflow-errors` | error workflow report (ids and a short message only) |
| `GET /api/health` · `GET /api/ready` | liveness · readiness (database reachable, schema present) |
