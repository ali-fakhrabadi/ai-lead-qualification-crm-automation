#!/usr/bin/env bash
# Run the n8n workflow harness in a disposable stack and remove it afterwards.
#
#   n8n/tests/harness/run.sh          # report: n8n/tests/harness/out/n8n-harness-report.json
#
# Throw-away secrets are generated for this run only (never the deployment's). The stack is
# on its own internal network; nothing is published on the host.
set -euo pipefail
cd "$(dirname "$0")"
export HARNESS_SECRETS
HARNESS_SECRETS=$(mktemp -d)
export HARNESS_UID HARNESS_GID HARNESS_OUT_DIR="$PWD/out"
HARNESS_UID=$(id -u)
HARNESS_GID=$(id -g)
cleanup() {
  if [ -n "${HARNESS_KEEP:-}" ]; then
    echo "kept the harness stack (secrets in $HARNESS_SECRETS); remove it with:" >&2
    echo "  HARNESS_SECRETS=$HARNESS_SECRETS docker compose -f $PWD/compose.yaml --profile run down -v" >&2
    return
  fi
  docker compose --profile run down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$HARNESS_SECRETS"
}
trap cleanup EXIT
for name in dispatch_secret internal_token n8n_encryption_key n8n_runners_auth_token \
            n8n_owner_password; do
  head -c 32 /dev/urandom | base64 | tr -d '/+=\n' > "$HARNESS_SECRETS/$name"
done
chmod 711 "$HARNESS_SECRETS"
chmod 644 "$HARNESS_SECRETS"/*
mkdir -p out
docker compose up -d --wait api mailpit n8n n8n-runners
status=0
docker compose --profile run run --rm -e HARNESS_ONLY="${HARNESS_ONLY:-}" harness || status=$?
if [ "$status" -ne 0 ]; then
  docker compose logs --no-color --tail 80 n8n > out/n8n.log 2>&1 || true
fi
exit "$status"
