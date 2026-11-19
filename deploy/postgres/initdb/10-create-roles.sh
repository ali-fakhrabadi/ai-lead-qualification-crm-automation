#!/usr/bin/env bash
# Runs once, when the PostgreSQL data volume is first initialised.
#
# Least-privilege roles on top of the bootstrap superuser:
#   lead_owner  owns the "leads" database and schema; runs migrations (DDL)
#   lead_app    API, dispatcher; reads and writes data, cannot change the schema
#   n8n         owns a separate "n8n" database; cannot connect to "leads"
#   crm_demo    owns the demo CRM's own database ("crm_demo"); cannot connect to "leads"
#
# Passwords come from mounted secret files and reach psql through its environment
# (\getenv), never through a command line where `ps` could show them.
set -euo pipefail

read_secret() {
    local file="/run/secrets/$1"
    if [[ ! -s "$file" ]]; then
        echo "missing or empty secret: $file" >&2
        exit 1
    fi
    cat "$file"
}

export LEAD_OWNER_PASSWORD="$(read_secret db_owner_password)"
export LEAD_APP_PASSWORD="$(read_secret db_app_password)"
export N8N_DB_PASSWORD="$(read_secret n8n_db_password)"
export CRM_DB_PASSWORD="$(read_secret crm_db_password)"

psql --no-psqlrc -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv owner_password LEAD_OWNER_PASSWORD
\getenv app_password LEAD_APP_PASSWORD
\getenv n8n_password N8N_DB_PASSWORD
\getenv crm_password CRM_DB_PASSWORD
\getenv db POSTGRES_DB

CREATE ROLE lead_owner WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'owner_password';
CREATE ROLE lead_app WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'app_password';

ALTER DATABASE :"db" OWNER TO lead_owner;
REVOKE ALL ON DATABASE :"db" FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE :"db" TO lead_app;

\connect :"db"
ALTER SCHEMA public OWNER TO lead_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lead_app;
-- Tables and sequences the owner creates later (migrations) are usable by the app role.
ALTER DEFAULT PRIVILEGES FOR ROLE lead_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lead_app;
ALTER DEFAULT PRIVILEGES FOR ROLE lead_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO lead_app;

-- Throwaway database for the integration tests, owned by the schema owner.
CREATE DATABASE leads_test OWNER lead_owner;
REVOKE ALL ON DATABASE leads_test FROM PUBLIC;
GRANT CONNECT, TEMPORARY ON DATABASE leads_test TO lead_app;
\connect leads_test
ALTER SCHEMA public OWNER TO lead_owner;
REVOKE ALL ON SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO lead_app;
ALTER DEFAULT PRIVILEGES FOR ROLE lead_owner IN SCHEMA public
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO lead_app;
ALTER DEFAULT PRIVILEGES FOR ROLE lead_owner IN SCHEMA public
    GRANT USAGE, SELECT ON SEQUENCES TO lead_app;

-- n8n keeps workflows, credentials and executions in a database of its own.
CREATE ROLE n8n WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'n8n_password';
CREATE DATABASE n8n OWNER n8n;
REVOKE ALL ON DATABASE n8n FROM PUBLIC;

-- The demo CRM is a separate system with its own database and user.
CREATE ROLE crm_demo WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD :'crm_password';
CREATE DATABASE crm_demo OWNER crm_demo;
REVOKE ALL ON DATABASE crm_demo FROM PUBLIC;
SQL
