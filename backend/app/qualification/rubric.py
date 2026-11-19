"""The qualification rubric: business context for the model and weights for the rules.

The bundled default describes the fictional demo business; set ``LEAD_RUBRIC_FILE`` to a JSON
file with the same shape to use your own. Only business-relevant information is scored; the
rubric never refers to personal characteristics.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import get_settings


class Service(BaseModel):
    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,39}$")
    label: str
    description: str


class Weights(BaseModel):
    fit: dict[str, int] = {"strong": 50, "partial": 30, "unclear": 10, "none": 0}
    urgency: dict[str, int] = {"high": 25, "medium": 15, "low": 5, "unknown": 0}
    completeness_max: int = 25
    per_missing_item: int = 5


class Rubric(BaseModel):
    version: str = "2026-11-19"
    business_name: str
    business_summary: str
    services: list[Service]
    ideal_customer: str
    qualified_threshold: int = Field(60, ge=0, le=100)
    weights: Weights = Weights()
    # Intents that are never sales leads: routed to a person, never auto-qualified.
    review_intents: list[str] = ["support_request", "job_application", "vendor_pitch"]

    def service_keys(self) -> list[str]:
        return [s.key for s in self.services]

    def interest_options(self) -> list[dict[str, str]]:
        return [{"value": s.key, "label": s.label} for s in self.services] + [
            {"value": "other", "label": "Something else"}
        ]


DEFAULT_RUBRIC: dict[str, Any] = {
    "business_name": "Northwind Automation Studio",
    "business_summary": (
        "A small consultancy (fictional, for this demo) that builds workflow automations, CRM "
        "integrations and AI assistants for small and mid-sized B2B companies."
    ),
    "services": [
        {
            "key": "workflow_automation",
            "label": "Workflow automation",
            "description": "Automating business processes with n8n, APIs and scripts.",
        },
        {
            "key": "crm_integration",
            "label": "CRM integration",
            "description": "Connecting forms, apps and data sources to a CRM; data clean-up.",
        },
        {
            "key": "ai_assistant",
            "label": "AI assistant",
            "description": "Chatbots and assistants grounded in the client's own documents.",
        },
        {
            "key": "data_migration",
            "label": "Data migration",
            "description": "Moving records between systems with validation and deduplication.",
        },
    ],
    "ideal_customer": (
        "A business with a concrete process or integration problem it wants solved, ideally "
        "with a timeline; one-off tiny tasks and unrelated requests are a weak fit."
    ),
}


@lru_cache
def get_rubric() -> Rubric:
    path = get_settings().rubric_file
    if path:
        return Rubric.model_validate(json.loads(Path(path).read_text(encoding="utf-8")))
    return Rubric.model_validate(DEFAULT_RUBRIC)
