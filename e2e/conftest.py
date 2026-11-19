"""Browser and API fixtures for end-to-end tests against a running deployment.

Run against a fresh stack started with the evaluation overlay (fault injection on):
see e2e/run.sh. The container joins the stack's internal network, so it reaches the web app,
Mailpit's API, the demo CRM and the n8n editor directly; nothing is published for it.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from playwright.sync_api import Browser, Page, sync_playwright

BASE_URL = os.environ.get("E2E_BASE_URL", "http://web:8080")
MAILPIT = os.environ.get("E2E_MAILPIT_URL", "http://mailpit:8025")
CRM = os.environ.get("E2E_CRM_URL", "http://crm-demo:8100")
N8N = os.environ.get("E2E_N8N_URL", "http://n8n:5678")
SCREENSHOTS = Path(os.environ.get("E2E_SCREENSHOTS", "/out/screenshots"))
PIPELINE_TIMEOUT_S = int(os.environ.get("E2E_PIPELINE_TIMEOUT_S", "900"))

ACCOUNTS = {
    "admin": "admin@northwind-automation.example",
    "operator": "olivia.operator@northwind-automation.example",
    "viewer": "victor.viewer@northwind-automation.example",
}


def secret(name: str) -> str:
    return Path(f"/run/secrets/{name}").read_text(encoding="utf-8").strip()


def http(
    method: str,
    url: str,
    body: Any = None,
    headers: dict[str, str] | None = None,
    raw: bytes | None = None,
    timeout: float = 60,
) -> tuple[int, Any]:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(  # noqa: S310 - test URLs from the environment
        url,
        data=data,
        method=method,
        headers={
            "Content-Type": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:  # noqa: S310 - test URLs
            text = response.read().decode()
            return response.status, json.loads(text) if text.strip() else None
    except urllib.error.HTTPError as exc:
        text = exc.read().decode()
        try:
            return exc.code, json.loads(text) if text.strip() else None
        except json.JSONDecodeError:
            return exc.code, text


class Api:
    """The operator API as a given account; signs in again when the access token expires."""

    def __init__(self, account: str) -> None:
        self.account = account
        self.headers: dict[str, str] = {}
        self.login()

    def login(self) -> None:
        body = {"email": ACCOUNTS[self.account], "password": secret("demo_password")}
        for _ in range(6):
            status, data = http("POST", f"{BASE_URL}/api/v1/auth/login", body)
            if status != 429:
                break
            time.sleep(15)
        assert status == 200, f"login as {self.account} failed with {status}"
        self.headers = {"Authorization": f"Bearer {data['access_token']}"}

    def call(self, method: str, path: str, body: Any = None) -> tuple[int, Any]:
        status, data = http(method, BASE_URL + path, body, self.headers)
        if status == 401:  # access tokens are short-lived; the pipeline waits are not
            self.login()
            status, data = http(method, BASE_URL + path, body, self.headers)
        return status, data

    def get(self, path: str) -> tuple[int, Any]:
        return self.call("GET", path)

    def post(self, path: str, body: Any = None) -> tuple[int, Any]:
        return self.call("POST", path, body if body is not None else {})

    def lead_by_email(self, email: str) -> dict[str, Any] | None:
        status, page = self.get(f"/api/v1/leads?q={urllib.request.quote(email)}&limit=5")
        assert status == 200
        hits = [lead for lead in page["items"] if lead["email"].lower() == email.lower()]
        return hits[0] if hits else None

    def wait_for_lead(
        self, email: str, statuses: set[str], timeout_s: int = PIPELINE_TIMEOUT_S
    ) -> dict[str, Any]:
        """Poll until the lead reaches one of ``statuses``; returns its detail."""
        deadline = time.time() + timeout_s
        last = None
        while time.time() < deadline:
            lead = self.lead_by_email(email)
            if lead is not None:
                last = lead["status"]
                if last in statuses:
                    return self.get(f"/api/v1/leads/{lead['id']}")[1]  # type: ignore[no-any-return]
            time.sleep(3)
        raise AssertionError(f"{email} did not reach {sorted(statuses)} (last status: {last})")

    def wait_for_run(self, lead_id: str, timeout_s: int = 120) -> dict[str, Any]:
        """Wait until n8n has reported the outcome of every workflow event of the lead."""
        deadline = time.time() + timeout_s
        detail: dict[str, Any] = {}
        while time.time() < deadline:
            detail = self.get(f"/api/v1/leads/{lead_id}")[1]
            if all(w["status"] in ("completed", "dead") for w in detail["workflow_events"]):
                return detail
            time.sleep(2)
        return detail


def submit_form(lead: dict[str, Any]) -> tuple[int, Any]:
    return http("POST", f"{BASE_URL}/api/v1/public/leads", lead)


def send_webhook(
    event: dict[str, Any],
    *,
    source: str = "partner-demo",
    secret_value: str | None = None,
    ts: int | None = None,
) -> tuple[int, Any]:
    key = secret_value or json.loads(secret("webhook_secrets"))[source]
    raw = json.dumps(event).encode()
    stamp = int(time.time()) if ts is None else ts
    signature = (
        "v1=" + hmac.new(key.encode(), f"{stamp}.".encode() + raw, hashlib.sha256).hexdigest()
    )
    return http(
        "POST",
        f"{BASE_URL}/api/v1/webhooks/leads/{source}",
        raw=raw,
        headers={"X-Lead-Timestamp": str(stamp), "X-Lead-Signature": signature},
    )


def llm_fault(email: str, mode: str, remaining: int = 1) -> None:
    status, _ = http(
        "POST",
        "http://api:8000/internal/v1/test/fault-rules",
        {"target": "llm", "mode": mode, "email": email, "remaining": remaining},
        {
            "Authorization": f"Bearer {secret('internal_token')}",
            "X-Fault-Admin-Token": secret("fault_admin_token"),
        },
    )
    assert status == 201, "fault injection is off: start the stack with compose.eval.yaml"


def crm_fault(operation: str, status_code: int, remaining: int, email: str = "*") -> None:
    status, _ = http(
        "POST",
        f"{CRM}/_admin/faults",
        {"operation": operation, "email": email, "status": status_code, "remaining": remaining},
        {"X-Admin-Token": secret("crm_admin_token")},
    )
    assert status == 201, "CRM fault injection is off: start the stack with compose.eval.yaml"


def crm_contacts(email: str) -> list[dict[str, Any]]:
    body = {
        "filterGroups": [{"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}]
    }
    status, data = http(
        "POST",
        f"{CRM}/crm/v3/objects/contacts/search",
        body,
        {"Authorization": f"Bearer {secret('crm_token')}"},
    )
    assert status == 200
    return data["results"]  # type: ignore[no-any-return]


def mail_to(address: str) -> list[dict[str, Any]]:
    status, data = http(
        "GET", f"{MAILPIT}/api/v1/search?query={urllib.request.quote('to:' + address)}"
    )
    assert status == 200
    return data.get("messages") or []  # type: ignore[no-any-return]


def mail_text(message_id: str) -> str:
    status, data = http("GET", f"{MAILPIT}/api/v1/message/{message_id}")
    assert status == 200
    return str(data.get("Text", ""))


@pytest.fixture(scope="session")
def browser() -> Iterator[Browser]:
    with sync_playwright() as playwright:
        instance = playwright.chromium.launch()
        yield instance
        instance.close()


@pytest.fixture
def new_page(browser: Browser) -> Iterator[Callable[..., Page]]:
    contexts = []

    def make(width: int = 1440, height: int = 900, base_url: str = BASE_URL) -> Page:
        context = browser.new_context(
            base_url=base_url,
            viewport={"width": width, "height": height},
            locale="en-GB",
            timezone_id="UTC",
        )
        contexts.append(context)
        return context.new_page()

    yield make
    for context in contexts:
        context.close()


def sign_in(page: Page, account: str) -> None:
    """Sign in through the form. Failures report the alert text only, never the password."""
    for _ in range(5):
        page.goto("/login")
        page.get_by_label("Email").fill(ACCOUNTS[account])
        page.get_by_label("Password").fill(secret("demo_password"))
        page.get_by_role("button", name="Sign in").click()
        page.locator("h1:text-is('Pipeline'), [role=alert]").first.wait_for(timeout=20_000)
        if page.get_by_role("heading", name="Pipeline").count():
            return
        message = page.get_by_role("alert").inner_text()
        page.get_by_label("Password").fill("")
        if "Too many requests" not in message:
            raise AssertionError(f"sign-in as {account} failed: {message}")
        time.sleep(15)
    raise AssertionError(f"sign-in as {account} stayed rate limited")


def screenshot(page: Page, name: str, *, full_page: bool = False) -> None:
    SCREENSHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SCREENSHOTS / f"{name}.png"), full_page=full_page)


@pytest.fixture(scope="session")
def operator_api() -> Api:
    return Api("operator")


@pytest.fixture(scope="session")
def viewer_api() -> Api:
    return Api("viewer")
