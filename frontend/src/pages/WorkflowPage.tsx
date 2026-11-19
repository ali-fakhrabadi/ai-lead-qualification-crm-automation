import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, type WorkflowEvent } from "../api";
import { Badge, duration, Empty, ErrorNote, formatDate } from "../components/ui";

const FILTERS = ["", "pending", "dispatched", "completed", "failed", "dead"];

export function WorkflowPage() {
  const [status, setStatus] = useState("");
  const [events, setEvents] = useState<WorkflowEvent[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api
      .workflowEvents(status)
      .then((page) => setEvents(page.items))
      .catch(() => setError("Could not load workflow events"));
  }, [status]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Workflow runs</h1>
          <p className="muted">
            Outbox events delivered to n8n (at least once, with backoff) and the result each n8n execution reported.
          </p>
        </div>
        <select aria-label="Status" value={status} onChange={(e) => setStatus(e.target.value)}>
          {FILTERS.map((f) => (
            <option key={f} value={f}>
              {f ? f[0]!.toUpperCase() + f.slice(1) : "All events"}
            </option>
          ))}
        </select>
      </header>
      <ErrorNote error={error} />
      {events && events.length === 0 && <Empty>No events.</Empty>}
      {events && events.length > 0 && (
        <div className="table-wrap card">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Event</th>
                <th scope="col">Status</th>
                <th scope="col">Attempts</th>
                <th scope="col">n8n execution</th>
                <th scope="col">Duration</th>
                <th scope="col">Created</th>
                <th scope="col">Completed</th>
              </tr>
            </thead>
            <tbody>
              {events.map((e) => (
                <tr key={e.id}>
                  <td>
                    {e.lead_id ? <Link to={`/leads/${e.lead_id}`}>{e.event_type}</Link> : e.event_type}
                    <div className="sub">{e.correlation_id}</div>
                  </td>
                  <td>
                    <Badge value={e.status} />
                    {e.last_error && <div className="sub">{e.last_error}</div>}
                  </td>
                  <td>{e.attempts}</td>
                  <td>
                    {e.workflow_execution_id ? `#${e.workflow_execution_id}` : "—"}
                    {e.workflow_status && <div className="sub">{e.workflow_status}</div>}
                  </td>
                  <td>{duration(e.workflow_duration_ms)}</td>
                  <td>{formatDate(e.created_at)}</td>
                  <td>{formatDate(e.completed_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
