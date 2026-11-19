"""Deterministic business rules: the score and the decision are computed here, not by the model.

``unqualified`` is never produced here: only a person can reject a lead.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import asdict, dataclass, field
from typing import Any

from app.db.models import Decision
from app.qualification.rubric import Rubric
from app.qualification.schema import Fit, Intent, QualificationOutput


@dataclass
class RuleResult:
    score: int
    decision: Decision
    reasons: list[str] = field(default_factory=list)
    evidence_found: list[str] = field(default_factory=list)
    evidence_dropped: int = 0
    requested_services: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["decision"] = self.decision.value
        return data


def _fold(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    text = re.sub(r"[\u2018\u2019\u201c\u201d]", "'", text)
    return re.sub(r"\s+", " ", text).strip(" .\"'")


def ground_evidence(quotes: list[str], message: str) -> tuple[list[str], int]:
    """Keep only quotes that really occur in the message (the model cannot invent evidence)."""
    folded = _fold(message)
    kept = [q for q in quotes if len(_fold(q)) >= 3 and _fold(q) in folded]
    return kept[:3], len(quotes) - len(kept[:3])


def score(rubric: Rubric, output: QualificationOutput) -> int:
    w = rubric.weights
    completeness = max(0, w.completeness_max - w.per_missing_item * len(output.missing_information))
    value = w.fit.get(output.service_fit.value, 0) + w.urgency.get(output.urgency.value, 0)
    return max(0, min(100, value + completeness))


def apply_rules(
    rubric: Rubric, output: QualificationOutput, message: str, *, injection_detected: bool
) -> RuleResult:
    evidence, dropped = ground_evidence(output.evidence, message)
    services = [s for s in output.requested_services if s in rubric.service_keys()]
    result = RuleResult(
        score=score(rubric, output),
        decision=Decision.NEEDS_REVIEW,
        evidence_found=evidence,
        evidence_dropped=dropped,
        requested_services=services,
    )
    reasons = result.reasons
    if injection_detected:
        reasons.append("instruction_like_text")
    if output.needs_human_review:
        reasons.append("model_requested_review")
    if output.intent_category.value in rubric.review_intents:
        reasons.append(f"not_a_sales_inquiry:{output.intent_category.value}")
    if output.intent_category is Intent.SPAM_OR_TEST:
        reasons.append("possible_spam_or_test")
    if output.service_fit in (Fit.NONE, Fit.UNCLEAR):
        reasons.append(f"service_fit_{output.service_fit.value}")
    if output.evidence and not evidence:
        reasons.append("evidence_not_found_in_message")
    if result.score < rubric.qualified_threshold:
        reasons.append("score_below_threshold")
    if not reasons:
        result.decision = Decision.QUALIFIED
    return result


def invalid_output_result() -> RuleResult:
    return RuleResult(score=0, decision=Decision.NEEDS_REVIEW, reasons=["invalid_model_output"])
