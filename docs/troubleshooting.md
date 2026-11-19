# Troubleshooting

| Symptom | Likely cause | Check / fix |
|---|---|---|
| The web app is a blank page over plain HTTP | (fixed) an old build used `crypto.randomUUID`, which browsers expose only on HTTPS or `localhost` | rebuild `web`; the form now uses `crypto.getRandomValues` |
| Lead stays `new` | dispatcher not running, or n8n unreachable | `docker compose logs dispatcher`; dashboard → Workflow runs shows `pending`/`failed` events and the last error |
| Lead stays `pending_qualification` for minutes | the model is busy (CPU inference takes about a minute or more per lead) or unreachable | `docker compose logs api` for `model_unavailable`; check `LEAD_OLLAMA_BASE_URL` from inside the API container; the lead is kept and retried |
| Lead goes to `retry_pending` | a transient failure outlasted the retries of one run | it is re-delivered automatically with backoff; the event's attempts and last error are on the lead page |
| Lead goes to `failed` with `crm: unauthorized` / `forbidden` / `rejected` | wrong CRM token, missing scope, or a property the CRM refuses (e.g. missing custom `lq_*` properties in HubSpot) | fix the token or the property map, then **Retry** on the lead page |
| Event `dead` in Workflow runs | n8n refused or was down for all 8 delivery attempts | fix n8n, then retry the lead (a new event is written) |
| n8n run fails at "Verify event" | dispatch secret differs between the API and the n8n credential | rerun `docker compose up -d n8n-setup n8n` after changing `secrets/dispatch_secret` |
| Unexpected n8n failures are not reported | the error workflow is not published (n8n 2.x skips unpublished error workflows) | the setup service publishes all three; check `docker compose logs n8n-setup` says "published 3" |
| `409 duplicate_review_open` when approving | an open duplicate or conflict review on the lead | resolve it in Duplicate review first |
| Webhook `401 expired_signature` | partner clock drift > 300 s, or a replay of an old request | sync the partner's clock (NTP) |
| `429` with `Retry-After` | rate limits (public form 10/min per IP, webhooks 300/min, login 10/min) | wait as told; adjust `LEAD_RATE_LIMIT_*` if legitimate traffic needs more |
| Nothing arrives by email | by design: delivery goes to Mailpit | open the Mailpit UI (`127.0.0.1:${MAILPIT_UI_PORT}`, SSH tunnel) |
| n8n editor not reachable | it is published on loopback only | `ssh -L 5679:127.0.0.1:5679 <host>` then `http://localhost:5679` |
| Fault-injection endpoints answer 404 | correct in production; they exist only with the evaluation overlay | use `compose.eval.yaml` for tests only |
| `api` exits at start with a settings error | production refuses short/missing secrets, fault injection, or SMTP without the second switch | read the message; run `scripts/init-secrets.sh` |
| Migrations fail after a restore | restoring into a database that already has a newer schema | restore into an empty volume, then `docker compose run --rm migrate` |

Useful commands:

```bash
docker compose ps
```

```bash
docker compose logs --tail 100 api dispatcher n8n
```

```bash
docker compose exec -T postgres psql -U postgres -d leads -c "select status, count(*) from leads group by 1"
```
