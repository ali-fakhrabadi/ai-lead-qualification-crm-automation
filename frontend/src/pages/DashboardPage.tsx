import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, type LeadListItem, type Stats } from "../api";
import { Badge, Empty, ErrorNote, formatDate, humanize, Panel } from "../components/ui";

const STAGES = [
  "new",
  "pending_qualification",
  "needs_review",
  "qualified",
  "synced_to_crm",
  "follow_up_draft_ready",
  "follow_up_approved",
  "follow_up_sent",
  "retry_pending",
  "failed",
];

export function DashboardPage() {
  const [stats, setStats] = useState<Stats | null>(null);
  const [attention, setAttention] = useState<LeadListItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.all([api.stats(), api.leads({ needs_attention: true, limit: 8 })])
      .then(([s, page]) => {
        setStats(s);
        setAttention(page.items);
      })
      .catch(() => setError("Could not load the pipeline"));
  }, []);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Pipeline</h1>
          <p className="muted">Where every lead stands, and what needs a person.</p>
        </div>
      </header>
      <ErrorNote error={error} />
      {stats && (
        <>
          <div className="kpis" aria-label="Summary">
            <div className="kpi">
              <span>Leads</span>
              <strong>{stats.leads_total}</strong>
            </div>
            <Link className="kpi" to="/leads?decision=qualified">
              <span>Qualified</span>
              <strong>{stats.by_decision.qualified ?? 0}</strong>
            </Link>
            <Link className="kpi warn" to="/duplicates">
              <span>Duplicates to review</span>
              <strong>{stats.open_duplicates}</strong>
            </Link>
            <Link className="kpi info" to="/follow-ups">
              <span>Drafts to approve</span>
              <strong>{stats.drafts_awaiting_review}</strong>
            </Link>
            <Link className={`kpi ${stats.events_failed ? "bad" : ""}`} to="/workflow">
              <span>Workflow events failed / pending</span>
              <strong>
                {stats.events_failed} / {stats.events_pending}
              </strong>
            </Link>
          </div>
          <Panel title="Leads by stage">
            <ol className="stages">
              {STAGES.map((stage) => (
                <li key={stage}>
                  <Link to={`/leads?status=${stage}`}>
                    <Badge value={stage} />
                    <strong>{stats.by_status[stage] ?? 0}</strong>
                  </Link>
                </li>
              ))}
            </ol>
          </Panel>
        </>
      )}
      <Panel title="Needs attention">
        {attention.length === 0 ? (
          <Empty>Nothing is waiting for a person.</Empty>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Lead</th>
                <th scope="col">Status</th>
                <th scope="col">Why</th>
                <th scope="col">Updated</th>
              </tr>
            </thead>
            <tbody>
              {attention.map((lead) => (
                <tr key={lead.id}>
                  <td>
                    <Link to={`/leads/${lead.id}`}>
                      {lead.reference} · {lead.first_name} {lead.last_name}
                    </Link>
                    <div className="sub">{lead.company}</div>
                  </td>
                  <td>
                    <Badge value={lead.status} />
                  </td>
                  <td className="reasons">{lead.review_reasons.map(humanize).join(", ") || "—"}</td>
                  <td>{formatDate(lead.last_event_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </Panel>
    </div>
  );
}
