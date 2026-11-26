# Limitations and roadmap

This is an independent reference project, built and tested with synthetic data. It is not a
customer deployment.

## Limitations

- **No HTTPS in the lab deployment.** There is no domain, so the stack runs over plain HTTP on
  a private LAN. It must not be exposed to the internet like this; put it behind a reverse proxy
  with a real certificate first.
- **HubSpot is not verified against a real account.** The adapter speaks HubSpot's CRM v3/v4 API
  and every test runs against the bundled demo CRM that mirrors that subset; a run against a
  HubSpot developer test account is still to be done.
- **GoHighLevel is not verified against a real account either.** The connector follows the
  published API specs and is tested against a local mock only; the mock's choices for
  undocumented behaviour are listed in [crm-integration.md](crm-integration.md#gohighlevel).
- **CPU-only model.** `llama3.1` 8B on a CPU host takes about a minute or more per
  qualification (see the benchmark). Fine for tens of leads per hour, not for bursts of hundreds;
  a GPU host or a hosted model changes that (the provider is a setting).
- **Model judgement is advisory.** Agreement with the labels is measured on synthetic leads and
  one operator's labels; real sales outcomes may differ. The rules route anything uncertain to a
  person, which keeps errors cheap but costs review time.
- **Prompt-injection detection is a pattern check.** Novel phrasing can pass it: in the benchmark
  one attempt did and was auto-qualified (synced and drafted, not sent) before the check was
  extended. The structural controls (no tools, schema, rules, human approval) still prevent any
  message or action without a person.
- **One organisation, one rubric.** No multi-tenancy; one rubric file per deployment.
- **Email delivery** is to a local sink. Real SMTP is implemented behind two explicit switches but
  was not used.
- **No SSO/MFA** for operators; passwords + short-lived tokens only.
- **Single application host.** No high availability; PostgreSQL backups are logical dumps.

## Roadmap

1. HubSpot developer-account contract run in CI (only when a sandbox token secret is present).
2. HTTPS profile (Caddy/Traefik with ACME) for deployments with a domain.
3. Pipedrive and Zoho adapters behind the same interface.
4. Calendar booking link in approved drafts; reply tracking.
5. Per-partner webhook IP allow-lists and key rotation without downtime.
6. OIDC sign-in for operators.
7. Batch re-qualification after a rubric change, with a diff report.
