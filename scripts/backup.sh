#!/usr/bin/env bash
# Back up the three databases (application, n8n, demo CRM) as PostgreSQL custom-format dumps.
#
#   scripts/backup.sh                 # -> backups/<UTC timestamp>/{leads,n8n,crm_demo}.dump
#   scripts/backup.sh /secure/path    # another target directory
#
# The dumps contain personal data, and the n8n dump contains credentials encrypted with
# secrets/n8n_encryption_key: store backups encrypted and access controlled, and keep a copy of
# that key separately (without it, n8n's stored credentials cannot be decrypted after a restore;
# the setup service can recreate them from the other secrets). Runs online; no downtime.
set -euo pipefail
cd "$(dirname "$0")/.."
target=${1:-backups}/$(date -u +%Y%m%dT%H%M%SZ)
umask 077
mkdir -p "$target"
for db in leads n8n crm_demo; do
  docker compose exec -T postgres pg_dump -U postgres -Fc --no-password "$db" > "$target/$db.dump"
done
docker compose exec -T postgres psql -U postgres -d leads -Atc \
  "select 'leads', count(*) from leads union all select 'lead_events', count(*) from lead_events
   union all select 'audit_events', count(*) from audit_events
   union all select 'outbox_events', count(*) from outbox_events" > "$target/row-counts.txt"
(cd "$target" && sha256sum ./*.dump > SHA256SUMS)
echo "backup written to $target"
ls -l "$target"
