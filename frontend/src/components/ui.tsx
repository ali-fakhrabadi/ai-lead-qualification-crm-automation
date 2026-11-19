import type { ReactNode } from "react";

const TONES: Record<string, string> = {
  qualified: "good",
  synced_to_crm: "good",
  synced: "good",
  follow_up_sent: "good",
  delivered: "good",
  completed: "good",
  success: "good",
  approved: "good",
  valid: "good",
  needs_review: "warn",
  follow_up_draft_ready: "info",
  draft: "info",
  open: "warn",
  pending_qualification: "info",
  pending: "info",
  dispatched: "info",
  running: "info",
  new: "neutral",
  retry_pending: "warn",
  failed: "bad",
  dead: "bad",
  failed_permanent: "bad",
  invalid_output: "bad",
  provider_error: "bad",
  rejected: "neutral",
  follow_up_rejected: "neutral",
  unqualified: "neutral",
  closed: "neutral",
};

export function humanize(value: string | null | undefined): string {
  if (!value) return "—";
  return value.replace(/[_.]/g, " ").replace(/^\w/, (c) => c.toUpperCase());
}

export function Badge({ value, label }: { value: string | null | undefined; label?: string }) {
  const tone = TONES[value ?? ""] ?? "neutral";
  return <span className={`badge ${tone}`}>{label ?? humanize(value)}</span>;
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString("en-GB", {
    day: "2-digit",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  });
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${ms} ms`;
}

export function Panel({ title, actions, children }: { title: string; actions?: ReactNode; children: ReactNode }) {
  const id = `panel-${title.toLowerCase().replace(/[^a-z0-9]+/g, "-")}`;
  return (
    <section className="panel" aria-labelledby={id}>
      <header className="panel-head">
        <h2 id={id}>{title}</h2>
        {actions}
      </header>
      {children}
    </section>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>;
}

export function ErrorNote({ error }: { error: string | null }) {
  return error ? (
    <div className="alert error" role="alert">
      {error}
    </div>
  ) : null;
}

export function Facts({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="facts">
      {rows.map(([label, value]) => (
        <div key={label}>
          <dt>{label}</dt>
          <dd>{value ?? "—"}</dd>
        </div>
      ))}
    </dl>
  );
}
