"""Small stdlib-only clients for the evaluation harness (no third-party packages needed).

The harness runs in a container on the stack's internal network and talks to the same
endpoints a real form, a partner system and an operator would use. Test hooks (fault
injection) need the evaluation overlay and the fault-admin token; they are refused otherwise.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

BASE_URL = os.environ.get("EVAL_BASE_URL", "http://web:8080")
API_URL = os.environ.get("EVAL_API_URL", "http://api:8000")
CRM_URL = os.environ.get("EVAL_CRM_URL", "http://crm-demo:8100")
MAILPIT_URL = os.environ.get("EVAL_MAILPIT_URL", "http://mailpit:8025")
OPERATOR = os.environ.get("EVAL_OPERATOR", "olivia.operator@northwind-automation.example")


def secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


class Response:
    def __init__(self, status: int, body: Any, ms: float, headers: dict[str, str]) -> None:
        self.status, self.body, self.ms, self.headers = status, body, ms, headers


def request(
    method: str,
    url: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
    timeout: float = 60,
) -> Response:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(  # noqa: S310 - URLs come from the harness configuration
        url,
        data=data,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            text = resp.read().decode()
            status, hdrs = resp.status, dict(resp.headers.items())
    except urllib.error.HTTPError as exc:
        text, status, hdrs = exc.read().decode(), exc.code, dict(exc.headers.items())
    ms = (time.perf_counter() - started) * 1000
    try:
        parsed = json.loads(text) if text.strip() else None
    except json.JSONDecodeError:
        parsed = text
    return Response(status, parsed, ms, hdrs)


class Operator:
    """The operator API, re-authenticating when the short-lived access token expires."""

    def __init__(self, email: str = OPERATOR) -> None:
        self.email = email
        self.headers: dict[str, str] = {}
        self.login()

    def login(self) -> None:
        for _ in range(10):
            r = request(
                "POST",
                f"{BASE_URL}/api/v1/auth/login",
                {"email": self.email, "password": secret("demo_password")},
            )
            if r.status == 200:
                self.headers = {"Authorization": f"Bearer {r.body['access_token']}"}
                return
            if r.status != 429:
                raise RuntimeError(f"operator login failed with HTTP {r.status}")
            time.sleep(int(r.headers.get("Retry-After", "15")))
        raise RuntimeError("operator login stayed rate limited")

    def call(self, method: str, path: str, body: Any = None) -> Response:
        r = request(method, BASE_URL + path, body, self.headers)
        if r.status == 401:
            self.login()
            r = request(method, BASE_URL + path, body, self.headers)
        return r

    def lead_by_email(self, email: str) -> dict[str, Any] | None:
        r = self.call("GET", "/api/v1/leads?limit=5&q=" + urllib.parse.quote(email))
        assert r.status == 200, r.status
        hits = [x for x in r.body["items"] if x["email"].lower() == email.lower()]
        return hits[0] if hits else None

    def leads_matching(self, email: str) -> list[dict[str, Any]]:
        r = self.call("GET", "/api/v1/leads?limit=20&q=" + urllib.parse.quote(email))
        return [x for x in r.body["items"] if x["email"].lower() == email.lower()]

    def detail(self, lead_id: str) -> dict[str, Any]:
        r = self.call("GET", f"/api/v1/leads/{lead_id}")
        assert r.status == 200, r.status
        return r.body  # type: ignore[no-any-return]

    def history(self, lead_id: str) -> list[dict[str, Any]]:
        r = self.call("GET", f"/api/v1/leads/{lead_id}/history")
        assert r.status == 200, r.status
        return r.body  # type: ignore[no-any-return]


def submission_id(scenario: str, n: int) -> str:
    return hashlib.sha256(f"{scenario}:{n}".encode()).hexdigest()[:32]


def submit_form(lead: dict[str, Any], sub_id: str | None) -> list[Response]:
    """POST the public form; on 429 wait as told and try again. Returns every attempt."""
    attempts = []
    body = {**lead, **({"submission_id": sub_id} if sub_id else {})}
    for _ in range(20):
        r = request("POST", f"{BASE_URL}/api/v1/public/leads", body)
        attempts.append(r)
        if r.status != 429:
            break
        time.sleep(int(r.headers.get("Retry-After", "10")) + 1)
    return attempts


def send_webhook(
    event: dict[str, Any],
    *,
    source: str = "partner-demo",
    key: str | None = None,
    ts: int | None = None,
) -> list[Response]:
    secret_key = key or json.loads(secret("webhook_secrets"))["partner-demo"]
    raw = json.dumps(event).encode()
    attempts = []
    for _ in range(20):
        stamp = int(time.time()) if ts is None else ts
        signature = (
            "v1="
            + hmac.new(secret_key.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
        )
        r = request(
            "POST",
            f"{BASE_URL}/api/v1/webhooks/leads/{source}",
            raw=raw,
            headers={"X-Lead-Timestamp": str(stamp), "X-Lead-Signature": signature},
        )
        attempts.append(r)
        if r.status != 429:
            break
        time.sleep(int(r.headers.get("Retry-After", "10")) + 1)
    return attempts


def llm_fault(email: str, mode: str, remaining: int) -> None:
    r = request(
        "POST",
        f"{API_URL}/internal/v1/test/fault-rules",
        {"target": "llm", "mode": mode, "email": email, "remaining": remaining},
        {
            "Authorization": f"Bearer {secret('internal_token')}",
            "X-Fault-Admin-Token": secret("fault_admin_token"),
        },
    )
    if r.status != 201:
        raise RuntimeError("model fault injection is off: use compose.eval.yaml")


def crm_fault(email: str, operation: str, status: int, remaining: int) -> None:
    r = request(
        "POST",
        f"{CRM_URL}/_admin/faults",
        {"operation": operation, "email": email, "status": status, "remaining": remaining},
        {"X-Admin-Token": secret("crm_admin_token")},
    )
    if r.status != 201:
        raise RuntimeError("CRM fault injection is off: use compose.eval.yaml")


def crm_contacts(email: str) -> list[dict[str, Any]]:
    body = {
        "filterGroups": [
            {"filters": [{"propertyName": "email", "operator": "EQ", "value": email.lower()}]}
        ]
    }
    r = request(
        "POST",
        f"{CRM_URL}/crm/v3/objects/contacts/search",
        body,
        {"Authorization": f"Bearer {secret('crm_token')}"},
    )
    assert r.status == 200, r.status
    return r.body["results"]  # type: ignore[no-any-return]


def crm_stats() -> dict[str, Any]:
    r = request(
        "GET", f"{CRM_URL}/_admin/stats", headers={"X-Admin-Token": secret("crm_admin_token")}
    )
    assert r.status == 200, r.status
    return r.body  # type: ignore[no-any-return]


def mail_to(address: str) -> list[dict[str, Any]]:
    r = request("GET", f"{MAILPIT_URL}/api/v1/search?query=" + urllib.parse.quote("to:" + address))
    assert r.status == 200, r.status
    return r.body.get("messages") or []  # type: ignore[no-any-return]


def mail_total() -> int:
    r = request("GET", f"{MAILPIT_URL}/api/v1/messages?limit=1")
    assert r.status == 200, r.status
    return int(r.body.get("total", 0))
