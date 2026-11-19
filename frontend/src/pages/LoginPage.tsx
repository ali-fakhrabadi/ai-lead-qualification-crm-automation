import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { ApiError } from "../api";
import { useSession } from "../App";
import { Logo } from "../components/Shell";

export function LoginPage() {
  const { me, signIn } = useSession();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (me) return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await signIn(email, password);
      navigate("/");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="login">
      <div className="login-panel">
        <div className="login-brand">
          <Logo />
          <strong>Lead Automation</strong>
        </div>
        <h1>Sign in</h1>
        <p className="muted">Review qualified leads, duplicates and follow-ups before anything is sent.</p>
        <form onSubmit={(e) => void submit(e)} className="stack" aria-label="Sign in">
          {error && (
            <div className="alert error" role="alert">
              {error}
            </div>
          )}
          <label>
            Email
            <input type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)} />
          </label>
          <label>
            Password
            <input
              type="password"
              autoComplete="current-password"
              required
              value={password}
              onChange={(e) => setPassword(e.target.value)}
            />
          </label>
          <button type="submit" className="primary" disabled={busy}>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
      <aside className="login-aside" aria-hidden="true">
        <h2>Every lead, once, with a person in control</h2>
        <ul>
          <li>Form and signed webhooks, validated and de-duplicated</li>
          <li>AI qualification checked by transparent rules</li>
          <li>CRM sync and follow-up drafts you approve</li>
        </ul>
      </aside>
    </div>
  );
}
