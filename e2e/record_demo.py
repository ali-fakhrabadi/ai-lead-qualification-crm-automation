"""Record the product walkthrough from the real running application (Playwright video).

    e2e/record_demo.sh        # see docs/demo/video-recording-notes.md

Each scene is recorded in its own browser context (its own .webm). Waiting for the model and
the workflow happens between scenes, off camera; nothing on screen is staged: every screen is
the application, n8n or the mail sink showing what really happened to this synthetic lead. A
caption bar is drawn over the page to say what is being shown. Scenes are joined (with the
title and closing cards) by ffmpeg afterwards.
"""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path
from typing import Any

from conftest import ACCOUNTS, BASE_URL, MAILPIT, N8N, Api, http, mail_to, secret
from playwright.sync_api import Browser, BrowserContext, Page, sync_playwright

OUT = Path(os.environ.get("DEMO_OUT", "/out/demo"))
SIZE = {"width": 1440, "height": 900}
OWNER = os.environ.get("E2E_N8N_OWNER_EMAIL", "owner@example.com")

LEAD = {
    "first_name": "Isla",
    "last_name": "Morgan",
    "email": "isla.morgan@fernbrook-vets.example",
    "company": "Fernbrook Veterinary Clinics",
    "website": "fernbrook-vets.example",
    "job_title": "Operations Manager",
    "message": (
        "We run three veterinary clinics and get about 80 appointment and boarding inquiries a "
        "week from our website and Facebook. Staff copy them into HubSpot by hand and some get "
        "lost. We want them synced to HubSpot automatically, with duplicates avoided and a "
        "reply drafted for staff to check. We would like this live within six weeks."
    ),
}

CAPTION_JS = """
(() => {
  const make = () => {
    if (document.getElementById('demo-caption')) return;
    const bar = document.createElement('div');
    bar.id = 'demo-caption';
    bar.style.cssText = 'position:fixed;left:50%;bottom:28px;transform:translateX(-50%);' +
      'z-index:2147483647;max-width:1100px;padding:14px 26px;border-radius:14px;' +
      'background:rgba(13,17,23,.92);color:#fff;font:600 22px/1.35 Inter,system-ui,sans-serif;' +
      'box-shadow:0 12px 40px rgba(1,4,9,.45);border:2px solid #2EA043;pointer-events:none;' +
      'transition:opacity .25s;opacity:0';
    document.body.appendChild(bar);
  };
  window.setCaption = (text) => {
    make();
    const bar = document.getElementById('demo-caption');
    bar.textContent = text;
    bar.style.opacity = text ? '1' : '0';
  };
  document.addEventListener('DOMContentLoaded', make);
})();
"""


class Scene:
    def __init__(self, browser: Browser, name: str, base_url: str = BASE_URL) -> None:
        self.name = name
        self.context: BrowserContext = browser.new_context(
            base_url=base_url,
            viewport=SIZE,
            record_video_dir=str(OUT / "raw"),
            record_video_size=SIZE,
            locale="en-GB",
            timezone_id="UTC",
        )
        self.context.add_init_script(CAPTION_JS)
        self.page: Page = self.context.new_page()

    def caption(self, text: str, hold: float = 0) -> None:
        self.page.evaluate("t => window.setCaption(t)", text)
        if hold:
            self.page.wait_for_timeout(int(hold * 1000))

    def close(self) -> None:
        video = self.page.video
        self.context.close()
        if video is not None:
            target = OUT / f"{self.name}.webm"
            shutil.move(video.path(), target)
            print(f"recorded {target.name}", flush=True)


def sign_in(page: Page, account: str) -> None:
    page.goto("/login")
    page.get_by_label("Email").type(ACCOUNTS[account], delay=25)
    page.get_by_label("Password").fill(secret("demo_password"))
    page.get_by_role("button", name="Sign in").click()
    page.get_by_role("heading", name="Pipeline").wait_for()


def fill_form(page: Page, lead: dict[str, Any], delay: int) -> None:
    page.goto("/demo-form")
    page.get_by_role("option", name="CRM integration").wait_for(state="attached")
    for label, key in (
        ("First name", "first_name"),
        ("Last name", "last_name"),
        ("Work email", "email"),
        ("Company", "company"),
        ("Website", "website"),
        ("Job title", "job_title"),
    ):
        page.get_by_label(label).type(lead[key], delay=delay)
    page.get_by_label("What do you need help with?").select_option("crm_integration")
    page.get_by_label("Tell us about your project").fill(lead["message"])
    page.get_by_label("I agree that Northwind").check()


def wait_status(api: Api, statuses: set[str], timeout_s: int = 1800) -> dict[str, Any]:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        lead = api.lead_by_email(LEAD["email"])
        if lead and lead["status"] in statuses:
            detail = api.get(f"/api/v1/leads/{lead['id']}")[1]
            if all(w["status"] in ("completed", "dead") for w in detail["workflow_events"]):
                return detail  # type: ignore[no-any-return]
        time.sleep(3)
    raise RuntimeError(f"lead did not reach {statuses}")


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    api = Api("operator")
    assert api.lead_by_email(LEAD["email"]) is None, "use a stack without the demo lead"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()

        s = Scene(browser, "01-form")
        s.page.goto("/demo-form")
        s.caption("A prospect fills in the website form (synthetic data)", 2.5)
        fill_form(s.page, LEAD, delay=35)
        s.page.get_by_label("Tell us about your project").scroll_into_view_if_needed()
        s.caption("Consent is required; a hidden honeypot field catches bots", 2.5)
        s.page.get_by_role("button", name="Send request").click()
        s.page.get_by_role("status").wait_for()
        s.caption("Stored with an outbox event in one transaction: 202 Accepted + reference", 3.5)
        s.close()

        detail = wait_status(api, {"follow_up_draft_ready", "needs_review"})
        print(f"pipeline finished: {detail['status']}", flush=True)

        s = Scene(browser, "02-duplicate")
        s.caption("The same person sends the same request again", 1.5)
        fill_form(s.page, LEAD, delay=0)
        s.page.get_by_role("button", name="Send request").click()
        s.page.get_by_role("status").wait_for()
        s.caption("Recognised by the normalised email: no second lead, no second workflow run", 4)
        s.close()

        s = Scene(browser, "03-qualification")
        sign_in(s.page, "operator")
        s.caption("Operator dashboard: every lead and the stage it is in", 3)
        s.page.goto(f"/leads/{detail['id']}")
        s.page.get_by_label("Rules score").wait_for()
        s.caption("n8n ran the pipeline: AI qualification, CRM sync, draft reply", 3)
        s.page.get_by_role("heading", name="AI qualification").scroll_into_view_if_needed()
        s.caption(
            "The local model suggests intent, fit and urgency; code computes score and decision", 4
        )
        s.page.get_by_text("Evidence found in the message").scroll_into_view_if_needed()
        s.caption("Evidence quotes must appear in the message; the summary is advisory", 3.5)
        s.page.get_by_role("heading", name="CRM sync").scroll_into_view_if_needed()
        s.caption(
            "CRM upsert: contact found by email, company by domain, one note per assessment", 3.5
        )
        s.page.get_by_role("heading", name="Submissions").scroll_into_view_if_needed()
        s.caption("Both submissions are recorded; the second was ignored as a duplicate", 3)
        s.close()

        s = Scene(browser, "04-approval")
        sign_in(s.page, "operator")
        s.page.goto(f"/leads/{detail['id']}")
        s.page.get_by_role("heading", name="Follow-up draft").scroll_into_view_if_needed()
        s.caption("A reply is drafted, never sent: a person reviews it first", 3)
        body = s.page.get_by_label("Message", exact=True)
        body.click()
        s.page.keyboard.press("Control+End")
        s.page.keyboard.type("\n\nP.S. Happy to show a similar setup on a short call.", delay=30)
        s.page.get_by_role("button", name="Save edits").click()
        s.page.get_by_role("status").wait_for()
        s.page.get_by_label("Decision note").type("Checked facts and tone", delay=30)
        s.caption("Edits and the approval are audited", 1.5)
        s.page.get_by_role("button", name="Approve").click()
        s.page.get_by_role("status").filter(has_text="Approved").wait_for()
        s.caption(
            "Approved: n8n delivers it to the configured channel (a local mail sink here)", 3.5
        )
        s.close()

        deadline = time.time() + 300
        while time.time() < deadline and not mail_to(LEAD["email"]):
            time.sleep(2)
        message_id = mail_to(LEAD["email"])[0]["ID"]

        s = Scene(browser, "05-mail-sink", base_url=MAILPIT)
        s.page.goto(f"/view/{message_id}")
        s.page.wait_for_load_state("networkidle")
        s.caption("Delivered once, to the local sink only: no real email leaves the lab", 4)
        s.close()

        lead = api.get(f"/api/v1/leads/{detail['id']}")[1]
        run = next(w for w in lead["workflow_events"] if w["event_type"] == "lead.received")
        s = Scene(browser, "06-n8n", base_url=N8N)
        s.page.goto("/signin")
        s.page.locator("input[type=email], input[name=emailOrLdapLoginId]").first.fill(OWNER)
        s.page.locator("input[type=password]").first.fill(secret("n8n_owner_password"))
        s.page.get_by_role("button", name="Sign in").click()
        s.page.wait_for_url(lambda url: "/signin" not in url, timeout=30_000)
        s.page.goto(f"/workflow/leadPipeline0001/executions/{run['workflow_execution_id']}")
        s.page.get_by_text("Succeeded").first.wait_for(timeout=30_000)
        s.page.wait_for_timeout(1500)
        s.page.keyboard.press("Escape")
        s.caption("The n8n execution for this lead: retries, waits and the outcome report", 5)
        s.close()

        s = Scene(browser, "07-audit")
        sign_in(s.page, "admin")
        s.page.goto("/audit")
        s.page.get_by_role("heading", name="Audit log").wait_for()
        s.caption("Audit log: who decided what, when; rejected webhooks and denied access too", 4.5)
        s.close()
        browser.close()
    status, _ = http("GET", f"{BASE_URL}/api/health")
    print(f"done (api health {status}); scenes in {OUT}", flush=True)


if __name__ == "__main__":
    main()
