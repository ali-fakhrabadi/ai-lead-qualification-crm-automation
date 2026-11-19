#!/usr/bin/env bash
# Record the walkthrough scenes from the running stack (real model, real workflows).
# Output: e2e/out/demo/*.webm, joined into the final MP4 as described in
# docs/demo/video-recording-notes.md. Needs a stack where the demo lead does not exist yet.
set -euo pipefail
cd "$(dirname "$0")/.."
docker build -q -t lead-automation-e2e \
  --build-arg PIP_INDEX_URL="${PIP_INDEX_URL:-https://pypi.org/simple}" e2e >/dev/null
mkdir -p e2e/out/demo
mounts=()
for name in demo_password webhook_secrets internal_token fault_admin_token crm_admin_token \
            crm_token n8n_owner_password; do
  mounts+=(-v "$PWD/secrets/$name:/run/secrets/$name:ro")
done
docker run --rm --network "${E2E_NETWORK:-lead-automation_backend}" --ipc=host \
  --user "$(id -u):$(id -g)" -e HOME=/tmp -e PYTHONDONTWRITEBYTECODE=1 \
  -e E2E_N8N_OWNER_EMAIL="${N8N_OWNER_EMAIL:-owner@example.com}" \
  -v "$PWD/e2e:/e2e:ro" -v "$PWD/e2e/out:/out" "${mounts[@]}" \
  lead-automation-e2e python record_demo.py
