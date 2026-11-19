# n8n

| Path | Contents |
|---|---|
| `workflows/` | Generated exports: lead pipeline, follow-up delivery, error handler. No credential values. |
| `code/` | Code node scripts embedded by `scripts/build_workflows.py` |
| `setup/setup.mjs` | One-shot importer run by the `n8n-setup` service: credentials from secrets, workflows, publish |
| `setup/task-runners.json` | External task runner configuration (only the `crypto` built-in allowed) |
| `tests/code_nodes.test.mjs` | Unit tests of the Code nodes (`node --test n8n/tests/code_nodes.test.mjs`) |
| `tests/harness/` | Workflow harness: a disposable n8n plus a scriptable API stub (`n8n/tests/harness/run.sh`) |

Rebuild the exports after a change and check them:

```bash
python3 scripts/build_workflows.py
```

```bash
python3 scripts/build_workflows.py --check
```

Apply to a running stack:

```bash
docker compose up -d n8n-setup n8n
```

The editor listens on `127.0.0.1:${N8N_HTTP_PORT}` only; open it through an SSH tunnel and sign
in as `N8N_OWNER_EMAIL` with the password in `secrets/n8n_owner_password`.

See [docs/n8n-workflows.md](../docs/n8n-workflows.md) for what each node does.
