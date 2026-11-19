import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, type AuditEvent } from "../api";
import { Badge, Empty, ErrorNote, formatDate, humanize } from "../components/ui";

function details(event: AuditEvent): string {
  const keep = ["decision", "score", "reason", "resolution", "provider", "code", "attempts", "step", "channel", "source"];
  return Object.entries(event.details)
    .filter(([k, v]) => keep.includes(k) && v !== null && v !== undefined && v !== "")
    .map(([k, v]) => `${k}: ${Array.isArray(v) ? v.join(", ") : String(v)}`)
    .join(" · ");
}

export function AuditPage() {
  const [events, setEvents] = useState<AuditEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.audit().then(setEvents).catch(() => setError("Could not load the audit log"));
  }, []);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Audit log</h1>
          <p className="muted">Qualification results and overrides, CRM syncs, approvals, rejections and access denials. No message text is stored here.</p>
        </div>
      </header>
      <ErrorNote error={error} />
      {events && events.length === 0 && <Empty>No audit events yet.</Empty>}
      {events && events.length > 0 && (
        <div className="table-wrap card">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Time</th>
                <th scope="col">Action</th>
                <th scope="col">Outcome</th>
                <th scope="col">Actor</th>
                <th scope="col">Lead</th>
                <th scope="col">Details</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>{formatDate(e.ts)}</td>
                  <td>{humanize(e.action)}</td>
                  <td>
                    <Badge value={e.outcome === "success" ? "completed" : e.outcome === "denied" ? "failed" : "retry_pending"} label={e.outcome} />
                  </td>
                  <td>{e.actor_email ?? e.actor_type}</td>
                  <td>{e.lead_id ? <Link to={`/leads/${e.lead_id}`}>open</Link> : "—"}</td>
                  <td className="sub">{details(e)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
