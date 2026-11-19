# CRM integration

One adapter (`backend/app/crm/client.py`, `HubSpotStyleClient`) speaks the HubSpot CRM v3/v4
API. The bundled **demo CRM** (`backend/crm_demo/`) implements the subset of that API the adapter
uses, with its own database and token, so the whole pipeline runs locally without an account.

| `LEAD_CRM_PROVIDER` | Target | Status |
|---|---|---|
| `demo` (default) | the `crm-demo` container (`LEAD_CRM_BASE_URL=http://crm-demo:8100`) | used by every test, the end-to-end runs and the benchmark |
| `hubspot` | `https://api.hubapi.com` with a private-app token in `secrets/crm_token` | **not verified against a real HubSpot account in this project** (no sandbox credentials were available); the request shapes follow HubSpot's public API and are exercised against the demo CRM |
| `none` | no CRM step; qualified leads go straight to the draft | |

## What a sync does

1. `POST /crm/v3/objects/contacts/search` by email (`EQ`); update the contact if found
   (`PATCH /crm/v3/objects/contacts/{id}`), otherwise create it
   (`POST /crm/v3/objects/contacts`). A `409 Contact already exists. Existing ID: …` (another
   writer created it meanwhile) is resolved to the existing id: never a second contact.
2. With a company domain (from the website or a non-free-mail email domain): search the company
   by `domain`, create it if missing, then associate contact and company
   (`PUT /crm/v4/objects/contact/{id}/associations/default/company/{id}`).
3. Add one qualification note per assessment (`POST /crm/v3/objects/notes`, association type
   202 to the contact). The note holds the decision, score, intent, fit, urgency, the model's
   short summary and the review reasons, never the raw message.
4. A hash of the properties skips an update when nothing changed.

Every HTTP call is stored in `crm_attempts` (operation, outcome, status, duration) and the sync
record (`crm_syncs`) keeps the remote ids, the attempt count and the last error code, all shown on
the lead page.

## Properties

| CRM property | Source |
|---|---|
| `email`, `firstname`, `lastname`, `company`, `jobtitle`, `website`, `country` | lead fields (standard HubSpot properties) |
| `lifecyclestage` | constant `lead` |
| `lq_reference`, `lq_decision`, `lq_score`, `lq_intent`, `lq_interest` | custom properties: create them in HubSpot first (single-line text, group "Lead qualification"), or remove them from the map |

To rename or drop properties, set `LEAD_CRM_PROPERTY_MAP_FILE` to a JSON file with the same shape
as `DEFAULT_MAP` in `backend/app/crm/mapping.py`.

## Errors

| Answer | Classified as | What happens |
|---|---|---|
| timeout, connection error | transient | retried by the adapter (3 attempts, full jitter 0.5-4 s) |
| `429`, `5xx` | transient | same, honouring `Retry-After`; if still failing, the API answers n8n `503 crm_unavailable` and n8n retries the step with longer backoff |
| `401` / `403` / `404` / other `4xx` | permanent (`unauthorized`, `forbidden`, `not_found`, `rejected`) | no retry; the lead goes to `failed` with the code; an operator fixes the cause and presses **Retry** |

## Using HubSpot

1. Create a private app with the scopes `crm.objects.contacts.read/write`,
   `crm.objects.companies.read/write` (notes use the contacts scope); use a **developer test
   account or sandbox**, never a portal with real customers, for trying this project.
2. Put the token in `secrets/crm_token`, set `LEAD_CRM_PROVIDER=hubspot` (base URL defaults to
   `https://api.hubapi.com`), create the `lq_*` properties or a property map without them.
3. `docker compose up -d api`; submit a synthetic lead; check the contact, company and note.

Data sent to HubSpot: the contact fields above, the company name and domain, and the
qualification note. The lead's message is not sent.

## Demo CRM

A small FastAPI service with its own PostgreSQL database and role: contacts (unique email),
companies (unique domain), associations, notes and a request log. It requires a bearer token and
is reachable only on the internal Docker network. A read-only page (`/ui/contacts`) lists the
records for demos. With `CRM_DEMO_FAULTS_ENABLED=true` (evaluation overlay only) and an admin
token, `/_admin/faults` injects a status code on contact search/create/update for a given email
and number of calls; `/_admin/stats` counts records, duplicate emails and faulted requests.
