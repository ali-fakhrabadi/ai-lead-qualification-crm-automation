import type { ReactNode } from "react";
import { NavLink } from "react-router";
import { useSession } from "../App";

export function Logo() {
  return (
    <svg className="logo" viewBox="0 0 64 64" aria-hidden="true">
      <rect width="64" height="64" rx="14" fill="#0D1117" />
      <path
        d="M14 18h36l-14 16v12l-8 4V34z"
        fill="none"
        stroke="#3FB950"
        strokeWidth="4"
        strokeLinejoin="round"
      />
      <circle cx="46" cy="44" r="6" fill="#38BDF8" />
    </svg>
  );
}

const ROLE_LABEL = { admin: "Admin", operator: "Operator", viewer: "Viewer" } as const;

export function Shell({ children }: { children: ReactNode }) {
  const { me, signOut } = useSession();
  return (
    <div className="shell">
      <aside className="sidebar" aria-label="Main navigation">
        <div className="brand">
          <Logo />
          <div>
            <strong>Lead Automation</strong>
            <span>Northwind Automation Studio</span>
          </div>
        </div>
        <nav>
          <NavLink to="/" end>
            Pipeline
          </NavLink>
          <NavLink to="/leads">Leads</NavLink>
          <NavLink to="/duplicates">Duplicate review</NavLink>
          <NavLink to="/follow-ups">Follow-up approvals</NavLink>
          <NavLink to="/workflow">Workflow runs</NavLink>
          <NavLink to="/audit">Audit log</NavLink>
        </nav>
        <a className="form-link" href="/demo-form" target="_blank" rel="noreferrer">
          Open the demo lead form ↗
        </a>
        <div className="sidebar-footer">
          <div className="who">
            <span className="avatar" aria-hidden="true">
              {(me?.full_name || me?.email || "?").slice(0, 1).toUpperCase()}
            </span>
            <div>
              <div className="who-name">{me?.full_name || me?.email}</div>
              <div className="who-role">{me ? ROLE_LABEL[me.role] : ""}</div>
            </div>
          </div>
          <button type="button" className="link-button" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </aside>
      <main className="content">{children}</main>
    </div>
  );
}
