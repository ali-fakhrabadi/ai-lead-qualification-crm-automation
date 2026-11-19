"""Screenshots of the real application and the n8n editor after the scenarios have run.

Runs after test_scenarios.py (file order), so the dashboard, queues, audit log and n8n
executions contain the synthetic leads those scenarios produced. Nothing here is mocked.
"""

from __future__ import annotations

import os
import re
from typing import Any

import pytest
from conftest import N8N, Api, screenshot, secret, sign_in
from playwright.sync_api import Page, expect

pytestmark = pytest.mark.pipeline
N8N_OWNER_EMAIL = os.environ.get("E2E_N8N_OWNER_EMAIL", "owner@example.com")
PIPELINE_WORKFLOW = "leadPipeline0001"


def test_login_page(new_page: Any) -> None:
    page = new_page()
    page.goto("/login")
    page.get_by_label("Email").fill("olivia.operator@northwind-automation.example")
    screenshot(page, "01-login")


def test_duplicate_queue(new_page: Any) -> None:
    page = new_page()
    sign_in(page, "operator")
    page.goto("/duplicates")
    expect(page.get_by_role("heading", name="Duplicate review")).to_be_visible()
    expect(page.get_by_text("meridian-analytics.example").first).to_be_visible()
    screenshot(page, "05-duplicate-detection", full_page=True)


def test_crm_sync_panel(new_page: Any, operator_api: Api) -> None:
    lead = operator_api.lead_by_email("lena.vogt@alpenrose-outdoor.example")
    assert lead is not None
    page = new_page(1440, 1100)
    sign_in(page, "operator")
    page.goto(f"/leads/{lead['id']}")
    panel = page.get_by_role("region", name="CRM sync")
    expect(panel).to_contain_text("synced", ignore_case=True)
    panel.scroll_into_view_if_needed()
    screenshot(page, "06-crm-sync-status")


def test_audit_log(new_page: Any) -> None:
    page = new_page()
    sign_in(page, "admin")
    page.goto("/audit")
    expect(page.get_by_role("heading", name="Audit log")).to_be_visible()
    expect(page.get_by_text(re.compile("follow.up delivered", re.I)).first).to_be_visible()
    screenshot(page, "08-audit-history")


def test_mobile_dashboard(new_page: Any) -> None:
    page = new_page(390, 844)
    sign_in(page, "operator")
    expect(page.get_by_role("heading", name="Pipeline")).to_be_visible()
    screenshot(page, "12-responsive-mobile-view", full_page=True)


# --- n8n editor ----------------------------------------------------------------------------------
def n8n_sign_in(page: Page) -> None:
    page.goto("/signin")
    page.locator("input[type=email], input[name=emailOrLdapLoginId]").first.fill(N8N_OWNER_EMAIL)
    page.locator("input[type=password]").first.fill(secret("n8n_owner_password"))
    page.get_by_role("button", name="Sign in").click()
    page.wait_for_url(lambda url: "/signin" not in url, timeout=30_000)


def settle(page: Page) -> None:
    page.wait_for_load_state("networkidle")
    page.keyboard.press("Escape")  # close any first-run tip
    page.wait_for_timeout(1500)


def zoom_in(page: Page, steps: int) -> None:
    """Zoom the canvas in from the fitted view so node names are readable."""
    button = page.locator("[data-test-id='zoom-in-button']")
    for _ in range(steps):
        if button.count():
            button.first.click()
        else:
            page.keyboard.press("=")
        page.wait_for_timeout(400)


def execution_id(operator_api: Api, email: str) -> str:
    lead = operator_api.lead_by_email(email)
    assert lead is not None
    detail = operator_api.get(f"/api/v1/leads/{lead['id']}")[1]
    runs = [
        w
        for w in detail["workflow_events"]
        if w["event_type"] == "lead.received" and w["workflow_execution_id"]
    ]
    assert runs, f"no recorded n8n execution for {email}"
    return str(runs[-1]["workflow_execution_id"])


@pytest.mark.n8n_ui
def test_n8n_canvas_and_executions(new_page: Any, operator_api: Api) -> None:
    page = new_page(1600, 1000, base_url=N8N)
    n8n_sign_in(page)
    page.goto(f"/workflow/{PIPELINE_WORKFLOW}")
    expect(page.locator(".vue-flow").first).to_be_visible(timeout=30_000)
    settle(page)
    page.keyboard.press("Shift+1")  # zoom to fit
    page.wait_for_timeout(800)
    zoom_in(page, 2)
    screenshot(page, "09-n8n-workflow-canvas")

    ok = execution_id(operator_api, "maya.chen@harborview-logistics.example")
    page.goto(f"/workflow/{PIPELINE_WORKFLOW}/executions/{ok}")
    expect(page.get_by_text("Succeeded").first).to_be_visible(timeout=30_000)
    settle(page)
    zoom_in(page, 1)
    screenshot(page, "10-n8n-successful-execution")

    retried = execution_id(operator_api, "lena.vogt@alpenrose-outdoor.example")
    page.goto(f"/workflow/{PIPELINE_WORKFLOW}/executions/{retried}")
    expect(page.locator(".vue-flow").first).to_be_visible(timeout=30_000)
    settle(page)
    zoom_in(page, 1)
    screenshot(page, "11-n8n-retry-error-handling")
