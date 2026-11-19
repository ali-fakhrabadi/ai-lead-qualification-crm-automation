#!/bin/sh
# Create the demo operator accounts (admin, operator, viewer) with the password in
# secrets/demo_password. The password goes to the CLI through the environment, never argv.
set -eu
cd "$(dirname "$0")/.."
LEAD_CLI_PASSWORD="$(cat secrets/demo_password)"
export LEAD_CLI_PASSWORD
for spec in "admin@northwind-automation.example:admin:Ada Admin" \
            "olivia.operator@northwind-automation.example:operator:Olivia Operator" \
            "victor.viewer@northwind-automation.example:viewer:Victor Viewer"; do
    email=${spec%%:*}
    rest=${spec#*:}
    role=${rest%%:*}
    name=${rest#*:}
    docker compose exec -T -e LEAD_CLI_PASSWORD api \
        python -m app.cli create-user --email "$email" --role "$role" --full-name "$name" </dev/null \
        || echo "($email already exists?)"
done
