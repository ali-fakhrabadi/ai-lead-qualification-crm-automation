# Progress log

## 2026-11-19 — inspection and plan

- Both lab host identifiers resolved through the existing SSH configuration; host keys verified
  against `known_hosts` (no host-key workarounds). Resources, Docker versions, listening ports
  and existing workloads inspected read-only on both; nothing changed.
- `24.176` runs a Kubernetes control plane with unrelated services and the shared Ollama model
  server; `24.179` runs other lab stacks and has the n8n 2.45.6 images already loaded.
- Topology decided: application and data on `24.179`, model inference on `24.176`
  (see `implementation-plan.md`).
- GitHub authenticated as `ali-fakhrabadi`; repository name `ai-lead-qualification-crm-automation`
  is free.
- Checked current documentation: n8n stable is 2.46.7 (2.45.6 used, see plan), HubSpot CRM v3
  contacts search/create/update and batch upsert, Mailpit 1.29.6.
