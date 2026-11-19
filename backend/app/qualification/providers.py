"""LLM providers: Ollama (default), OpenAI (optional) and a deterministic fixture.

The fixture is keyword rules, not a model: it exists so infrastructure, workflow and connector
tests run without a model. Its results are labelled ``provider=fixture`` everywhere and are
never reported as model performance.
"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

from app.core.config import Settings


@dataclass(frozen=True)
class LLMResult:
    content: str
    model: str
    prompt_tokens: int | None
    completion_tokens: int | None
    duration_ms: int


class ProviderUnavailable(Exception):
    """Transient: timeout, connection error, 5xx, rate limit or model not loaded yet."""


class ProviderError(Exception):
    """Permanent for this request: authentication, bad request, unknown model."""


class LLMProvider(Protocol):
    name: str
    model: str

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResult: ...


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResult:
        s = self.settings
        options: dict[str, Any] = {
            "temperature": s.llm_temperature,
            "seed": s.llm_seed,
            "num_ctx": s.llm_num_ctx,
        }
        if s.llm_num_thread:
            options["num_thread"] = s.llm_num_thread
        body = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "format": schema,
            "options": options,
            "keep_alive": s.llm_keep_alive,
        }
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{s.ollama_base_url.rstrip('/')}/api/chat", json=body, timeout=s.llm_timeout_s
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"ollama: {type(exc).__name__}") from exc
        if response.status_code >= 500 or response.status_code == 429:
            raise ProviderUnavailable(f"ollama: HTTP {response.status_code}")
        if response.status_code == 404:
            raise ProviderError(f"ollama: model {self.model} not found")
        if response.status_code >= 400:
            raise ProviderError(f"ollama: HTTP {response.status_code}")
        data = response.json()
        return LLMResult(
            content=data.get("message", {}).get("content", ""),
            model=data.get("model", self.model),
            prompt_tokens=data.get("prompt_eval_count"),
            completion_tokens=data.get("eval_count"),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


class OpenAIProvider:
    name = "openai"

    def __init__(self, settings: Settings):
        self.settings = settings
        self.model = settings.llm_model

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResult:
        s = self.settings
        key = s.openai_api_key.get_secret_value() if s.openai_api_key else ""
        body = {
            "model": self.model,
            "messages": messages,
            "temperature": s.llm_temperature,
            "seed": s.llm_seed,
            "response_format": {
                "type": "json_schema",
                "json_schema": {"name": "result", "schema": schema, "strict": True},
            },
        }
        started = time.perf_counter()
        try:
            response = httpx.post(
                f"{s.openai_base_url.rstrip('/')}/chat/completions",
                json=body,
                headers={"Authorization": f"Bearer {key}"},
                timeout=s.llm_timeout_s,
            )
        except httpx.HTTPError as exc:
            raise ProviderUnavailable(f"openai: {type(exc).__name__}") from exc
        if response.status_code in (429, 500, 502, 503, 504):
            raise ProviderUnavailable(f"openai: HTTP {response.status_code}")
        if response.status_code >= 400:
            raise ProviderError(f"openai: HTTP {response.status_code}")
        data = response.json()
        usage = data.get("usage") or {}
        return LLMResult(
            content=data["choices"][0]["message"].get("content") or "",
            model=data.get("model", self.model),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )


_KEYWORDS: list[tuple[str, re.Pattern[str]]] = [
    (
        "workflow_automation",
        re.compile(r"(?i)\b(automat\w*|workflow|n8n|zapier|manual (work|steps))"),
    ),
    ("crm_integration", re.compile(r"(?i)\b(crm|hubspot|salesforce|pipedrive|contacts? sync)")),
    ("ai_assistant", re.compile(r"(?i)\b(chatbot|ai assistant|llm|gpt|answer questions)")),
    (
        "data_migration",
        re.compile(r"(?i)\b(migrat\w*|import (our|the) data|move (our|the) records)"),
    ),
]


class FixtureProvider:
    """Deterministic keyword rules that emit schema-valid JSON. Not a model."""

    name = "fixture"
    model = "fixture-rules-v1"

    def chat_json(self, messages: list[dict[str, str]], schema: dict[str, Any]) -> LLMResult:
        user = messages[-1]["content"] if messages else ""
        if "subject" in schema.get("properties", {}):
            content = json.dumps(_fixture_draft(user))
        else:
            content = json.dumps(_fixture_qualification(user))
        return LLMResult(content, self.model, None, None, 0)


def _field(user: str, name: str) -> str:
    match = re.search(rf"<<<{name}>>>\n(.*?)\n<<<END {name}>>>", user, re.S)
    return match.group(1) if match else ""


def _fixture_qualification(user: str) -> dict[str, Any]:
    message = _field(user, "MESSAGE")
    text = message.lower()
    services = [key for key, pattern in _KEYWORDS if pattern.search(message)]
    if re.search(r"\b(resume|cv|job|position|hiring me)\b", text):
        intent = "job_application"
    elif re.search(r"\b(we offer|our agency|seo services|lead lists?)\b", text):
        intent = "vendor_pitch"
    elif re.search(r"\b(not working|bug|broken|support ticket|existing account)\b", text):
        intent = "support_request"
    elif re.search(r"\b(test|asdf|lorem ipsum)\b", text) and len(text) < 80:
        intent = "spam_or_test"
    elif re.search(r"\b(partner|reseller|white.?label)\b", text):
        intent = "partnership"
    elif re.search(r"\b(price|pricing|quote|cost|budget)\b", text):
        intent = "pricing_request"
    else:
        intent = "new_project" if services else "other"
    if re.search(r"\b(urgent|asap|this week|next week|by (the )?end of (the )?month)\b", text):
        urgency = "high"
    elif re.search(r"\b(this quarter|next month|within \w+ (weeks|months)|q[1-4])\b", text):
        urgency = "medium"
    elif re.search(r"\b(no rush|someday|eventually|next year)\b", text):
        urgency = "low"
    else:
        urgency = "unknown"
    fit = (
        "strong"
        if len(services) >= 1 and intent in ("new_project", "pricing_request")
        else ("partial" if services else ("none" if intent != "other" else "unclear"))
    )
    missing = []
    if urgency == "unknown":
        missing.append("timeline")
    if not re.search(r"\b(budget|\$|eur|usd)\b", text):
        missing.append("budget")
    sentence = re.split(r"(?<=[.!?])\s", message.strip())[0][:150] if message.strip() else ""
    return {
        "intent_category": intent,
        "service_fit": fit,
        "requested_services": services[:3],
        "urgency": urgency,
        "evidence": [sentence] if sentence else [],
        "reasoning_summary": f"Fixture rules: intent {intent}, fit {fit}, urgency {urgency}.",
        "recommended_next_action": "book_discovery_call" if fit == "strong" else "ask_for_details",
        "missing_information": missing,
        "needs_human_review": False,
    }


def _fixture_draft(user: str) -> dict[str, str]:
    first = _field(user, "FIRST_NAME") or "there"
    return {
        "subject": "Thanks for your inquiry",
        "body": (
            f"Hi {first},\n\nThank you for reaching out. We read your message and would like "
            "to learn a little more about your process before suggesting next steps. Would a "
            "short call next week work for you?\n\nThe Northwind Automation Studio team"
        ),
    }


def get_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "fixture":
        return FixtureProvider()
    if settings.llm_provider == "openai":
        return OpenAIProvider(settings)
    return OllamaProvider(settings)
