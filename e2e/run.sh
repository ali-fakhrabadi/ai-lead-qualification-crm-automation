#!/usr/bin/env bash
# Build the e2e image and run the end-to-end scenarios against the running Compose stack.
#
#   docker compose -f compose.yaml -f compose.eval.yaml up -d --build
#   scripts/create-demo-users.sh
#   e2e/run.sh                         # all 15 scenarios + screenshots
#   e2e/run.sh -m "not n8n_ui"         # skip the n8n editor screenshots
#
# The container joins the stack's internal network (nothing extra is published) and reads only
# the secrets it needs. Use a fresh stack: the scenarios expect their synthetic leads not to
# exist yet. Screenshots go to e2e/out/screenshots, the JUnit report to e2e/out/junit.xml.
set -euo pipefail
cd "$(dirname "$0")/.."

docker build -q -t lead-automation-e2e \
  --build-arg PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}" e2e >/dev/null
mkdir -p e2e/out
mounts=()
for name in demo_password webhook_secrets internal_token fault_admin_token crm_admin_token \
            crm_token n8n_owner_password; do
  mounts+=(-v "$PWD/secrets/$name:/run/secrets/$name:ro")
done
docker run --rm --network "${E2E_NETWORK:-lead-automation_backend}" --ipc=host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -e E2E_PIPELINE_TIMEOUT_S="${E2E_PIPELINE_TIMEOUT_S:-900}" \
  -e E2E_N8N_OWNER_EMAIL="${N8N_OWNER_EMAIL:-owner@example.com}" \
  -v "$PWD/e2e:/e2e:ro" -v "$PWD/e2e/out:/out" "${mounts[@]}" \
  lead-automation-e2e python -m pytest -p no:cacheprovider -v \
  --junitxml=/out/junit.xml "$@"
