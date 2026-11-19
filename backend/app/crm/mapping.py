"""Configurable mapping from a lead and its assessment to CRM contact properties.

HubSpot accounts differ: standard properties exist everywhere, custom ones (``lq_*``) must be
created first (docs/crm-integration.md). Set ``LEAD_CRM_PROPERTY_MAP_FILE`` to a JSON file with
the same shape as ``DEFAULT_MAP`` to change names or drop custom properties.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.db.models import Assessment, Lead

DEFAULT_MAP: dict[str, Any] = {
    # CRM property -> lead attribute
    "standard": {
        "email": "email_normalized",
        "firstname": "first_name",
        "lastname": "last_name",
        "company": "company",
        "jobtitle": "job_title",
        "website": "website",
        "country": "country",
    },
    # CRM property -> value computed from the lead/assessment (custom properties)
    "custom": {
        "lq_reference": "reference",
        "lq_decision": "decision",
        "lq_score": "score",
        "lq_intent": "intent_category",
        "lq_interest": "interest",
    },
    "constants": {"lifecyclestage": "lead"},
}


@lru_cache
def property_map() -> dict[str, Any]:
    path = get_settings().crm_property_map_file
    if path:
        return json.loads(Path(path).read_text(encoding="utf-8"))  # type: ignore[no-any-return]
    return DEFAULT_MAP


def contact_properties(lead: Lead) -> dict[str, str]:
    mapping = property_map()
    props: dict[str, str] = {}
    for crm_name, attr in mapping.get("standard", {}).items():
        value = getattr(lead, attr, None)
        if value:
            props[crm_name] = str(value)
    computed = {
        "reference": f"L-{lead.reference}",
        "decision": lead.decision.value if lead.decision else None,
        "score": lead.score,
        "intent_category": lead.intent_category,
        "interest": lead.interest,
    }
    for crm_name, key in mapping.get("custom", {}).items():
        value = computed.get(key)
        if value is not None:
            props[crm_name] = str(value)
    props.update({k: str(v) for k, v in mapping.get("constants", {}).items()})
    return props


def note_body(lead: Lead, assessment: Assessment | None) -> str:
    """Plain-text qualification note; no raw lead message, no model output verbatim."""
    lines = [f"Lead L-{lead.reference} qualification (automated, advisory)."]
    if assessment is not None and assessment.rules:
        rules = assessment.rules
        parsed = assessment.parsed or {}
        lines += [
            f"Decision: {rules.get('decision')} (score {rules.get('score')}/100)",
            f"Intent: {parsed.get('intent_category', 'n/a')}; fit: "
            f"{parsed.get('service_fit', 'n/a')}; urgency: {parsed.get('urgency', 'n/a')}",
            f"Summary: {str(parsed.get('reasoning_summary', ''))[:300]}",
        ]
        if rules.get("reasons"):
            lines.append("Review reasons: " + ", ".join(rules["reasons"]))
        lines.append(f"Model: {assessment.provider}/{assessment.model}")
    return "\n".join(lines)
