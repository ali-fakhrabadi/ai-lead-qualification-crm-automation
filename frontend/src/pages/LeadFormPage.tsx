import { useEffect, useMemo, useState, type FormEvent } from "react";
import { api, ApiError, type FormConfig, type IntakeResult, type LeadForm } from "../api";
import { Logo } from "../components/Shell";
import { newSubmissionId } from "../ids";

const EMPTY: LeadForm = {
  first_name: "",
  last_name: "",
  email: "",
  company: "",
  website: "",
  job_title: "",
  country: "",
  interest: "",
  message: "",
  consent_contact: false,
  consent_marketing: false,
  nickname: "",
};


const LABELS: Record<string, string> = {
  first_name: "First name",
  last_name: "Last name",
  email: "Work email",
  company: "Company",
  website: "Website",
  job_title: "Job title",
  country: "Country",
  interest: "What do you need help with?",
  message: "Tell us about your project",
};

export function LeadFormPage() {
  const [config, setConfig] = useState<FormConfig | null>(null);
  const [form, setForm] = useState<LeadForm>(EMPTY);
  const [errors, setErrors] = useState<Record<string, string>>({});
  const [failure, setFailure] = useState<string | null>(null);
  const [result, setResult] = useState<IntakeResult | null>(null);
  const [busy, setBusy] = useState(false);
  // One id per filled-in form: a double click or a retry is recognised as the same submission.
  const submissionId = useMemo(newSubmissionId, [result]);

  useEffect(() => {
    api.formConfig().then(setConfig).catch(() => setFailure("The form is not available right now."));
  }, []);

  const required = new Set(config?.required_fields ?? []);
  const set = (field: keyof LeadForm, value: string | boolean) => setForm((f) => ({ ...f, [field]: value }));

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setErrors({});
    setFailure(null);
    try {
      setResult(await api.submitLead(form, submissionId));
      setForm(EMPTY);
    } catch (err) {
      if (err instanceof ApiError && err.details.length) {
        setErrors(Object.fromEntries(err.details.map((d) => [d.field.split(".").pop() ?? d.field, d.msg])));
        setFailure("Please check the highlighted fields.");
      } else {
        setFailure(err instanceof ApiError ? err.message : "Sending failed. Please try again.");
      }
    } finally {
      setBusy(false);
    }
  }

  function field(name: keyof LeadForm, props: { type?: string; autoComplete?: string } = {}) {
    const id = `f-${name}`;
    return (
      <label htmlFor={id} className={errors[name] ? "has-error" : undefined}>
        {LABELS[name]}
        {required.has(name) && <span className="req"> *</span>}
        <input
          id={id}
          type={props.type ?? "text"}
          autoComplete={props.autoComplete}
          value={String(form[name])}
          required={required.has(name)}
          aria-invalid={Boolean(errors[name])}
          aria-describedby={errors[name] ? `${id}-error` : undefined}
          onChange={(e) => set(name, e.target.value)}
        />
        {errors[name] && (
          <span id={`${id}-error`} className="field-error">
            {errors[name]}
          </span>
        )}
      </label>
    );
  }

  return (
    <div className="public-page">
      <header className="public-head">
        <Logo />
        <div>
          <strong>{config?.business_name ?? "Northwind Automation Studio"}</strong>
          <span>Demo lead form · synthetic data only</span>
        </div>
      </header>
      <main className="public-main">
        <h1>Tell us what you want to automate</h1>
        <p className="muted">We reply within one business day. A person reviews every reply before it is sent.</p>
        {result && (
          <div className="alert success" role="status">
            <strong>{result.status === "duplicate" ? "We already have this request." : "Thank you, your request was received."}</strong>{" "}
            Reference <code>{result.lead_reference}</code> · tracking id <code>{result.correlation_id}</code>
          </div>
        )}
        {failure && (
          <div className="alert error" role="alert">
            {failure}
          </div>
        )}
        <form className="lead-form" onSubmit={(e) => void submit(e)} aria-label="Lead form" noValidate>
          <div className="grid-2">
            {field("first_name", { autoComplete: "given-name" })}
            {field("last_name", { autoComplete: "family-name" })}
            {field("email", { type: "email", autoComplete: "email" })}
            {field("company", { autoComplete: "organization" })}
            {field("website", { autoComplete: "url" })}
            {field("job_title", { autoComplete: "organization-title" })}
          </div>
          <label htmlFor="f-interest" className={errors.interest ? "has-error" : undefined}>
            {LABELS.interest}
            <span className="req"> *</span>
            <select id="f-interest" required value={form.interest} onChange={(e) => set("interest", e.target.value)}>
              <option value="">Choose one…</option>
              {config?.interests.map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
            </select>
            {errors.interest && <span className="field-error">{errors.interest}</span>}
          </label>
          <label htmlFor="f-message" className={errors.message ? "has-error" : undefined}>
            {LABELS.message}
            <span className="req"> *</span>
            <textarea
              id="f-message"
              rows={6}
              required
              minLength={20}
              maxLength={4000}
              value={form.message}
              aria-invalid={Boolean(errors.message)}
              onChange={(e) => set("message", e.target.value)}
            />
            {errors.message && <span className="field-error">{errors.message}</span>}
          </label>
          {/* Honeypot: invisible to people and screen readers; bots tend to fill it in. */}
          <div className="hp" aria-hidden="true">
            <label>
              Nickname
              <input tabIndex={-1} autoComplete="off" value={form.nickname} onChange={(e) => set("nickname", e.target.value)} />
            </label>
          </div>
          <label className="check">
            <input type="checkbox" checked={form.consent_contact} onChange={(e) => set("consent_contact", e.target.checked)} />
            <span>{config?.consent_text ?? "I agree to be contacted about this inquiry."}</span>
          </label>
          {errors.consent_contact && <span className="field-error">{errors.consent_contact}</span>}
          <label className="check">
            <input type="checkbox" checked={form.consent_marketing} onChange={(e) => set("consent_marketing", e.target.checked)} />
            <span>Send me occasional updates (optional).</span>
          </label>
          <button type="submit" className="primary" disabled={busy || !config}>
            {busy ? "Sending…" : "Send request"}
          </button>
        </form>
      </main>
    </div>
  );
}
