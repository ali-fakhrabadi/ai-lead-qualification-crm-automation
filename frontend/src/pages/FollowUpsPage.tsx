import { useEffect, useState } from "react";
import { Link } from "react-router";
import { api, type Draft } from "../api";
import { Badge, Empty, ErrorNote, formatDate } from "../components/ui";

const TABS = ["draft", "approved", "delivered", "rejected"] as const;

export function FollowUpsPage() {
  const [tab, setTab] = useState<(typeof TABS)[number]>("draft");
  const [drafts, setDrafts] = useState<Draft[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setDrafts(null);
    api
      .drafts(tab)
      .then((page) => setDrafts(page.items))
      .catch(() => setError("Could not load follow-ups"));
  }, [tab]);

  return (
    <div className="page">
      <header className="page-head">
        <div>
          <h1>Follow-up approvals</h1>
          <p className="muted">Drafts are never sent until a person approves them. Delivery in this demo goes to a local mail sink.</p>
        </div>
      </header>
      <div className="tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t} type="button" role="tab" aria-selected={tab === t} className={tab === t ? "active" : undefined} onClick={() => setTab(t)}>
            {t === "draft" ? "Waiting for review" : t[0]!.toUpperCase() + t.slice(1)}
          </button>
        ))}
      </div>
      <ErrorNote error={error} />
      {drafts && drafts.length === 0 && <Empty>Nothing here.</Empty>}
      <ul className="draft-list">
        {drafts?.map((d) => (
          <li key={d.id} className="card">
            <div className="review-head">
              <Link to={`/leads/${d.lead_id}`}>{d.subject}</Link>
              <Badge value={d.status} />
            </div>
            <p className="excerpt">{d.body.slice(0, 220)}{d.body.length > 220 ? "…" : ""}</p>
            <div className="sub">
              {d.generated_by === "llm" ? `Written by ${d.model}` : "Template"} · created {formatDate(d.created_at)}
              {d.delivered_at && ` · delivered ${formatDate(d.delivered_at)}`}
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}
