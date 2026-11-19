#!/usr/bin/env bash
# Restore a backup made by scripts/backup.sh.
#
#   scripts/restore.sh --check backups/<stamp>   # restore into scratch databases and compare
#                                                # row counts; the live data is not touched
#   scripts/restore.sh backups/<stamp>           # replace the live databases (asks first)
#
# A full restore stops the services that write, restores each database with its original
# owner (roles come from the init script), runs migrations and starts everything again.
set -euo pipefail
cd "$(dirname "$0")/.."
check=false
if [ "${1:-}" = "--check" ]; then check=true; shift; fi
src=${1:?usage: scripts/restore.sh [--check] backups/<stamp>}
(cd "$src" && sha256sum -c --quiet SHA256SUMS)
psql_() { docker compose exec -T postgres psql -U postgres -v ON_ERROR_STOP=1 "$@"; }

if $check; then
  for db in leads n8n crm_demo; do
    scratch="restore_check_$db"
    psql_ -d postgres -qc "DROP DATABASE IF EXISTS $scratch" -c "CREATE DATABASE $scratch"
    docker compose exec -T postgres pg_restore -U postgres --no-owner --exit-on-error \
      -d "$scratch" < "$src/$db.dump"
  done
  psql_ -d restore_check_leads -Atc \
    "select 'leads', count(*) from leads union all select 'lead_events', count(*) from lead_events
     union all select 'audit_events', count(*) from audit_events
     union all select 'outbox_events', count(*) from outbox_events" > /tmp/restored-counts.txt
  n8n_workflows=$(psql_ -d restore_check_n8n -Atc "select count(*) from workflow_entity")
  crm_contacts=$(psql_ -d restore_check_crm_demo -Atc "select count(*) from contacts")
  for db in leads n8n crm_demo; do psql_ -d postgres -qc "DROP DATABASE restore_check_$db"; done
  if diff -u "$src/row-counts.txt" /tmp/restored-counts.txt; then
    echo "restore check passed: row counts match ($(paste -sd' ' /tmp/restored-counts.txt));" \
         "n8n workflows: $n8n_workflows; CRM contacts: $crm_contacts"
  else
    echo "restore check FAILED: row counts differ" >&2
    exit 1
  fi
  exit 0
fi

read -r -p "Replace the live databases with $src? Type 'restore' to continue: " answer
[ "$answer" = "restore" ] || { echo "cancelled"; exit 1; }
docker compose stop web api dispatcher n8n n8n-runners crm-demo
for db in leads n8n crm_demo; do
  docker compose exec -T postgres pg_restore -U postgres --clean --if-exists --exit-on-error \
    -d "$db" < "$src/$db.dump"
done
docker compose run --rm migrate
docker compose up -d --wait
echo "restored from $src"
