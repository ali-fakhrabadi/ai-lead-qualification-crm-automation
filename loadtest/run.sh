#!/usr/bin/env bash
# Run a k6 script against the Compose deployment from a container on its internal network.
#
#   loadtest/run.sh api.js -e VUS=5 -e DURATION=60s
#
# Signs in as the demo operator (scripts/create-demo-users.sh) and writes a JSON summary to
# loadtest/out/<script>-summary.json (the access token returned by setup() is removed).
set -euo pipefail
cd "$(dirname "$0")/.."
script=$1
shift
mkdir -p loadtest/out
status=0
docker run --rm --network "${LOADTEST_NETWORK:-lead-automation_backend}" \
  --user "$(id -u):$(id -g)" \
  -v "$PWD/loadtest:/scripts:ro" -v "$PWD/loadtest/out:/out" \
  -v "$PWD/secrets/demo_password:/run/secrets/demo_password:ro" \
  -v "$PWD/secrets/webhook_secrets:/run/secrets/webhook_secrets:ro" \
  "${K6_IMAGE:-grafana/k6:2.3.0}" run --quiet \
  -e BASE_URL=http://web:8080 -e EMAIL=olivia.operator@northwind-automation.example \
  -e PASSWORD_FILE=/run/secrets/demo_password -e WEBHOOK_SECRETS_FILE=/run/secrets/webhook_secrets \
  --summary-export "/out/${script%.js}-summary.json" "$@" "/scripts/$script" || status=$?

python3 - "loadtest/out/${script%.js}-summary.json" <<'PY'
import json
import sys

path = sys.argv[1]
with open(path, encoding="utf-8") as f:
    summary = json.load(f)
summary.pop("setup_data", None)
with open(path, "w", encoding="utf-8") as f:
    json.dump(summary, f, indent=2)
PY
exit "$status"
