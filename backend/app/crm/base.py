"""What every CRM connector shares: the connector interface, errors, retries, attempt records.

A connector is a small class with the operations the sync service needs (``CrmConnector``).
HTTP connectors subclass ``HttpCrmClient``, which owns the retry loop and the
transient/permanent classification, so every connector behaves the same under failure:

- timeouts, connection errors, ``429`` and ``5xx`` are transient: retried with bounded
  exponential backoff and full jitter, honouring ``Retry-After`` up to the cap;
- every other ``4xx`` is permanent and raised at once (``unauthorized``, ``forbidden``,
  ``not_found``, ``rejected``);
- each HTTP attempt is reported to ``record`` so retries are measurable.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx

Recorder = Callable[[str, str, int | None, int, str | None], None]


class CrmError(Exception):
    def __init__(self, message: str, *, transient: bool, code: str, http_status: int | None):
        super().__init__(message)
        self.transient = transient
        self.code = code
        self.http_status = http_status


class CrmConnector(Protocol):
    """The operations ``app.services.crm_sync`` uses. Ids are the CRM's own, as strings.

    ``create_contact`` must be safe against a concurrent create of the same email: it returns
    the existing contact's id instead of creating a second one.
    """

    def find_contact(self, email: str) -> str | None: ...
    def create_contact(self, properties: dict[str, Any]) -> str: ...
    def update_contact(self, contact_id: str, properties: dict[str, Any]) -> None: ...
    def find_company(self, domain: str) -> str | None: ...
    def create_company(self, name: str, domain: str) -> str: ...
    def associate(self, contact_id: str, company_id: str) -> None: ...
    def add_note(self, contact_id: str, body: str, timestamp_iso: str) -> str: ...
    def close(self) -> None: ...


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


PERMANENT_CODES = {401: "unauthorized", 403: "forbidden", 404: "not_found"}


class HttpCrmClient:
    """httpx client with the shared retry loop. ``transport`` and ``sleep`` are injectable."""

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
        headers: Mapping[str, str] | None = None,
    ):
        self.retry = retry or RetryPolicy()
        self.record = record or (lambda *_: None)
        self.sleep = sleep
        self.http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                **(headers or {}),
            },
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
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        error: CrmError | None = None
        for attempt in range(1, self.retry.max_attempts + 1):
            started = time.perf_counter()
            try:
                response = self.http.request(method, path, json=json, params=params)
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
                    raise CrmError(
                        f"CRM refused the request ({status})",
                        transient=False,
                        code=PERMANENT_CODES.get(status, "rejected"),
                        http_status=status,
                    )
            if attempt < self.retry.max_attempts:
                self.sleep(wait)
        assert error is not None
        raise error

    @staticmethod
    def _field(data: Any, *path: str) -> str:
        """``data[path[0]][path[1]]…`` as a string, or a permanent error if the shape is wrong."""
        value = data
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is None or value == "":
            raise CrmError(
                f"CRM answer has no {'.'.join(path)}",
                transient=False,
                code="unexpected_response",
                http_status=None,
            )
        return str(value)
