"""Application settings.

Every value comes from an environment variable with the ``LEAD_`` prefix. Secrets can also be
read from a file: ``LEAD_JWT_SECRET_FILE=/run/secrets/jwt_secret`` sets ``LEAD_JWT_SECRET``.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_FIELDS = (
    "jwt_secret",
    "db_password",
    "internal_token",
    "dispatch_secret",
    "openai_api_key",
    "crm_token",
    "fault_admin_token",
)


def _read_secret_files(values: dict[str, Any]) -> dict[str, Any]:
    for name in _SECRET_FIELDS:
        path = os.environ.get(f"LEAD_{name.upper()}_FILE")
        if path and not values.get(name):
            values[name] = Path(path).read_text(encoding="utf-8").strip()
    path = os.environ.get("LEAD_WEBHOOK_SECRETS_FILE")
    if path and not values.get("webhook_secrets"):
        text = Path(path).read_text(encoding="utf-8").strip()
        values["webhook_secrets"] = json.loads(text) if text else {}
    return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="LEAD_", extra="ignore")

    environment: Literal["development", "test", "production"] = "production"
    log_level: str = "INFO"
    enable_docs: bool = True

    # Database: the application connects as a non-owner role.
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "leads"
    db_user: str = "lead_app"
    db_password: SecretStr = SecretStr("")
    db_pool_size: int = 5

    # Operator authentication
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "lead-automation"
    jwt_audience: str = "lead-api"
    access_token_minutes: int = Field(15, ge=1, le=120)
    refresh_token_days: int = Field(7, ge=1, le=90)
    trust_proxy_headers: bool = False
    cors_origins: list[str] = []

    # Service-to-service: n8n -> FastAPI internal endpoints (bearer token) and
    # dispatcher -> n8n webhooks (HS256 JWT carrying a hash of the body).
    internal_token: SecretStr = SecretStr("")
    dispatch_secret: SecretStr = SecretStr("")
    n8n_base_url: str = "http://n8n:5678"
    n8n_lead_webhook_path: str = "lead-events"
    n8n_follow_up_webhook_path: str = "follow-up-events"
    dispatch_batch_size: int = Field(10, ge=1, le=100)
    dispatch_poll_s: float = Field(1.0, ge=0.1, le=60)
    dispatch_timeout_s: float = Field(30.0, ge=1, le=600)
    dispatch_max_attempts: int = Field(8, ge=1, le=50)
    dispatch_backoff_base_s: float = Field(2.0, ge=0.1, le=60)
    dispatch_backoff_cap_s: float = Field(300.0, ge=1, le=3600)

    # Inbound partner webhooks: {"source-name": "shared secret"} from a secret file.
    webhook_secrets: dict[str, str] = {}
    webhook_tolerance_s: int = Field(300, ge=30, le=3600)

    # Intake: optional fields that this deployment requires (company, website, job_title,
    # country). Name, email, interest, message and contact consent are always required.
    required_optional_fields: list[str] = ["company"]

    # Limits
    max_body_bytes: int = Field(32 * 1024, ge=1024, le=1024 * 1024)
    rate_limit_public_per_minute: int = 10
    rate_limit_webhook_per_minute: int = 300
    rate_limit_login_per_minute: int = 10
    rate_limit_requalify_per_hour: int = 30

    # LLM
    llm_provider: Literal["ollama", "openai", "fixture"] = "ollama"
    llm_model: str = "llama3.1:8b"
    llm_timeout_s: float = 300.0
    llm_num_ctx: int = 4096
    llm_num_thread: int | None = None
    llm_temperature: float = 0.0
    llm_seed: int = 7
    llm_keep_alive: str = "30m"
    llm_draft_followups: bool = True
    ollama_base_url: str = "http://ollama:11434"
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr | None = None

    # Qualification rubric (business context and thresholds); a bundled default is used when
    # the file is not set.
    rubric_file: str | None = None

    # CRM
    crm_provider: Literal["demo", "hubspot", "none"] = "demo"
    crm_base_url: str = "http://demo-crm:8100"
    crm_token: SecretStr = SecretStr("")
    crm_timeout_s: float = Field(10.0, ge=1, le=120)
    crm_max_attempts: int = Field(3, ge=1, le=10)
    crm_backoff_base_s: float = Field(0.5, ge=0.0, le=30)
    crm_backoff_cap_s: float = Field(4.0, ge=0.0, le=120)
    crm_property_map_file: str | None = None

    # Follow-up delivery. Only the local sink is allowed unless explicitly switched on.
    delivery_mode: Literal["sink", "smtp"] = "sink"
    delivery_allow_real_recipients: bool = False
    smtp_host: str = "mailpit"
    smtp_port: int = 1025
    mail_from: str = "Northwind Automation Studio <sales@northwind-automation.example>"

    # Evaluation and test only: lets the evaluation harness inject provider failures for
    # specific scenarios. Never enable in production.
    fault_injection_enabled: bool = False
    fault_admin_token: SecretStr = SecretStr("")

    # Data retention: closed leads older than this many days can be purged (CLI).
    retention_days: int = Field(365, ge=1, le=3650)

    @model_validator(mode="before")
    @classmethod
    def _load_secret_files(cls, data: Any) -> Any:
        return _read_secret_files(dict(data)) if isinstance(data, dict) else data

    @field_validator("llm_num_thread", mode="before")
    @classmethod
    def _empty_means_unset(cls, value: Any) -> Any:
        # Compose passes "${LEAD_LLM_NUM_THREAD:-}" through as an empty string when unset.
        return None if isinstance(value, str) and not value.strip() else value

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.environment == "production":
            for name in ("jwt_secret", "internal_token", "dispatch_secret"):
                if len(getattr(self, name).get_secret_value()) < 32:
                    raise ValueError(f"LEAD_{name.upper()} must be at least 32 characters")
            if self.fault_injection_enabled:
                raise ValueError("LEAD_FAULT_INJECTION_ENABLED must not be set in production")
        if self.llm_provider == "openai" and not self.openai_api_key:
            raise ValueError("LEAD_OPENAI_API_KEY is required when the OpenAI provider is used")
        if self.delivery_mode == "smtp" and not self.delivery_allow_real_recipients:
            raise ValueError(
                "LEAD_DELIVERY_MODE=smtp sends real email; it also needs "
                "LEAD_DELIVERY_ALLOW_REAL_RECIPIENTS=true (see docs/security.md)"
            )
        return self

    @property
    def database_url(self) -> str:
        from sqlalchemy.engine import URL

        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)


@lru_cache
def get_settings() -> Settings:
    return Settings()
