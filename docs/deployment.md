# Deployment

Docker Compose on one Linux host (plus, optionally, a separate model host; see
[two-host-deployment.md](two-host-deployment.md)). Nothing needs a paid service: the demo CRM
and the Mailpit sink make the whole flow work locally.

## Requirements

- Docker Engine 24+ with Compose v2; 4 vCPU and 6 GB RAM for the stack without a model.
- A model: Ollama with `llama3.1:8b` (about 5 GB RAM, CPU works but is slow; a GPU or a separate
  model host is better), or `LEAD_LLM_PROVIDER=openai` with a key in `secrets/openai_api_key`.
  The model is never downloaded at start-up; pull it explicitly:

```bash
docker compose --profile ollama up -d ollama && docker compose exec ollama ollama pull llama3.1:8b
```

## Install

```bash
git clone https://github.com/ali-fakhrabadi/ai-lead-qualification-crm-automation.git && cd ai-lead-qualification-crm-automation
```

```bash
scripts/init-secrets.sh
```

```bash
cp .env.example .env
```

Edit `.env`: model URL and name, `WEB_BIND` (keep `127.0.0.1` unless the host is on a trusted
private network), `LEAD_DASHBOARD_URL`, and the n8n owner email. Then:

```bash
docker compose up -d --build --wait
```

```bash
scripts/create-demo-users.sh
```

The second command creates the three demo accounts (admin, operator, viewer) with the password
in `secrets/demo_password`. For real use, create your own accounts (the password is read from
the `LEAD_CLI_PASSWORD` environment variable, never from the command line):

```bash
read -rs LEAD_CLI_PASSWORD && export LEAD_CLI_PASSWORD
```

```bash
docker compose exec -T -e LEAD_CLI_PASSWORD api python -m app.cli create-user --email you@example.com --role admin --full-name "Your Name"
```

Open `http://<WEB_BIND>:<WEB_PORT>/` for the dashboard and `/demo-form` for the public form.

## What starts

| Service | Published | Notes |
|---|---|---|
| `postgres` | no | databases `leads`, `n8n`, `crm_demo`; roles created on first start by `deploy/postgres/initdb/10-create-roles.sh` |
| `migrate` | no | one-shot `alembic upgrade head` as the schema owner |
| `api`, `dispatcher` | no | behind `web` |
| `crm-demo` | no | internal only |
| `mailpit` | `127.0.0.1:${MAILPIT_UI_PORT}` | web UI of the sink |
| `n8n-setup` | no | one-shot import + publish |
| `n8n`, `n8n-runners` | n8n editor on `127.0.0.1:${N8N_HTTP_PORT}` | webhooks internal |
| `web` | `${WEB_BIND}:${WEB_PORT}` | the only entry point |
| `ollama` | no | optional profile `ollama` |

Health: `GET /api/health` (process) and `GET /api/ready` (database and schema). Every service
has a container health check; `docker compose up --wait` returns when all are healthy.

## Configuration

Everything is in `.env` (non-secret) and `secrets/` (secret). Main settings: `LEAD_LLM_PROVIDER`
(`ollama`, `openai`, `fixture`), `LEAD_LLM_MODEL`, `LEAD_OLLAMA_BASE_URL`, `LEAD_LLM_NUM_CTX`,
`LEAD_LLM_DRAFT_FOLLOWUPS`, `LEAD_CRM_PROVIDER` (`demo`, `hubspot`, `none`),
`LEAD_RUBRIC_FILE`, `LEAD_CRM_PROPERTY_MAP_FILE`, `LEAD_DELIVERY_MODE` (`sink`), rate limits
`LEAD_RATE_LIMIT_*`, `LEAD_RETENTION_DAYS`. Production start-up refuses missing/short secrets,
fault injection, and real SMTP without `LEAD_DELIVERY_ALLOW_REAL_RECIPIENTS=true`.

**HTTPS:** the bundled `web` service speaks HTTP. For anything reachable beyond a trusted LAN,
put a TLS-terminating reverse proxy with a real certificate in front of it and keep `WEB_BIND`
on loopback. An IP-only HTTP deployment is a lab setup, not a production one.

## Backup and restore

```bash
scripts/backup.sh
```

writes `backups/<timestamp>/{leads,n8n,crm_demo}.dump` (custom format), row counts and
checksums, online. Keep `secrets/` (especially `n8n_encryption_key`) backed up separately and
encrypted.

```bash
scripts/restore.sh --check backups/<timestamp>
```

restores into scratch databases and compares row counts without touching live data (use it to
test every backup). `scripts/restore.sh backups/<timestamp>` replaces the live databases (it
asks first, stops the writers, restores, migrates, starts).

## Upgrade

```bash
scripts/backup.sh
```

```bash
git fetch && git checkout <new tag>
```

```bash
docker compose build && docker compose up -d --wait
```

Migrations run in the `migrate` one-shot before the API starts; workflow changes are applied by
`n8n-setup` (idempotent).

## Rollback

Images are built locally and tagged `:local`; tag the running ones before an upgrade so you can
go back without rebuilding:

```bash
for i in api web; do docker tag lead-automation-$i:local lead-automation-$i:rollback; done
```

To roll back: check out the previous tag, then either rebuild or start the saved images with
`LEAD_API_IMAGE=lead-automation-api:rollback LEAD_WEB_IMAGE=lead-automation-web:rollback docker compose up -d --wait`.
If the new version ran a migration, restore the pre-upgrade backup (or `alembic downgrade` to
the previous revision first; every migration has a tested downgrade). The workflows of the
previous version are re-imported by its `n8n-setup`.

## Operations

- Dashboard → **Workflow runs**: pending, failed and dead events with their last error;
  **Audit log** for decisions.
- Logs are JSON on stdout (`docker compose logs`), with request and correlation ids.
- `docker compose exec api python -m app.cli purge` applies the retention period.
- n8n prunes executions older than 14 days.
