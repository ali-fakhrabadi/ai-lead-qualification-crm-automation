import { useEffect, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router";
import { api, type LeadListItem, type Page } from "../api";
import { Badge, Empty, ErrorNote, formatDate, humanize } from "../components/ui";

const STATUSES = [
  "new",
  "pending_qualification",
  "qualified",
  "needs_review",
  "unqualified",
  "synced_to_crm",
  "follow_up_draft_ready",
  "follow_up_approved",
  "follow_up_rejected",
  "follow_up_sent",
  "closed",
  "retry_pending",
  "failed",
];
const LIMIT = 25;

export function LeadsPage() {
  const [params, setParams] = useSearchParams();
  const [page, setPage] = useState<Page<LeadListItem> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [q, setQ] = useState(params.get("q") ?? "");
  const status = params.get("status") ?? "";
  const decision = params.get("decision") ?? "";
  const offset = Number(params.get("offset") ?? 0);

  useEffect(() => {
    setError(null);
    api
      .leads({ status, decision, q: params.get("q") ?? "", limit: LIMIT, offset })
      .then(setPage)
      .catch(() => setError("Could not load leads"));
  }, [status, decision, offset, params]);

  function update(changes: Record<string, string>) {
    const next = new URLSearchParams(params);
    for (const [key, value] of Object.entries(changes)) {
      if (value) next.set(key, value);
      else next.delete(key);
    }
    if (!("offset" in changes)) next.delete("offset");
    setParams(next);
  }

  function search(event: FormEvent) {
    event.preventDefault();
    update({ q: q.trim() });
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Leads</h1>
          <p className="muted">{page ? `${page.total} leads` : "Loading…"}</p>
        </div>
      </header>
      <form className="filters" onSubmit={search} role="search" aria-label="Filter leads">
        <input type="search" placeholder="Name, email, company or L-number" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search" />
        <select aria-label="Status" value={status} onChange={(e) => update({ status: e.target.value })}>
          <option value="">All statuses</option>
          {STATUSES.map((s) => (
            <option key={s} value={s}>
              {humanize(s)}
            </option>
          ))}
        </select>
        <select aria-label="Decision" value={decision} onChange={(e) => update({ decision: e.target.value })}>
          <option value="">Any decision</option>
          <option value="qualified">Qualified</option>
          <option value="needs_review">Needs review</option>
          <option value="unqualified">Unqualified</option>
        </select>
        <button type="submit">Search</button>
      </form>
      <ErrorNote error={error} />
      {page && page.items.length === 0 && <Empty>No leads match these filters.</Empty>}
      {page && page.items.length > 0 && (
        <div className="table-wrap card">
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Lead</th>
                <th scope="col">Company</th>
                <th scope="col">Interest</th>
                <th scope="col">Status</th>
                <th scope="col">AI recommendation</th>
                <th scope="col">Source</th>
                <th scope="col">Received</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((lead) => (
                <tr key={lead.id}>
                  <td>
                    <Link to={`/leads/${lead.id}`}>
                      {lead.first_name} {lead.last_name}
                    </Link>
                    <div className="sub">
                      {lead.reference} · {lead.email}
                    </div>
                  </td>
                  <td>{lead.company ?? "—"}</td>
                  <td>{humanize(lead.interest)}</td>
                  <td>
                    <Badge value={lead.status} />
                  </td>
                  <td>
                    {lead.decision ? (
                      <>
                        <Badge value={lead.decision} /> <span className="score">{lead.score ?? "—"}</span>
                        <div className="sub">{humanize(lead.intent_category)}</div>
                      </>
                    ) : (
                      "—"
                    )}
                  </td>
                  <td>{lead.source}</td>
                  <td>{formatDate(lead.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {page && page.total > LIMIT && (
        <nav className="pager" aria-label="Pages">
          <button type="button" disabled={offset === 0} onClick={() => update({ offset: String(Math.max(0, offset - LIMIT)) })}>
            Previous
          </button>
          <span>
            {offset + 1}–{Math.min(offset + LIMIT, page.total)} of {page.total}
          </span>
          <button type="button" disabled={offset + LIMIT >= page.total} onClick={() => update({ offset: String(offset + LIMIT) })}>
            Next
          </button>
        </nav>
      )}
    </div>
  );
}
