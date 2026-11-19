import { render } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { vi } from "vitest";
import type { Assessment, Draft, LeadDetail, LeadListItem, Me } from "../api";
import { App } from "../App";

export interface Call {
  key: string;
  init: RequestInit | undefined;
  url: URL;
}

type Handler = (init: RequestInit | undefined, url: URL) => Response | Promise<Response>;

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

export function apiError(status: number, code: string, message: string, details: unknown[] = []): Response {
  return json({ error: { code, message, request_id: "test", details } }, status);
}

export function page<T>(items: T[]) {
  return { items, total: items.length, limit: 25, offset: 0 };
}

// Replaces fetch with a table of "METHOD /path" -> handler or JSON body. Unknown routes fail
// loudly so a test never silently depends on an endpoint it did not declare.
export function mockApi(routes: Record<string, Handler | object>) {
  const calls: Call[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = new URL(String(input), "http://localhost");
      const key = `${init?.method ?? "GET"} ${url.pathname}`;
      calls.push({ key, init, url });
      const route = routes[key];
      if (route === undefined) return apiError(404, "not_found", `No mock for ${key}`);
      return typeof route === "function" ? (route as Handler)(init, url) : json(route);
    }),
  );
  return calls;
}

export const tokens = { access_token: "access", refresh_token: "r".repeat(43), expires_in: 900 };
export const operatorMe: Me = { id: "u1", email: "olivia@example.com", full_name: "Olivia Operator", role: "operator" };
export const viewerMe: Me = { ...operatorMe, id: "u2", full_name: "Victor Viewer", role: "viewer" };

export function signedInAs(me: Me) {
  sessionStorage.setItem("lead.refresh", tokens.refresh_token);
  return { "POST /api/v1/auth/refresh": tokens, "GET /api/v1/me": me };
}

export function renderAt(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <App />
    </MemoryRouter>,
  );
}

export function makeLead(overrides: Partial<LeadListItem> = {}): LeadListItem {
  return {
    id: "lead-1",
    reference: "L-1001",
    status: "follow_up_draft_ready",
    first_name: "Maya",
    last_name: "Chen",
    email: "maya.chen@harborview-logistics.example",
    company: "Harborview Logistics",
    interest: "crm_integration",
    source: "website-form",
    decision: "qualified",
    score: 85,
    intent_category: "new_project",
    review_reasons: [],
    created_at: "2026-11-19T10:00:00Z",
    last_event_at: "2026-11-19T10:00:00Z",
    ...overrides,
  };
}

export function makeAssessment(overrides: Partial<Assessment> = {}): Assessment {
  return {
    id: "a1",
    provider: "ollama",
    model: "llama3.1:8b",
    status: "valid",
    error: null,
    parsed: {
      intent_category: "new_project",
      service_fit: "strong",
      requested_services: ["crm_integration"],
      urgency: "high",
      evidence: ["sync our web forms into HubSpot"],
      reasoning_summary: "Clear CRM integration project with a deadline.",
      recommended_next_action: "book_discovery_call",
      missing_information: ["budget"],
      needs_human_review: false,
    },
    rules: { score: 85, decision: "qualified", reasons: [], evidence_found: ["sync our web forms into HubSpot"], evidence_dropped: 0, requested_services: ["crm_integration"] },
    decision: "qualified",
    score: 85,
    latency_ms: 21000,
    superseded: false,
    override_decision: null,
    override_reason: null,
    overridden_at: null,
    created_at: "2026-11-19T10:00:30Z",
    ...overrides,
  };
}

export function makeDraft(overrides: Partial<Draft> = {}): Draft {
  return {
    id: "draft-1",
    lead_id: "lead-1",
    version: 1,
    subject: "Your CRM integration request",
    body: "Hi Maya,\n\nThanks for reaching out about syncing your forms. Would a short call work?\n\nThe team",
    generated_by: "llm",
    model: "ollama/llama3.1:8b",
    status: "draft",
    edited_at: null,
    decided_at: null,
    decision_note: null,
    delivery_channel: null,
    delivered_at: null,
    delivery_error: null,
    created_at: "2026-11-19T10:01:00Z",
    ...overrides,
  };
}

export function makeDetail(overrides: Partial<LeadDetail> = {}): LeadDetail {
  return {
    ...makeLead(),
    website: "harborview-logistics.example",
    company_domain: "harborview-logistics.example",
    job_title: "Operations Manager",
    country: "Canada",
    message: "We need to sync our web forms into HubSpot by the end of the month. <b>not html</b>",
    consent_contact: true,
    consent_marketing: false,
    retry_stage: null,
    qualified_at: "2026-11-19T10:00:30Z",
    synced_at: "2026-11-19T10:00:40Z",
    events: [
      {
        id: "e1",
        source: "website-form",
        channel: "form",
        outcome: "created",
        correlation_id: "req-1",
        received_at: "2026-11-19T10:00:00Z",
        occurred_at: null,
        replay_count: 2,
        payload: {},
      },
    ],
    assessments: [makeAssessment()],
    duplicates: [],
    crm: {
      provider: "demo",
      status: "synced",
      remote_contact_id: "42",
      remote_company_id: "7",
      attempts: 5,
      last_error_code: null,
      last_error: null,
      synced_at: "2026-11-19T10:00:40Z",
      updated_at: "2026-11-19T10:00:40Z",
    },
    drafts: [makeDraft()],
    workflow_events: [],
    ...overrides,
  };
}
