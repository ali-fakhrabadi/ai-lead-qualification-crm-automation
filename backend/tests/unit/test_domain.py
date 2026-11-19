"""Unit tests: normalization, webhook signatures, lifecycle, tokens, logging, settings."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid

import jwt
import pytest

from app.core.config import Settings
from app.core.errors import Conflict, NotFound, Unauthorized
from app.core.logging import JsonFormatter, redact
from app.core.tokens import dispatch_token
from app.db.models import Lead, LeadStatus, Stage
from app.services import lifecycle, webhooks
from app.services.normalize import (
    InvalidField,
    clean_text,
    company_domain,
    email_base,
    name_key,
    normalize_email,
    normalize_name,
    normalize_website,
    payload_hash,
)


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "test",
        "jwt_secret": "j" * 40,
        "internal_token": "i" * 40,
        "dispatch_secret": "d" * 40,
        "webhook_secrets": {"partner-demo": "s3cret-for-tests"},
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- normalization ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  Maya.Chen@Harborview.EXAMPLE ", "maya.chen@harborview.example"),
        ("first+sales@example.com", "first+sales@example.com"),
        ("j.doe@gmail.com", "j.doe@gmail.com"),
    ],
)
def test_email_is_trimmed_and_lowercased_but_not_folded(raw: str, expected: str) -> None:
    assert normalize_email(raw) == expected


@pytest.mark.parametrize("raw", ["not-an-email", "a@b", "x@[127.0.0.1]", "user@192.168.1.10", ""])
def test_invalid_emails_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidField) as exc:
        normalize_email(raw)
    assert exc.value.field == "email"


def test_plus_tags_are_only_a_duplicate_signal() -> None:
    assert email_base("anna+crm@acme.example") == "anna@acme.example"
    assert email_base("anna@acme.example") == "anna@acme.example"


@pytest.mark.parametrize(
    ("raw", "host"),
    [
        ("https://www.Harborview-Logistics.example/contact?x=1", "harborview-logistics.example"),
        ("harborview.example", "harborview.example"),
        ("", None),
        (None, None),
    ],
)
def test_website_is_reduced_to_a_host_name(raw: str | None, host: str | None) -> None:
    assert normalize_website(raw) == host


@pytest.mark.parametrize(
    "raw", ["ftp://example.com", "https://user:pw@example.com", "http://localhost", "javascript:alert(1)"]
)
def test_unsafe_websites_are_rejected(raw: str) -> None:
    with pytest.raises(InvalidField):
        normalize_website(raw)


def test_company_domain_ignores_free_mail_providers() -> None:
    assert company_domain("maya@harborview.example", None) == "harborview.example"
    assert company_domain("maya@gmail.com", None) is None
    assert company_domain("maya@gmail.com", "harborview.example") == "harborview.example"


def test_names_reject_markup_and_links_and_keep_spelling() -> None:
    assert normalize_name("  Zoë   Müller ", "first_name") == "Zoë Müller"
    for bad in ("<script>", "http://spam.example", "a@b.c", "   "):
        with pytest.raises(InvalidField):
            normalize_name(bad, "first_name")


def test_name_key_folds_case_and_accents() -> None:
    assert name_key("Zoë", "Müller") == name_key("ZOE", "muller")


def test_text_cleanup_removes_control_characters_and_keeps_paragraphs() -> None:
    text = "Hello\u0000 world\u200b!\r\n\r\n\r\n\r\nSecond   paragraph\u2028"
    assert clean_text(text, multiline=True) == "Hello world!\n\nSecond paragraph"
    assert clean_text("a\nb") == "a b"


def test_payload_hash_is_order_independent() -> None:
    assert payload_hash({"a": 1, "b": "x"}) == payload_hash({"b": "x", "a": 1})
    assert payload_hash({"a": 1}) != payload_hash({"a": 2})


# --- webhook signatures ------------------------------------------------------------------------
BODY = b'{"event_id":"evt_000001","lead":{}}'


def test_valid_signature_is_accepted() -> None:
    s = settings()
    signature = webhooks.sign("s3cret-for-tests", 1_763_560_000, BODY)
    webhooks.verify(s, "partner-demo", "1763560000", signature, BODY, now=1_763_560_100)


def test_tampered_body_wrong_secret_and_unknown_source_are_refused() -> None:
    s = settings()
    signature = webhooks.sign("s3cret-for-tests", 1_763_560_000, BODY)
    with pytest.raises(Unauthorized):
        webhooks.verify(s, "partner-demo", "1763560000", signature, BODY + b" ", now=1_763_560_000)
    with pytest.raises(Unauthorized):
        webhooks.verify(s, "partner-demo", "1763560000",
                        webhooks.sign("other", 1_763_560_000, BODY), BODY, now=1_763_560_000)
    with pytest.raises(NotFound):
        webhooks.verify(s, "unknown", "1763560000", signature, BODY, now=1_763_560_000)
    with pytest.raises(Unauthorized):
        webhooks.verify(s, "partner-demo", None, signature, BODY, now=1_763_560_000)


@pytest.mark.parametrize("age", [301, -301, 3600])
def test_old_or_future_timestamps_are_refused(age: int) -> None:
    s = settings()
    signature = webhooks.sign("s3cret-for-tests", 1_763_560_000, BODY)
    with pytest.raises(Unauthorized) as exc:
        webhooks.verify(s, "partner-demo", "1763560000", signature, BODY,
                        now=1_763_560_000 + age)
    assert exc.value.code == "expired_signature"


def test_signature_header_may_carry_several_candidates_during_rotation() -> None:
    s = settings()
    good = webhooks.sign("s3cret-for-tests", 1_763_560_000, BODY)
    webhooks.verify(s, "partner-demo", "1763560000", f"v1=deadbeef, {good}", BODY,
                    now=1_763_560_000)


# --- lifecycle ---------------------------------------------------------------------------------
def make_lead(status: LeadStatus) -> Lead:
    return Lead(status=status, review_reasons=[])


def test_happy_path_transitions_are_allowed() -> None:
    lead = make_lead(LeadStatus.NEW)
    for target in (
        LeadStatus.PENDING_QUALIFICATION, LeadStatus.QUALIFIED, LeadStatus.SYNCED_TO_CRM,
        LeadStatus.FOLLOW_UP_DRAFT_READY, LeadStatus.FOLLOW_UP_APPROVED, LeadStatus.FOLLOW_UP_SENT,
    ):
        lifecycle.transition(lead, target)
    assert lead.status is LeadStatus.FOLLOW_UP_SENT


def test_invalid_transitions_raise_conflict() -> None:
    lead = make_lead(LeadStatus.NEW)
    with pytest.raises(Conflict):
        lifecycle.transition(lead, LeadStatus.FOLLOW_UP_SENT)
    assert lead.status is LeadStatus.NEW


def test_unqualified_and_closed_need_a_person() -> None:
    lead = make_lead(LeadStatus.NEEDS_REVIEW)
    with pytest.raises(Conflict):
        lifecycle.transition(lead, LeadStatus.UNQUALIFIED)
    lifecycle.transition(lead, LeadStatus.UNQUALIFIED, by_operator=True)
    assert lead.status is LeadStatus.UNQUALIFIED


def test_retry_stage_is_kept_only_while_waiting() -> None:
    lead = make_lead(LeadStatus.QUALIFIED)
    lifecycle.transition(lead, LeadStatus.RETRY_PENDING, retry_stage=Stage.CRM_SYNC)
    assert lead.retry_stage is Stage.CRM_SYNC
    lifecycle.transition(lead, LeadStatus.SYNCED_TO_CRM)
    assert lead.retry_stage is None


def test_every_status_has_a_transition_entry() -> None:
    assert set(lifecycle.TRANSITIONS) == set(LeadStatus)


# --- tokens, logging, settings -------------------------------------------------------------------
def test_dispatch_token_binds_event_and_payload_hash() -> None:
    s = settings()
    event_id = uuid.uuid4()
    payload = b'{"event_id": "x"}'
    claims = jwt.decode(dispatch_token(s, event_id, payload), "d" * 40, algorithms=["HS256"],
                        audience="n8n")
    assert claims["eid"] == str(event_id)
    assert claims["payload_sha256"] == hashlib.sha256(payload).hexdigest()
    assert claims["iss"] == "lead-automation-dispatcher"
    assert claims["exp"] - claims["iat"] <= 300


def test_logs_redact_tokens_and_mask_emails() -> None:
    text = redact("Bearer abc.def.ghi password=hunter2 maya.chen@harborview.example")
    assert "hunter2" not in text
    assert "abc.def.ghi" not in text
    assert "maya.chen@" not in text
    assert "m***@harborview.example" in text
    record = logging.makeLogRecord({"msg": "lead from j.doe@example.com", "levelname": "INFO"})
    assert "j***@example.com" in json.loads(JsonFormatter().format(record))["msg"]


def test_production_requires_strong_secrets_and_forbids_fault_injection() -> None:
    with pytest.raises(ValueError, match="LEAD_JWT_SECRET"):
        Settings(environment="production", jwt_secret="short", internal_token="i" * 40,
                 dispatch_secret="d" * 40)
    with pytest.raises(ValueError, match="FAULT_INJECTION"):
        Settings(environment="production", jwt_secret="j" * 40, internal_token="i" * 40,
                 dispatch_secret="d" * 40, fault_injection_enabled=True)


def test_real_email_delivery_needs_a_second_explicit_switch() -> None:
    with pytest.raises(ValueError, match="ALLOW_REAL_RECIPIENTS"):
        settings(delivery_mode="smtp")
    assert settings(delivery_mode="smtp", delivery_allow_real_recipients=True).delivery_mode == "smtp"


def test_openai_provider_needs_a_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        settings(llm_provider="openai")


def test_secrets_are_read_from_files(tmp_path: object, monkeypatch: pytest.MonkeyPatch) -> None:
    from pathlib import Path

    folder = Path(str(tmp_path))
    (folder / "jwt").write_text("f" * 40 + "\n")
    (folder / "hooks").write_text('{"crm-form": "abc"}')
    # An explicit environment value wins over a file; remove the test defaults first.
    monkeypatch.delenv("LEAD_JWT_SECRET", raising=False)
    monkeypatch.delenv("LEAD_WEBHOOK_SECRETS", raising=False)
    monkeypatch.setenv("LEAD_JWT_SECRET_FILE", str(folder / "jwt"))
    monkeypatch.setenv("LEAD_WEBHOOK_SECRETS_FILE", str(folder / "hooks"))
    s = Settings(environment="test")
    assert s.jwt_secret.get_secret_value() == "f" * 40
    assert s.webhook_secrets == {"crm-form": "abc"}
