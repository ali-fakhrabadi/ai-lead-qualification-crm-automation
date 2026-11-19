import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Link, useParams } from "react-router";
import { api, ApiError, type AuditEvent, type Draft, type LeadDetail } from "../api";
import { useSession } from "../App";
import { Badge, duration, Empty, ErrorNote, Facts, formatDate, humanize, Panel } from "../components/ui";

function message(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : fallback;
}

export function LeadDetailPage() {
  const { leadId = "" } = useParams();
  const { can } = useSession();
  const [lead, setLead] = useState<LeadDetail | null>(null);
  const [history, setHistory] = useState<AuditEvent[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const load = useCallback(() => {
    Promise.all([api.lead(leadId), api.history(leadId)])
      .then(([detail, events]) => {
        setLead(detail);
        setHistory(events);
        setError(null);
      })
      .catch((err: unknown) => setError(message(err, "Could not load the lead")));
  }, [leadId]);

  useEffect(load, [load]);

  async function act(action: () => Promise<unknown>, done: string) {
    setNotice(null);
    try {
      await action();
      setNotice(done);
      load();
    } catch (err) {
      setError(message(err, "The action failed"));
    }
  }

  if (!lead) {
    return (
      <div className="page">
        <ErrorNote error={error} />
        {!error && <p role="status">Loading…</p>}
      </div>
    );
  }

  const assessment = lead.assessments.find((a) => !a.superseded) ?? lead.assessments[0] ?? null;
  const draft = lead.drafts[0] ?? null;
  const operator = can("operator");

  return (
    <div className="page">
      <nav className="crumbs" aria-label="Breadcrumb">
        <Link to="/leads">Leads</Link> / {lead.reference}
      </nav>
      <header className="page-head">
        <div>
          <h1>
            {lead.first_name} {lead.last_name}
          </h1>
          <p className="muted">
            {lead.reference} · {lead.company ?? "No company"} · received {formatDate(lead.created_at)} via {lead.source}
          </p>
        </div>
        <div className="head-badges">
          <Badge value={lead.status} />
          {lead.retry_stage && <span className="tag">stage: {humanize(lead.retry_stage)}</span>}
          {operator && ["retry_pending", "failed"].includes(lead.status) && (
            <button type="button" onClick={() => void act(() => api.retry(lead.id), "Retry queued.")}>
              Retry now
            </button>
          )}
        </div>
      </header>
      <ErrorNote error={error} />
      {notice && (
        <div className="alert success" role="status">
          {notice}
        </div>
      )}
      {lead.review_reasons.length > 0 && (
        <div className="alert warn">
          <strong>Review reasons:</strong> {lead.review_reasons.map(humanize).join(", ")}
        </div>
      )}

      <div className="detail-grid">
        <div className="col">
          <Panel title="Inquiry">
            <Facts
              rows={[
                ["Email", lead.email],
                ["Company", lead.company],
                ["Website", lead.website],
                ["Job title", lead.job_title],
                ["Country", lead.country],
                ["Interest", humanize(lead.interest)],
                ["Consent", `contact: ${lead.consent_contact ? "yes" : "no"} · updates: ${lead.consent_marketing ? "yes" : "no"}`],
              ]}
            />
            <h3 className="label">Message (as submitted, untrusted text)</h3>
            <p className="message" data-testid="lead-message">
              {lead.message}
            </p>
          </Panel>

          <Panel title="AI qualification">
            {!assessment ? (
              <Empty>Not qualified yet.</Empty>
            ) : (
              <QualificationView lead={lead} operator={operator} onAct={act} />
            )}
          </Panel>

          <Panel title="Follow-up draft">
            {!draft ? (
              <Empty>No draft yet. Drafts are created after the CRM sync of a qualified lead.</Empty>
            ) : (
              <DraftView key={draft.id + draft.status} draft={draft} operator={operator} onAct={act} />
            )}
          </Panel>
        </div>

        <div className="col">
          <Panel title="CRM sync">
            {!lead.crm ? (
              <Empty>Not synced. Only qualified leads are sent to the CRM.</Empty>
            ) : (
              <Facts
                rows={[
                  ["Provider", lead.crm.provider],
                  ["Status", <Badge key="s" value={lead.crm.status} />],
                  ["Contact id", lead.crm.remote_contact_id && <code>{lead.crm.remote_contact_id}</code>],
                  ["Company id", lead.crm.remote_company_id && <code>{lead.crm.remote_company_id}</code>],
                  ["HTTP attempts", lead.crm.attempts],
                  ["Last error", lead.crm.last_error_code ? `${lead.crm.last_error_code}: ${lead.crm.last_error}` : "—"],
                  ["Synced", formatDate(lead.crm.synced_at)],
                ]}
              />
            )}
          </Panel>

          <Panel title="Duplicates">
            {lead.duplicates.length === 0 ? (
              <Empty>No duplicate signals.</Empty>
            ) : (
              <ul className="list">
                {lead.duplicates.map((d) => (
                  <li key={d.id}>
                    <Badge value={d.status} /> {humanize(d.kind)}: {d.reasons.map(humanize).join(", ")}
                    {d.candidate_lead_id && (
                      <>
                        {" "}
                        ·{" "}
                        <Link to={`/leads/${d.candidate_lead_id === lead.id ? d.lead_id : d.candidate_lead_id}`}>other lead</Link>
                      </>
                    )}
                    {Object.keys(d.proposed).length > 0 && (
                      <div className="sub">
                        Proposed: {Object.entries(d.proposed).map(([k, v]) => `${humanize(k)} → ${v}`).join("; ")}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            )}
            {lead.duplicates.some((d) => d.status === "open") && <Link to="/duplicates">Open the review queue →</Link>}
          </Panel>

          <Panel title="Submissions">
            <ul className="list">
              {lead.events.map((e) => (
                <li key={e.id}>
                  <Badge value={e.outcome === "created" ? "new" : e.outcome} label={humanize(e.outcome)} /> {e.channel} from{" "}
                  {e.source} · {formatDate(e.received_at)}
                  {e.replay_count > 0 && <span className="tag">replayed {e.replay_count}×, ignored</span>}
                  <div className="sub">correlation {e.correlation_id}</div>
                </li>
              ))}
            </ul>
          </Panel>

          <Panel title="Workflow runs">
            {lead.workflow_events.length === 0 ? (
              <Empty>No workflow events.</Empty>
            ) : (
              <table className="table compact">
                <thead>
                  <tr>
                    <th scope="col">Event</th>
                    <th scope="col">Status</th>
                    <th scope="col">n8n run</th>
                    <th scope="col">Took</th>
                  </tr>
                </thead>
                <tbody>
                  {lead.workflow_events.map((w) => (
                    <tr key={w.id}>
                      <td>
                        {w.event_type}
                        <div className="sub">attempts {w.attempts}</div>
                      </td>
                      <td>
                        <Badge value={w.status} />
                        {w.last_error && <div className="sub">{w.last_error}</div>}
                      </td>
                      <td>{w.workflow_execution_id ? `#${w.workflow_execution_id}` : "—"}</td>
                      <td>{duration(w.workflow_duration_ms)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}
          </Panel>

          <Panel title="History">
            <ol className="timeline">
              {history.map((h) => (
                <li key={h.id} className={h.outcome !== "success" ? "failed" : undefined}>
                  <span className="when">{formatDate(h.ts)}</span>
                  <span className="what">{humanize(h.action)}</span>
                  <span className="who">{h.actor_email ?? h.actor_type}</span>
                </li>
              ))}
            </ol>
          </Panel>
        </div>
      </div>
    </div>
  );
}

function QualificationView({
  lead,
  operator,
  onAct,
}: {
  lead: LeadDetail;
  operator: boolean;
  onAct: (action: () => Promise<unknown>, done: string) => Promise<void>;
}) {
  const assessment = lead.assessments.find((a) => !a.superseded) ?? lead.assessments[0]!;
  const parsed = assessment.parsed ?? {};
  const rules = assessment.rules ?? {};
  const [decision, setDecision] = useState("qualified");
  const [reason, setReason] = useState("");

  function override(event: FormEvent) {
    event.preventDefault();
    void onAct(() => api.override(lead.id, decision, reason), "Decision recorded.").then(() => setReason(""));
  }

  return (
    <div className="qualification">
      <div className="score-row">
        <div className="score-box" aria-label="Rules score">
          <strong>{assessment.score ?? "—"}</strong>
          <span>/ 100</span>
        </div>
        <div>
          <div>
            Recommendation: <Badge value={assessment.decision} />{" "}
            {assessment.override_decision && (
              <>
                → overridden: <Badge value={assessment.override_decision} />
              </>
            )}
          </div>
          <div className="sub">
            {assessment.provider}/{assessment.model} · {duration(assessment.latency_ms)} · output <Badge value={assessment.status} />
          </div>
        </div>
      </div>
      <Facts
        rows={[
          ["Intent", humanize(parsed.intent_category)],
          ["Service fit", humanize(parsed.service_fit)],
          ["Urgency", humanize(parsed.urgency)],
          ["Services", (rules.requested_services ?? []).map(humanize).join(", ") || "—"],
          ["Missing", (parsed.missing_information ?? []).map(humanize).join(", ") || "—"],
          ["Next action", humanize(parsed.recommended_next_action)],
        ]}
      />
      {parsed.reasoning_summary && (
        <>
          <h3 className="label">Model summary (advisory)</h3>
          <p className="summary">{parsed.reasoning_summary}</p>
        </>
      )}
      {(rules.evidence_found ?? []).length > 0 && (
        <>
          <h3 className="label">Evidence found in the message</h3>
          <ul className="quotes">
            {(rules.evidence_found ?? []).map((q) => (
              <li key={q}>“{q}”</li>
            ))}
          </ul>
        </>
      )}
      <h3 className="label">Rules applied</h3>
      <p className="sub">
        {(rules.reasons ?? []).length ? (rules.reasons ?? []).map(humanize).join(", ") : "All checks passed: qualified by the rules."}
        {rules.evidence_dropped ? ` · ${rules.evidence_dropped} quote(s) not found in the message were ignored` : ""}
      </p>
      {assessment.error && <p className="sub">Model output problem: {assessment.error}</p>}
      {assessment.override_reason && <p className="sub">Override reason: {assessment.override_reason}</p>}
      {operator && (
        <form className="inline-form" onSubmit={override} aria-label="Override decision">
          <select aria-label="New decision" value={decision} onChange={(e) => setDecision(e.target.value)}>
            <option value="qualified">Qualify</option>
            <option value="needs_review">Needs review</option>
            <option value="unqualified">Unqualified</option>
          </select>
          <input aria-label="Reason" placeholder="Reason (required)" required minLength={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          <button type="submit">Record decision</button>
        </form>
      )}
    </div>
  );
}

function DraftView({
  draft,
  operator,
  onAct,
}: {
  draft: Draft;
  operator: boolean;
  onAct: (action: () => Promise<unknown>, done: string) => Promise<void>;
}) {
  const [subject, setSubject] = useState(draft.subject);
  const [body, setBody] = useState(draft.body);
  const [note, setNote] = useState("");
  const editable = operator && draft.status === "draft";
  const changed = subject !== draft.subject || body !== draft.body;

  return (
    <div className="draft">
      <p className="sub">
        Version {draft.version} · {draft.generated_by === "llm" ? `written by ${draft.model}` : "template"} · <Badge value={draft.status} />
        {draft.delivered_at && ` · delivered to the ${draft.delivery_channel} at ${formatDate(draft.delivered_at)}`}
      </p>
      {/* Labels point at their fields (not wrapping them), so the accessible name is just
          "Subject" / "Message" and not the field's own text. */}
      <label htmlFor={`draft-subject-${draft.id}`}>Subject</label>
      <input
        id={`draft-subject-${draft.id}`}
        value={subject}
        readOnly={!editable}
        onChange={(e) => setSubject(e.target.value)}
      />
      <label htmlFor={`draft-body-${draft.id}`}>Message</label>
      <textarea
        id={`draft-body-${draft.id}`}
        rows={9}
        value={body}
        readOnly={!editable}
        onChange={(e) => setBody(e.target.value)}
      />
      {draft.decision_note && <p className="sub">Note: {draft.decision_note}</p>}
      {editable && (
        <div className="actions">
          <button type="button" disabled={!changed} onClick={() => void onAct(() => api.editDraft(draft.id, subject, body), "Draft saved.")}>
            Save edits
          </button>
          <input aria-label="Decision note" placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} />
          <button
            type="button"
            className="primary"
            disabled={changed}
            title={changed ? "Save your edits first" : undefined}
            onClick={() => void onAct(() => api.approveDraft(draft.id, note), "Approved. It will be delivered to the configured channel.")}
          >
            Approve
          </button>
          <button type="button" className="danger" onClick={() => void onAct(() => api.rejectDraft(draft.id, note), "Rejected. Nothing will be sent.")}>
            Reject
          </button>
        </div>
      )}
    </div>
  );
}
