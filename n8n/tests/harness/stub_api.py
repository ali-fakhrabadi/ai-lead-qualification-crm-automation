"""A scriptable stand-in for the FastAPI internal API, for testing the n8n workflows alone.

The workflow harness runs the real exported workflows in a disposable n8n and points them at
this server (as host ``api``). By default it behaves like the real API: it remembers each
lead's progress, so ``next-step`` resumes where the lead stands, a repeated CRM sync updates
instead of creating, and a draft is created once. A test can script the next responses of any
step for a lead (HTTP status, body, headers) to simulate rate limits, outages, permanent
errors or invalid model output. Every call is recorded for the assertions.

Standard library only. Requires the same service token as the real API.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

TOKEN = Path(os.environ.get("STUB_TOKEN_FILE", "/run/secrets/internal_token")).read_text().strip()
LOCK = threading.Lock()
CALLS: list[dict[str, Any]] = []
SCRIPTS: dict[tuple[str, str], list[dict[str, Any]]] = {}
LEADS: dict[str, dict[str, Any]] = {}
DRAFTS: dict[str, dict[str, Any]] = {}

ROUTES = [
    ("GET", re.compile(r"^/internal/v1/leads/([0-9a-f-]{36})/next-step$"), "next_step"),
    ("POST", re.compile(r"^/internal/v1/leads/([0-9a-f-]{36})/qualify$"), "qualify"),
    ("POST", re.compile(r"^/internal/v1/leads/([0-9a-f-]{36})/crm-sync$"), "crm_sync"),
    ("POST", re.compile(r"^/internal/v1/leads/([0-9a-f-]{36})/follow-up-draft$"), "draft"),
    ("POST", re.compile(r"^/internal/v1/follow-ups/([0-9a-f-]{36})/deliver$"), "deliver"),
    ("POST", re.compile(r"^/internal/v1/events/([0-9a-f-]{36})/started$"), "started"),
    ("POST", re.compile(r"^/internal/v1/events/([0-9a-f-]{36})/outcome$"), "outcome"),
    ("POST", re.compile(r"^/internal/v1/workflow-errors$"), "workflow_error"),
]


def lead_state(lead_id: str) -> dict[str, Any]:
    return LEADS.setdefault(
        lead_id,
        {
            "qualified": False,
            "review": False,
            "crm_contact": None,
            "draft_id": None,
            "reference": f"L-{len(LEADS) + 1001}",
        },
    )


def default(step: str, key: str) -> tuple[int, dict[str, Any]]:
    """What the real API would answer, given what already happened to this lead."""
    if step == "next_step":
        s = lead_state(key)
        if s["draft_id"]:
            return 200, {"lead_id": key, "status": "follow_up_draft_ready", "next": "done"}
        if s["review"]:
            return 200, {"lead_id": key, "status": "needs_review", "next": "stop"}
        if s["crm_contact"]:
            return 200, {"lead_id": key, "status": "synced_to_crm", "next": "follow_up_draft"}
        if s["qualified"]:
            return 200, {"lead_id": key, "status": "qualified", "next": "crm_sync"}
        return 200, {"lead_id": key, "status": "new", "next": "qualify"}
    if step == "qualify":
        lead_state(key)["qualified"] = True
        return 200, {
            "lead_id": key,
            "status": "qualified",
            "next": "crm_sync",
            "detail": {"decision": "qualified", "score": 85},
        }
    if step == "crm_sync":
        s = lead_state(key)
        created = s["crm_contact"] is None
        s["crm_contact"] = s["crm_contact"] or f"contact-{key[:8]}"
        return 200, {
            "lead_id": key,
            "status": "synced_to_crm",
            "next": "follow_up_draft",
            "detail": {"contact_id": s["crm_contact"], "created": created},
        }
    if step == "draft":
        s = lead_state(key)
        created = s["draft_id"] is None
        if created:
            s["draft_id"] = f"{key[:8]}-0000-4000-8000-{key[-12:]}"
            DRAFTS[s["draft_id"]] = {"lead_id": key, "delivered": 0}
        return 200, {
            "lead_id": key,
            "status": "follow_up_draft_ready",
            "next": "done",
            "detail": {"created": created, "draft_id": s["draft_id"], "reference": s["reference"]},
        }
    if step == "deliver":
        draft = DRAFTS.setdefault(key, {"lead_id": None, "delivered": 0})
        now = draft["delivered"] == 0
        draft["delivered"] = 1  # idempotent: a replay never sends twice
        return 200, {
            "lead_id": draft["lead_id"],
            "status": "follow_up_sent",
            "next": "done",
            "detail": {"delivered_now": now, "channel": "sink"},
        }
    return 200, {"ok": True}


def apply_scripted(step: str, key: str, item: dict[str, Any]) -> None:
    """Scripted successes still change state the way the real API would."""
    if step == "qualify" and item.get("status", 200) == 200:
        nxt = (item.get("body") or {}).get("next")
        s = lead_state(key)
        s["qualified"] = True
        s["review"] = nxt == "stop"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: Any) -> None:  # noqa: ARG002 - quiet
        return

    def reply(self, status: int, body: Any, headers: dict[str, str] | None = None) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def body(self) -> Any:
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            return json.loads(raw) if raw else None
        except json.JSONDecodeError:
            return raw.decode(errors="replace")

    def handle_any(self, method: str) -> None:
        payload = self.body()
        path = self.path.split("?", 1)[0]
        if path.startswith("/_stub/"):
            return self.admin(method, path, payload)
        if self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self.reply(401, {"error": {"code": "unauthorized"}})
        for m, pattern, step in ROUTES:
            match = pattern.match(path)
            if m == method and match:
                key = match.group(1) if match.groups() else "-"
                with LOCK:
                    queue = SCRIPTS.get((step, key)) or SCRIPTS.get((step, "*"))
                    if queue:
                        # Scripted answers are used once each; a "sticky" last one repeats.
                        sticky = len(queue) == 1 and queue[0].get("sticky")
                        item = queue[0] if sticky else queue.pop(0)
                        apply_scripted(step, key, item)
                        status, body = item.get("status", 200), item.get("body") or {}
                        headers = item.get("headers") or {}
                    else:
                        (status, body), headers = default(step, key), {}
                    CALLS.append(
                        {
                            "step": step,
                            "key": key,
                            "status": status,
                            "body": payload,
                            "response": body,
                            "ts": time.time(),
                        }
                    )
                return self.reply(status, body, headers)
        return self.reply(404, {"error": {"code": "not_found"}})

    def admin(self, method: str, path: str, payload: Any) -> None:
        with LOCK:
            if path == "/_stub/reset" and method == "POST":
                for store in (CALLS, SCRIPTS, LEADS, DRAFTS):
                    store.clear()
                return self.reply(200, {"ok": True})
            if path == "/_stub/script" and method == "POST":
                SCRIPTS[(payload["step"], payload.get("key", "*"))] = list(payload["responses"])
                return self.reply(200, {"ok": True})
            if path == "/_stub/calls" and method == "GET":
                return self.reply(200, {"calls": CALLS, "leads": LEADS, "drafts": DRAFTS})
        return self.reply(404, {"error": {"code": "not_found"}})

    def do_GET(self) -> None:
        self.handle_any("GET")

    def do_POST(self) -> None:
        self.handle_any("POST")


if __name__ == "__main__":
    ThreadingHTTPServer(("0.0.0.0", 8000), Handler).serve_forever()  # noqa: S104
