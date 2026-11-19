#!/bin/sh
# Generate local secrets once. Files are git-ignored and mounted as Docker secrets.
set -eu
cd "$(dirname "$0")/.."
mkdir -p secrets
# The directory is private to the owner; the files are world-readable so the non-root users in
# the containers can read their bind-mounted copies. Nobody else on the host can reach them
# through the 0700 directory.
chmod 700 secrets
umask 022
random() {
    # 48 random bytes, base64url without padding: safe in URLs, SQL and headers.
    openssl rand -base64 48 | tr '+/' '-_' | tr -d '=\n'
}
for name in postgres_superuser_password db_owner_password db_app_password n8n_db_password \
    crm_db_password jwt_secret internal_token dispatch_secret n8n_encryption_key \
    n8n_owner_password n8n_runners_auth_token crm_token crm_admin_token fault_admin_token \
    demo_password; do
    if [ ! -s "secrets/$name" ]; then
        random > "secrets/$name"
        echo "created secrets/$name"
    fi
done
if [ ! -s secrets/webhook_secrets ]; then
    # One shared secret per partner source; the source name is the URL path segment.
    printf '{"partner-demo": "%s"}\n' "$(random)" > secrets/webhook_secrets
    echo "created secrets/webhook_secrets (source: partner-demo)"
fi
if [ ! -e secrets/openai_api_key ]; then
    : > secrets/openai_api_key
    echo "created empty secrets/openai_api_key (only needed with the OpenAI provider)"
fi
chmod 644 secrets/*
