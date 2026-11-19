import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router";
import { api, ApiError, type DuplicateWithLeads, type LeadListItem } from "../api";
import { useSession } from "../App";
import { Badge, Empty, ErrorNote, formatDate, humanize } from "../components/ui";

function LeadCard({ lead, title }: { lead: LeadListItem; title: string }) {
  return (
    <div className="mini-card">
      <span className="label">{title}</span>
      <Link to={`/leads/${lead.id}`}>
        {lead.first_name} {lead.last_name}
      </Link>
      <div>{lead.email}</div>
      <div className="sub">
        {lead.company ?? "—"} · {lead.reference} · {formatDate(lead.created_at)}
      </div>
      <Badge value={lead.status} />
    </div>
  );
}

export function DuplicatesPage() {
  const { can } = useSession();
  const [items, setItems] = useState<DuplicateWithLeads[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notes, setNotes] = useState<Record<string, string>>({});

  const load = useCallback(() => {
    api
      .duplicates()
      .then((page) => setItems(page.items))
      .catch(() => setError("Could not load the review queue"));
  }, []);
  useEffect(load, [load]);

  async function resolve(id: string, resolution: string) {
    try {
      await api.resolveDuplicate(id, resolution, notes[id] ?? "");
      load();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not resolve");
    }
  }

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Duplicate review</h1>
          <p className="muted">
            Possible duplicates are never merged automatically. Same-email conflicts keep the stored values until you apply the proposed ones.
          </p>
        </div>
      </header>
      <ErrorNote error={error} />
      {items && items.length === 0 && <Empty>No open review items.</Empty>}
      <ul className="review-list">
        {items?.map((item) => (
          <li key={item.id} className="card review">
            <div className="review-head">
              <Badge value="open" label={humanize(item.kind)} />
              <span className="sub">{item.reasons.map(humanize).join(", ")}</span>
            </div>
            <div className="review-body">
              <LeadCard lead={item.lead} title="Lead" />
              {item.candidate ? (
                <LeadCard lead={item.candidate} title="Possibly the same person" />
              ) : (
                <div className="mini-card">
                  <span className="label">New submission proposes</span>
                  <ul className="plain">
                    {Object.entries(item.proposed).map(([k, v]) => (
                      <li key={k}>
                        {humanize(k)}: <strong>{v}</strong>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
            </div>
            {can("operator") && (
              <div className="actions">
                <input
                  aria-label="Note"
                  placeholder="Note (optional)"
                  value={notes[item.id] ?? ""}
                  onChange={(e) => setNotes((n) => ({ ...n, [item.id]: e.target.value }))}
                />
                {item.kind === "possible_same_person" ? (
                  <>
                    <button type="button" onClick={() => void resolve(item.id, "kept_separate")}>
                      Different people
                    </button>
                    <button type="button" onClick={() => void resolve(item.id, "same_person")}>
                      Same person (link)
                    </button>
                  </>
                ) : (
                  <button type="button" className="primary" onClick={() => void resolve(item.id, "update_applied")}>
                    Apply proposed values
                  </button>
                )}
                <button type="button" onClick={() => void resolve(item.id, "dismissed")}>
                  Dismiss
                </button>
              </div>
            )}
          </li>
        ))}
      </ul>
    </div>
  );
}
