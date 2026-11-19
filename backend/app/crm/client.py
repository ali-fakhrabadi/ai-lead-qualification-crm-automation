"""CRM adapter for the HubSpot CRM v3 API shape.

Used for both providers:

- ``demo``: the bundled demo CRM service, which implements the subset of the API below so the
  whole workflow runs without an external account;
- ``hubspot``: a real HubSpot account (private app token). Not verified against a live account
  in this project; see docs/crm-integration.md.

Operations: search a contact by email, create, update, search/create a company by domain,
associate contact and company, add a note. Every HTTP attempt is reported to ``record`` so
retries are measurable. Transient errors (timeouts, 429, 5xx) are retried with bounded
exponential backoff and full jitter, honouring ``Retry-After`` up to the cap; anything else is
permanent and raised at once.
"""

from __future__ import annotations

import random
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

Recorder = Callable[[str, str, int | None, int, str | None], None]
NOTE_TO_CONTACT = 202  # HUBSPOT_DEFINED association type id: note -> contact


class CrmError(Exception):
    def __init__(self, message: str, *, transient: bool, code: str, http_status: int | None):
        super().__init__(message)
        self.transient = transient
        self.code = code
        self.http_status = http_status


@dataclass
class RetryPolicy:
    max_attempts: int = 3
    base_s: float = 0.5
    cap_s: float = 4.0

    def delay(self, attempt: int, retry_after: float | None = None) -> float:
        if retry_after is not None:
            return min(retry_after, self.cap_s)
        return random.uniform(0, min(self.cap_s, self.base_s * 2 ** (attempt - 1)))  # noqa: S311


def _retry_after(response: httpx.Response) -> float | None:
    value = response.headers.get("retry-after")
    try:
        return float(value) if value else None
    except ValueError:
        return None


class HubSpotStyleClient:
    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 10.0,
        retry: RetryPolicy | None = None,
        record: Recorder | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self.retry = retry or RetryPolicy()
        self.record = record or (lambda *_: None)
        self.sleep = sleep
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            timeout=timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self.http.close()

    def _request(
        self,
        operation: str,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
        accept: tuple[int, ...] = (),
    ) -> httpx.Response:
        for attempt in range(1, self.retry.max_attempts + 1):
            started = time.perf_counter()
            try:
                response = self.http.request(method, path, json=json)
            except httpx.TimeoutException:
                ms = int((time.perf_counter() - started) * 1000)
                self.record(operation, "transient", None, ms, "timeout")
                error = CrmError(
                    "CRM request timed out", transient=True, code="timeout", http_status=None
                )
                wait = self.retry.delay(attempt)
            except httpx.TransportError as exc:
                ms = int((time.perf_counter() - started) * 1000)
                self.record(operation, "transient", None, ms, type(exc).__name__)
                error = CrmError(
                    "CRM unreachable", transient=True, code="unreachable", http_status=None
                )
                wait = self.retry.delay(attempt)
            else:
                ms = int((time.perf_counter() - started) * 1000)
                status = response.status_code
                if status < 400 or status in accept:
                    self.record(operation, "success", status, ms, None)
                    return response
                if status == 429 or status >= 500:
                    self.record(operation, "transient", status, ms, f"HTTP {status}")
                    code = "rate_limited" if status == 429 else "server_error"
                    error = CrmError(
                        f"CRM returned {status}", transient=True, code=code, http_status=status
                    )
                    wait = self.retry.delay(attempt, _retry_after(response))
                else:
                    self.record(operation, "permanent", status, ms, f"HTTP {status}")
                    code = {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(
                        status, "rejected"
                    )
                    raise CrmError(
                        f"CRM refused the request ({status})",
                        transient=False,
                        code=code,
                        http_status=status,
                    )
            if attempt < self.retry.max_attempts:
                self.sleep(wait)
        raise error

    # --- contacts ---------------------------------------------------------------------------
    def find_contact(self, email: str) -> str | None:
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "email", "operator": "EQ", "value": email}]}
            ],
            "properties": ["email"],
            "limit": 1,
        }
        data = self._request(
            "search_contact", "POST", "/crm/v3/objects/contacts/search", body
        ).json()
        results = data.get("results") or []
        return str(results[0]["id"]) if results else None

    def create_contact(self, properties: dict[str, Any]) -> str:
        response = self._request(
            "create_contact",
            "POST",
            "/crm/v3/objects/contacts",
            {"properties": properties},
            accept=(409,),
        )
        if response.status_code == 409:
            # Created meanwhile (or search not yet consistent): HubSpot names the existing id.
            match = re.search(r"Existing ID:\s*(\d+)", response.text)
            if not match:
                raise CrmError(
                    "CRM reported a conflict without an id",
                    transient=False,
                    code="conflict",
                    http_status=409,
                )
            existing = match.group(1)
            self.update_contact(existing, properties)
            return existing
        return str(response.json()["id"])

    def update_contact(self, contact_id: str, properties: dict[str, Any]) -> None:
        self._request(
            "update_contact",
            "PATCH",
            f"/crm/v3/objects/contacts/{contact_id}",
            {"properties": properties},
        )

    # --- companies --------------------------------------------------------------------------
    def find_company(self, domain: str) -> str | None:
        body = {
            "filterGroups": [
                {"filters": [{"propertyName": "domain", "operator": "EQ", "value": domain}]}
            ],
            "properties": ["domain"],
            "limit": 1,
        }
        data = self._request(
            "search_company", "POST", "/crm/v3/objects/companies/search", body
        ).json()
        results = data.get("results") or []
        return str(results[0]["id"]) if results else None

    def create_company(self, name: str, domain: str) -> str:
        data = self._request(
            "create_company",
            "POST",
            "/crm/v3/objects/companies",
            {"properties": {"name": name, "domain": domain}},
        ).json()
        return str(data["id"])

    def associate(self, contact_id: str, company_id: str) -> None:
        self._request(
            "associate",
            "PUT",
            f"/crm/v4/objects/contact/{contact_id}/associations/default/company/{company_id}",
        )

    def add_note(self, contact_id: str, body: str, timestamp_iso: str) -> str:
        payload = {
            "properties": {"hs_note_body": body, "hs_timestamp": timestamp_iso},
            "associations": [
                {
                    "to": {"id": contact_id},
                    "types": [
                        {
                            "associationCategory": "HUBSPOT_DEFINED",
                            "associationTypeId": NOTE_TO_CONTACT,
                        }
                    ],
                }
            ],
        }
        data = self._request("create_note", "POST", "/crm/v3/objects/notes", payload).json()
        return str(data["id"])
