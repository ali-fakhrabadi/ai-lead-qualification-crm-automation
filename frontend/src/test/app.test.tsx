import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import {
  apiError,
  json,
  makeDetail,
  makeDraft,
  makeLead,
  mockApi,
  operatorMe,
  page,
  renderAt,
  signedInAs,
  tokens,
  viewerMe,
} from "./utils";

const stats = {
  by_status: { needs_review: 2, follow_up_draft_ready: 1 },
  by_decision: { qualified: 3, needs_review: 2 },
  open_duplicates: 1,
  drafts_awaiting_review: 1,
  events_pending: 0,
  events_failed: 0,
  leads_total: 5,
};

describe("authentication", () => {
  it("redirects to sign-in without a session", async () => {
    mockApi({});
    renderAt("/leads");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
  });

  it("shows the server's message on wrong credentials", async () => {
    mockApi({ "POST /api/v1/auth/login": () => apiError(401, "unauthorized", "Invalid email or password") });
    renderAt("/login");
    await userEvent.type(screen.getByLabelText("Email"), "olivia@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "wrong-password");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
  });

  it("signs in and lands on the pipeline", async () => {
    mockApi({
      "POST /api/v1/auth/login": tokens,
      "GET /api/v1/me": operatorMe,
      "GET /api/v1/stats": stats,
      "GET /api/v1/leads": page([makeLead({ status: "needs_review", review_reasons: ["possible_duplicate"] })]),
    });
    renderAt("/login");
    await userEvent.type(screen.getByLabelText("Email"), "olivia@example.com");
    await userEvent.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await userEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("heading", { name: "Pipeline" })).toBeInTheDocument();
    expect(await screen.findByText("Possible duplicate")).toBeInTheDocument();
    expect(screen.getByText("Operator")).toBeInTheDocument();
  });
});

describe("public lead form", () => {
  const config = {
    business_name: "Northwind Automation Studio",
    interests: [{ value: "crm_integration", label: "CRM integration" }],
    required_fields: ["first_name", "last_name", "email", "interest", "message", "consent_contact", "company"],
    consent_text: "I agree to be contacted.",
    consent_text_version: "2026-11",
  };

  async function fill() {
    await userEvent.type(screen.getByLabelText(/First name/), "Maya");
    await userEvent.type(screen.getByLabelText(/Last name/), "Chen");
    await userEvent.type(screen.getByLabelText(/Work email/), "maya@harborview.example");
    await userEvent.type(screen.getByLabelText(/Company/), "Harborview");
    await userEvent.selectOptions(screen.getByLabelText(/What do you need/), "crm_integration");
    await userEvent.type(screen.getByLabelText(/Tell us about/), "We want our forms synced into the CRM automatically.");
    await userEvent.click(screen.getByLabelText("I agree to be contacted."));
  }

  it("submits with a stable submission id and shows the reference", async () => {
    const calls = mockApi({
      "GET /api/v1/public/form-config": config,
      "POST /api/v1/public/leads": {
        status: "accepted",
        outcome: "created",
        lead_reference: "L-1001",
        correlation_id: "abc123",
        replayed: false,
      },
    });
    renderAt("/demo-form");
    await screen.findByRole("option", { name: "CRM integration" });
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Send request" }));
    expect(await screen.findByRole("status")).toHaveTextContent("L-1001");
    const body = JSON.parse(String(calls.find((c) => c.key === "POST /api/v1/public/leads")?.init?.body));
    expect(body.submission_id).toMatch(/^[0-9a-f]{32}$/);
    expect(body.nickname).toBeNull();
    expect(body.website).toBeNull();
  });

  it("shows field errors returned by the API without echoing values", async () => {
    mockApi({
      "GET /api/v1/public/form-config": config,
      "POST /api/v1/public/leads": () =>
        apiError(422, "validation_error", "Request validation failed", [{ field: "email", msg: "is not a valid email address" }]),
    });
    renderAt("/demo-form");
    await screen.findByRole("option", { name: "CRM integration" });
    await fill();
    await userEvent.click(screen.getByRole("button", { name: "Send request" }));
    expect(await screen.findByText("is not a valid email address")).toBeInTheDocument();
    expect(screen.getByRole("alert")).toHaveTextContent("Please check the highlighted fields.");
    expect(screen.getByLabelText(/Work email/)).toHaveAttribute("aria-invalid", "true");
  });

  it("keeps the honeypot out of reach of people and screen readers", async () => {
    mockApi({ "GET /api/v1/public/form-config": config });
    const { container } = renderAt("/demo-form");
    await screen.findByRole("option", { name: "CRM integration" });
    const honeypot = container.querySelector(".hp");
    expect(honeypot).toHaveAttribute("aria-hidden", "true");
    expect(within(honeypot as HTMLElement).getByRole("textbox", { hidden: true })).toHaveAttribute("tabindex", "-1");
  });
});

describe("leads", () => {
  it("lists leads with the AI recommendation and filters by status", async () => {
    const calls = mockApi({
      ...signedInAs(operatorMe),
      "GET /api/v1/leads": page([makeLead(), makeLead({ id: "lead-2", reference: "L-1002", first_name: "Sam", decision: null, score: null, status: "new" })]),
    });
    renderAt("/leads?status=needs_review");
    expect(await screen.findByRole("link", { name: "Maya Chen" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Sam Chen" })).toBeInTheDocument();
    expect(calls.find((c) => c.key === "GET /api/v1/leads")?.url.searchParams.get("status")).toBe("needs_review");
  });

  it("renders the lead message as text, never as HTML", async () => {
    mockApi({ ...signedInAs(operatorMe), "GET /api/v1/leads/lead-1": makeDetail(), "GET /api/v1/leads/lead-1/history": [] });
    renderAt("/leads/lead-1");
    const message = await screen.findByTestId("lead-message");
    expect(message).toHaveTextContent("<b>not html</b>");
    expect(message.querySelector("b")).toBeNull();
  });

  it("shows the rules score, grounded evidence and model summary", async () => {
    mockApi({ ...signedInAs(operatorMe), "GET /api/v1/leads/lead-1": makeDetail(), "GET /api/v1/leads/lead-1/history": [] });
    renderAt("/leads/lead-1");
    expect(await screen.findByLabelText("Rules score")).toHaveTextContent("85");
    expect(screen.getByText("“sync our web forms into HubSpot”")).toBeInTheDocument();
    expect(screen.getByText("Clear CRM integration project with a deadline.")).toBeInTheDocument();
    expect(screen.getByText(/All checks passed/)).toBeInTheDocument();
    expect(screen.getByText("replayed 2×, ignored")).toBeInTheDocument();
  });

  it("records an override with a reason", async () => {
    const calls = mockApi({
      ...signedInAs(operatorMe),
      "GET /api/v1/leads/lead-1": makeDetail(),
      "GET /api/v1/leads/lead-1/history": [],
      "POST /api/v1/leads/lead-1/override": makeDetail(),
    });
    renderAt("/leads/lead-1");
    await screen.findByLabelText("Rules score");
    await userEvent.selectOptions(screen.getByLabelText("New decision"), "needs_review");
    await userEvent.type(screen.getByLabelText("Reason"), "Budget unclear");
    await userEvent.click(screen.getByRole("button", { name: "Record decision" }));
    await screen.findByText("Decision recorded.");
    const body = JSON.parse(String(calls.find((c) => c.key === "POST /api/v1/leads/lead-1/override")?.init?.body));
    expect(body).toEqual({ decision: "needs_review", reason: "Budget unclear" });
  });
});

describe("follow-up approval", () => {
  it("lets an operator edit, then approve; approve waits until edits are saved", async () => {
    const calls = mockApi({
      ...signedInAs(operatorMe),
      "GET /api/v1/leads/lead-1": makeDetail(),
      "GET /api/v1/leads/lead-1/history": [],
      "PATCH /api/v1/follow-ups/draft-1": makeDraft({ subject: "Edited subject" }),
      "POST /api/v1/follow-ups/draft-1/approve": makeDraft({ status: "approved" }),
    });
    renderAt("/leads/lead-1");
    const subject = await screen.findByLabelText("Subject");
    await userEvent.clear(subject);
    await userEvent.type(subject, "Edited subject");
    expect(screen.getByRole("button", { name: "Approve" })).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "Save edits" }));
    await screen.findByText("Draft saved.");
    expect(calls.some((c) => c.key === "PATCH /api/v1/follow-ups/draft-1")).toBe(true);
  });

  it("rejects without sending anything", async () => {
    const calls = mockApi({
      ...signedInAs(operatorMe),
      "GET /api/v1/leads/lead-1": makeDetail(),
      "GET /api/v1/leads/lead-1/history": [],
      "POST /api/v1/follow-ups/draft-1/reject": makeDraft({ status: "rejected" }),
    });
    renderAt("/leads/lead-1");
    await userEvent.type(await screen.findByLabelText("Decision note"), "Wrong tone");
    await userEvent.click(screen.getByRole("button", { name: "Reject" }));
    expect(await screen.findByText("Rejected. Nothing will be sent.")).toBeInTheDocument();
    const body = JSON.parse(String(calls.find((c) => c.key.endsWith("/reject"))?.init?.body));
    expect(body).toEqual({ note: "Wrong tone" });
  });

  it("shows a viewer the draft read-only without decision buttons", async () => {
    mockApi({ ...signedInAs(viewerMe), "GET /api/v1/leads/lead-1": makeDetail(), "GET /api/v1/leads/lead-1/history": [] });
    renderAt("/leads/lead-1");
    expect(await screen.findByLabelText("Subject")).toHaveAttribute("readonly");
    expect(screen.queryByRole("button", { name: "Approve" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Record decision" })).toBeNull();
  });
});

describe("duplicate review", () => {
  it("resolves a possible duplicate as different people", async () => {
    const item = {
      id: "dup-1",
      kind: "possible_same_person",
      lead_id: "lead-2",
      candidate_lead_id: "lead-1",
      reasons: ["same_name", "same_company_domain"],
      proposed: {},
      status: "open",
      note: null,
      resolved_at: null,
      created_at: "2026-11-19T10:02:00Z",
      lead: makeLead({ id: "lead-2", email: "m.chen@harborview-logistics.example" }),
      candidate: makeLead(),
    };
    let resolved = false;
    const calls = mockApi({
      ...signedInAs(operatorMe),
      "GET /api/v1/duplicates": () => json(resolved ? page([]) : page([item])),
      "POST /api/v1/duplicates/dup-1/resolve": () => {
        resolved = true;
        return json({ ...item, status: "kept_separate" });
      },
    });
    renderAt("/duplicates");
    expect(await screen.findByText("Same name, Same company domain")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Different people" }));
    await waitFor(() => expect(screen.getByText("No open review items.")).toBeInTheDocument());
    const body = JSON.parse(String(calls.find((c) => c.key.endsWith("/resolve"))?.init?.body));
    expect(body.resolution).toBe("kept_separate");
  });
});
