"""Test settings, applied before the application reads its configuration.

Unit tests need no services. Integration tests (tests/integration) use a real PostgreSQL; its
connection comes from LEAD_DB_* variables set by the test runner (see docs/testing.md).
"""

from __future__ import annotations

import os

TEST_ENV = {
    "LEAD_ENVIRONMENT": "test",
    "LEAD_JWT_SECRET": "test-jwt-secret-" + "x" * 32,
    "LEAD_INTERNAL_TOKEN": "test-internal-token-" + "y" * 32,
    "LEAD_DISPATCH_SECRET": "test-dispatch-secret-" + "z" * 32,
    "LEAD_WEBHOOK_SECRETS": '{"partner-demo": "partner-secret-for-tests"}',
    "LEAD_LLM_PROVIDER": "fixture",
    "LEAD_FAULT_INJECTION_ENABLED": "true",
    "LEAD_FAULT_ADMIN_TOKEN": "fault-admin-token-for-tests",
    "LEAD_CRM_TOKEN": "crm-token-for-tests",
    "LEAD_RATE_LIMIT_PUBLIC_PER_MINUTE": "1000",
    "LEAD_RATE_LIMIT_LOGIN_PER_MINUTE": "1000",
}
for key, value in TEST_ENV.items():
    os.environ.setdefault(key, value)
