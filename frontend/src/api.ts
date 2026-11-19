// Typed client for the backend API. The access token lives in memory only; the refresh token
// is kept in sessionStorage so a reload in the same tab stays signed in. Both are cleared on
// logout. See docs/security.md for the trade-offs.

export type Role = "admin" | "operator" | "viewer";

export interface Me {
  id: string;
  email: string;
  full_name: string;
  role: Role;
}

export interface Page<T> {
  items: T[];
  total: number;
  limit: number;
  offset: number;
}

export interface LeadListItem {
  id: string;
  reference: string;
  status: string;
  first_name: string;
  last_name: string;
  email: string;
  company: string | null;
  interest: string;
  source: string;
  decision: string | null;
  score: number | null;
  intent_category: string | null;
  review_reasons: string[];
  created_at: string;
  last_event_at: string;
}

export interface LeadEvent {
  id: string;
  source: string;
  channel: string;
  outcome: string;
  correlation_id: string;
  received_at: string;
  occurred_at: string | null;
  replay_count: number;
  payload: Record<string, unknown>;
}

export interface Assessment {
  id: string;
  provider: string;
  model: string;
  status: string;
  error: string | null;
  parsed: {
    intent_category?: string;
    service_fit?: string;
    requested_services?: string[];
    urgency?: string;
    evidence?: string[];
    reasoning_summary?: string;
    recommended_next_action?: string;
    missing_information?: string[];
    needs_human_review?: boolean;
  } | null;
  rules: {
    score?: number;
    decision?: string;
    reasons?: string[];
    evidence_found?: string[];
    evidence_dropped?: number;
    requested_services?: string[];
  } | null;
  decision: string | null;
  score: number | null;
  latency_ms: number | null;
  superseded: boolean;
  override_decision: string | null;
  override_reason: string | null;
  overridden_at: string | null;
  created_at: string;
}

export interface Duplicate {
  id: string;
  kind: string;
  lead_id: string;
  candidate_lead_id: string | null;
  reasons: string[];
  proposed: Record<string, string>;
  status: string;
  note: string | null;
  resolved_at: string | null;
  created_at: string;
}

export interface DuplicateWithLeads extends Duplicate {
  lead: LeadListItem;
  candidate: LeadListItem | null;
}

export interface CrmSync {
  provider: string;
  status: string;
  remote_contact_id: string | null;
  remote_company_id: string | null;
  attempts: number;
  last_error_code: string | null;
  last_error: string | null;
  synced_at: string | null;
  updated_at: string;
}

export interface Draft {
  id: string;
  lead_id: string;
  version: number;
  subject: string;
  body: string;
  generated_by: string;
  model: string | null;
  status: string;
  edited_at: string | null;
  decided_at: string | null;
  decision_note: string | null;
  delivery_channel: string | null;
  delivered_at: string | null;
  delivery_error: string | null;
  created_at: string;
}

export interface WorkflowEvent {
  id: string;
  event_type: string;
  lead_id: string | null;
  status: string;
  attempts: number;
  last_error: string | null;
  correlation_id: string;
  created_at: string;
  dispatched_at: string | null;
  completed_at: string | null;
  workflow_execution_id: string | null;
  workflow_status: string | null;
  workflow_duration_ms: number | null;
}

export interface AuditEvent {
  id: number;
  ts: string;
  actor_type: string;
  actor_email: string | null;
  action: string;
  outcome: string;
  lead_id: string | null;
  correlation_id: string | null;
  details: Record<string, unknown>;
}

export interface LeadDetail extends LeadListItem {
  website: string | null;
  company_domain: string | null;
  job_title: string | null;
  country: string | null;
  message: string;
  consent_contact: boolean;
  consent_marketing: boolean;
  retry_stage: string | null;
  qualified_at: string | null;
  synced_at: string | null;
  events: LeadEvent[];
  assessments: Assessment[];
  duplicates: Duplicate[];
  crm: CrmSync | null;
  drafts: Draft[];
  workflow_events: WorkflowEvent[];
}

export interface Stats {
  by_status: Record<string, number>;
  by_decision: Record<string, number>;
  open_duplicates: number;
  drafts_awaiting_review: number;
  events_pending: number;
  events_failed: number;
  leads_total: number;
}

export interface FormConfig {
  business_name: string;
  interests: { value: string; label: string }[];
  required_fields: string[];
  consent_text: string;
  consent_text_version: string;
}

export interface LeadForm {
  first_name: string;
  last_name: string;
  email: string;
  company: string;
  website: string;
  job_title: string;
  country: string;
  interest: string;
  message: string;
  consent_contact: boolean;
  consent_marketing: boolean;
  nickname: string;
}

export interface IntakeResult {
  status: "accepted" | "duplicate";
  outcome: string;
  lead_reference: string;
  correlation_id: string;
  replayed: boolean;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
    public details: { field: string; msg: string }[] = [],
  ) {
    super(message);
  }
}

const REFRESH_KEY = "lead.refresh";
let accessToken: string | null = null;
let onSignedOut: (() => void) | null = null;

export function setSignedOutHandler(handler: () => void): void {
  onSignedOut = handler;
}

export function hasSession(): boolean {
  return accessToken !== null || sessionStorage.getItem(REFRESH_KEY) !== null;
}

function storeTokens(access: string, refresh: string): void {
  accessToken = access;
  sessionStorage.setItem(REFRESH_KEY, refresh);
}

export function clearTokens(): void {
  accessToken = null;
  sessionStorage.removeItem(REFRESH_KEY);
}

async function parseError(response: Response): Promise<ApiError> {
  try {
    const body = (await response.json()) as {
      error?: { code?: string; message?: string; details?: { field: string; msg: string }[] };
    };
    return new ApiError(
      response.status,
      body.error?.code ?? "error",
      body.error?.message ?? response.statusText,
      Array.isArray(body.error?.details) ? body.error.details : [],
    );
  } catch {
    return new ApiError(response.status, "error", response.statusText || "Request failed");
  }
}

let refreshing: Promise<boolean> | null = null;

async function refresh(): Promise<boolean> {
  const token = sessionStorage.getItem(REFRESH_KEY);
  if (!token) return false;
  refreshing ??= (async () => {
    const response = await fetch("/api/v1/auth/refresh", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ refresh_token: token }),
    });
    if (!response.ok) {
      clearTokens();
      return false;
    }
    const body = (await response.json()) as { access_token: string; refresh_token: string };
    storeTokens(body.access_token, body.refresh_token);
    return true;
  })().finally(() => {
    refreshing = null;
  });
  return refreshing;
}

async function request<T>(path: string, init: RequestInit = {}, retry = true): Promise<T> {
  if (accessToken === null && !(await refresh())) {
    onSignedOut?.();
    throw new ApiError(401, "unauthorized", "Please sign in");
  }
  const headers = new Headers(init.headers);
  if (accessToken) headers.set("Authorization", `Bearer ${accessToken}`);
  if (init.body) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...init, headers });
  if (response.status === 401 && retry && (await refresh())) {
    return request<T>(path, init, false);
  }
  if (response.status === 401) {
    clearTokens();
    onSignedOut?.();
  }
  if (!response.ok) throw await parseError(response);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", body: JSON.stringify(body) });
}

export interface LeadFilters {
  status?: string;
  decision?: string;
  q?: string;
  needs_attention?: boolean;
  limit?: number;
  offset?: number;
}

export const api = {
  async login(email: string, password: string): Promise<void> {
    const response = await fetch("/api/v1/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ email, password }),
    });
    if (!response.ok) throw await parseError(response);
    const body = (await response.json()) as { access_token: string; refresh_token: string };
    storeTokens(body.access_token, body.refresh_token);
  },
  async logout(): Promise<void> {
    const token = sessionStorage.getItem(REFRESH_KEY);
    try {
      if (token) await post("/api/v1/auth/logout", { refresh_token: token });
    } finally {
      clearTokens();
    }
  },
  me: () => request<Me>("/api/v1/me"),
  stats: () => request<Stats>("/api/v1/stats"),
  leads: (filters: LeadFilters = {}) => {
    const query = new URLSearchParams({
      limit: String(filters.limit ?? 25),
      offset: String(filters.offset ?? 0),
    });
    if (filters.status) query.set("status", filters.status);
    if (filters.decision) query.set("decision", filters.decision);
    if (filters.q) query.set("q", filters.q);
    if (filters.needs_attention) query.set("needs_attention", "true");
    return request<Page<LeadListItem>>(`/api/v1/leads?${query}`);
  },
  lead: (id: string) => request<LeadDetail>(`/api/v1/leads/${id}`),
  history: (id: string) => request<AuditEvent[]>(`/api/v1/leads/${id}/history`),
  override: (id: string, decision: string, reason: string) =>
    post<LeadDetail>(`/api/v1/leads/${id}/override`, { decision, reason }),
  requalify: (id: string, reason: string) =>
    post<{ status: string }>(`/api/v1/leads/${id}/requalify`, { reason }),
  retry: (id: string) => post<{ status: string }>(`/api/v1/leads/${id}/retry`, {}),
  duplicates: (status = "open") =>
    request<Page<DuplicateWithLeads>>(`/api/v1/duplicates?status=${status}&limit=50`),
  resolveDuplicate: (id: string, resolution: string, note: string) =>
    post<DuplicateWithLeads>(`/api/v1/duplicates/${id}/resolve`, { resolution, note: note || null }),
  drafts: (status = "draft") => request<Page<Draft>>(`/api/v1/follow-ups?status=${status}&limit=50`),
  editDraft: (id: string, subject: string, body: string) =>
    request<Draft>(`/api/v1/follow-ups/${id}`, {
      method: "PATCH",
      body: JSON.stringify({ subject, body }),
    }),
  approveDraft: (id: string, note: string) =>
    post<Draft>(`/api/v1/follow-ups/${id}/approve`, { note: note || null }),
  rejectDraft: (id: string, note: string) =>
    post<Draft>(`/api/v1/follow-ups/${id}/reject`, { note: note || null }),
  workflowEvents: (status = "") =>
    request<Page<WorkflowEvent>>(`/api/v1/workflow-events?limit=50${status ? `&status=${status}` : ""}`),
  audit: () => request<AuditEvent[]>("/api/v1/audit-events?limit=100"),
  formConfig: async (): Promise<FormConfig> => {
    const response = await fetch("/api/v1/public/form-config");
    if (!response.ok) throw await parseError(response);
    return (await response.json()) as FormConfig;
  },
  submitLead: async (form: LeadForm, submissionId: string): Promise<IntakeResult> => {
    const body: Record<string, unknown> = { ...form, submission_id: submissionId };
    for (const key of ["company", "website", "job_title", "country", "nickname"]) {
      if (body[key] === "") body[key] = null;
    }
    const response = await fetch("/api/v1/public/leads", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!response.ok) throw await parseError(response);
    return (await response.json()) as IntakeResult;
  },
};
