# Contributing

Issues and pull requests are welcome. Please use synthetic data only (reserved `.example`
domains), never real leads, customer records or credentials.

## Development checks

Backend (from `backend/`, PostgreSQL reachable as described in `.github/workflows/ci.yml`):

```bash
ruff check . && ruff format --check .
```

```bash
mypy app crm_demo
```

```bash
pytest --cov=app --cov=crm_demo
```

Frontend (from `frontend/`):

```bash
npm ci && npm run typecheck && npm test && npm run build
```

Workflows:

```bash
python3 scripts/build_workflows.py --check && node --test n8n/tests/code_nodes.test.mjs
```

```bash
n8n/tests/harness/run.sh
```

End to end (a running stack with the evaluation overlay):

```bash
e2e/run.sh
```

## Rules of the project

- FastAPI owns the data and the decisions; n8n orchestrates. A workflow never writes the database
  directly.
- Every step must be idempotent; add a replay test for anything new.
- The model's output is advisory: the score and decision come from `qualification/rules.py`.
  Change the rules with tests.
- Nothing is sent to a lead without a person's approval.
- Edit workflows in `scripts/build_workflows.py` / `n8n/code/`, regenerate the exports, and keep
  the harness green.
- Benchmark numbers in the docs come from `evals/run.sh` runs only. Never edit them by hand.
