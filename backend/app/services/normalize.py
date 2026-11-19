"""Normalization of submitted lead data.

Conservative on purpose: email addresses are only trimmed and lower-cased (no Gmail dot or
plus-tag folding, which could merge different people); names keep their spelling. Websites are
parsed, never fetched.
"""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from typing import Any
from urllib.parse import urlsplit

from email_validator import EmailNotValidError, validate_email

# Personal mailbox providers: their domain says nothing about the lead's company.
FREE_MAIL_DOMAINS = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "outlook.com",
        "hotmail.com",
        "live.com",
        "yahoo.com",
        "icloud.com",
        "me.com",
        "aol.com",
        "proton.me",
        "protonmail.com",
        "gmx.com",
        "gmx.de",
        "mail.com",
        "yandex.com",
        "zoho.com",
        "fastmail.com",
    }
)
_CONTROL = re.compile(r"[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f\u200b-\u200f\u2028\u2029]")
_SPACES = re.compile(r"[ \t]+")
_DOMAIN = re.compile(r"^(?=.{1,253}$)(?!-)[a-z0-9-]{1,63}(?<!-)(\.(?!-)[a-z0-9-]{1,63}(?<!-))+$")
_NAME_BAD = re.compile(r"(https?://|www\.|[<>{}\[\]|\\@])", re.IGNORECASE)


class InvalidField(ValueError):
    def __init__(self, field: str, message: str):
        super().__init__(f"{field}: {message}")
        self.field = field
        self.message = message


def clean_text(value: str | None, *, multiline: bool = False) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFC", value)
    text = _CONTROL.sub("", text)
    if multiline:
        lines = [_SPACES.sub(" ", line).strip() for line in text.replace("\r\n", "\n").split("\n")]
        text = "\n".join(lines)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
    return _SPACES.sub(" ", text.replace("\n", " ")).strip()


def normalize_email(value: str) -> str:
    try:
        result = validate_email(value.strip(), check_deliverability=False)
    except EmailNotValidError as exc:
        raise InvalidField("email", "is not a valid email address") from exc
    domain = result.domain.lower()
    if domain.replace(".", "").isdigit() or domain.startswith("["):
        raise InvalidField("email", "must use a domain name")
    return f"{result.local_part.lower()}@{domain}"


def email_domain(normalized_email: str) -> str:
    return normalized_email.rsplit("@", 1)[1]


def email_base(normalized_email: str) -> str:
    """Local part without a ``+tag``: used only to *flag* possible duplicates, never to merge."""
    local, domain = normalized_email.rsplit("@", 1)
    return f"{local.split('+', 1)[0]}@{domain}"


def normalize_name(value: str, field: str) -> str:
    name = clean_text(value)
    if not name:
        raise InvalidField(field, "is required")
    if _NAME_BAD.search(name):
        raise InvalidField(field, "contains characters that are not allowed in a name")
    return name


def normalize_website(value: str | None) -> str | None:
    """Return the lower-cased host name of a website, or None. Nothing is ever fetched."""
    text = clean_text(value)
    if not text:
        return None
    candidate = text if "://" in text else f"https://{text}"
    try:
        parts = urlsplit(candidate)
    except ValueError as exc:
        raise InvalidField("website", "is not a valid website address") from exc
    if parts.scheme not in ("http", "https") or parts.username or parts.password:
        raise InvalidField("website", "must be an http(s) address without credentials")
    host = (parts.hostname or "").lower().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    if not _DOMAIN.match(host):
        raise InvalidField("website", "must be a public domain name")
    return host


def company_domain(normalized_email: str, website_host: str | None) -> str | None:
    if website_host:
        return website_host
    domain = email_domain(normalized_email)
    return None if domain in FREE_MAIL_DOMAINS else domain


def name_key(first: str, last: str) -> str:
    folded = unicodedata.normalize("NFKD", f"{first} {last}".casefold())
    return "".join(c for c in folded if not unicodedata.combining(c)).strip()


def payload_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
