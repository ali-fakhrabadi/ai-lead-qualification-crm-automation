"""The structured output the model must return, validated strictly.

The same Pydantic model produces the JSON schema sent to the model (Ollama ``format`` /
OpenAI ``json_schema``) and validates the reply, so the two can never drift apart.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError


class Intent(StrEnum):
    NEW_PROJECT = "new_project"
    PRICING_REQUEST = "pricing_request"
    PARTNERSHIP = "partnership"
    SUPPORT_REQUEST = "support_request"
    JOB_APPLICATION = "job_application"
    VENDOR_PITCH = "vendor_pitch"
    SPAM_OR_TEST = "spam_or_test"
    OTHER = "other"


class Fit(StrEnum):
    STRONG = "strong"
    PARTIAL = "partial"
    NONE = "none"
    UNCLEAR = "unclear"


class Urgency(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class NextAction(StrEnum):
    BOOK_DISCOVERY_CALL = "book_discovery_call"
    SEND_INFORMATION = "send_information"
    ASK_FOR_DETAILS = "ask_for_details"
    ROUTE_TO_SUPPORT = "route_to_support"
    NO_ACTION = "no_action"


class Missing(StrEnum):
    USE_CASE = "use_case"
    TIMELINE = "timeline"
    BUDGET = "budget"
    COMPANY = "company"
    SYSTEMS = "systems"


class QualificationOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    intent_category: Intent
    service_fit: Fit
    requested_services: list[str] = Field(default_factory=list, max_length=4)
    urgency: Urgency
    evidence: list[str] = Field(default_factory=list, max_length=4)
    reasoning_summary: str = Field(max_length=400)
    recommended_next_action: NextAction
    missing_information: list[Missing] = Field(default_factory=list, max_length=5)
    needs_human_review: bool


class DraftOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    subject: str = Field(min_length=3, max_length=120)
    body: str = Field(min_length=40, max_length=1800)


def json_schema(model: type[BaseModel]) -> dict[str, Any]:
    """Inline, self-contained JSON schema (no $defs) for providers that need a flat schema."""
    schema = model.model_json_schema()
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                return resolve(defs[node["$ref"].rsplit("/", 1)[1]])
            return {k: resolve(v) for k, v in node.items() if k not in ("title", "default")}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    flat: dict[str, Any] = resolve(schema)
    flat["additionalProperties"] = False
    flat["required"] = list(model.model_fields)
    return flat


class OutputError(ValueError):
    """The model reply is not valid JSON for the schema."""


def parse_output[T: BaseModel](model: type[T], raw: str) -> T:
    text = raw.strip()
    if text.startswith("```"):
        text = text.strip("`")
        text = text[text.find("{") :] if "{" in text else text
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        errors = exc.errors()
        where = ".".join(str(p) for p in errors[0]["loc"]) if errors else ""
        message = errors[0]["msg"] if errors else "invalid"
        raise OutputError(f"{where or 'body'}: {message}"[:200]) from exc
