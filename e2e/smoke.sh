#!/usr/bin/env bash
# One synthetic lead through a deployed stack (production configuration). See e2e/smoke.py.
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -t lead-automation-e2e \
  --build-arg PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}" e2e >/dev/null
docker run --rm --network "${E2E_NETWORK:-lead-automation_backend}" \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -v "$PWD/e2e:/e2e:ro" \
  -v "$PWD/secrets/demo_password:/run/secrets/demo_password:ro" \
  -v "$PWD/secrets/crm_token:/run/secrets/crm_token:ro" \
  lead-automation-e2e python smoke.py
