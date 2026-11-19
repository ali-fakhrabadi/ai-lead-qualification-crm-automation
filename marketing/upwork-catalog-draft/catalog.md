# Upwork Project Catalog draft: AI lead qualification & CRM automation

**State: DRAFT SAVED IN UPWORK** (2026-11-19). All five editor steps were saved; the listing
sits at the final "Finalize" step with **Submit for Review not clicked** and the terms checkbox
not ticked. The dashboard shows it under Drafts, with Approved (0) and Under Review (0). Never
published, activated or submitted. Based on this repository (Portfolio item 08). Market
research: [market-research.md](market-research.md).

## What was entered in Upwork

| Field | Value |
|---|---|
| Title | You will get an AI lead qualification and CRM sync workflow with n8n (55/75) |
| Category | Development & IT > AI & Machine Learning > Generative AI (no automation-specific category is offered) |
| Attributes | AI algorithm: Large Language Model; applications: Natural Language Understanding, Natural Language Generation, AI-Enhanced Classification; language: Python; tool: LLaMA |
| Search tags | n8n, lead qualification, crm integration, ai automation, fastapi |
| Tiers | Lead Capture Workflow / AI Qualification & CRM Sync / Secure Lead Automation System; $420 / $1,080 / $2,280; 5 / 10 / 21 days; 1 / 2 / 3 revisions |
| Tier options | Setup file, source code: all tiers. AI model integration, database integration, model deployment, NLP, prompt engineering: Standard and Advanced. Model testing & optimization: Advanced |
| Gallery | hero (cover), workflow, CRM sync, human approval, architecture and n8n overview (the two diagrams padded to 4:3), all 1600 x 1200 |
| Video | `assets/demo/08-ai-lead-qualification-crm-automation-demo-upwork.mp4` (85 s; closing card without the repository link, as Upwork does not allow external links in media) |
| Requirements | the eight buyer questions below; 1, 2 and 4 marked as needed before work starts |
| Steps | Kickoff and scope; Intake, validation and duplicate rules; AI qualification and CRM sync; Drafts, human approval and error handling; Test, deploy and hand over |
| Summary | the text below (1,167 characters; the editor's server rejected moving on with a longer version) |
| FAQ | the first five questions below |

## Title

`You will get an AI lead qualification and CRM sync workflow with n8n`

(Upwork prefixes "You will get"; adjusted from the working title "I will automate lead
qualification and CRM workflows with AI and n8n" to fit the form and buyer search terms.)

- **Category:** Development & IT → AI Apps & Integration (or Workflow Automation, whichever the
  form offers closest).
- **Search tags:** n8n, Lead Qualification, CRM Integration, AI Automation, FastAPI.
- **Keywords used naturally in the text:** HubSpot, webhook, Python, PostgreSQL, Docker, Ollama,
  OpenAI, lead management, deduplication.

## Packages

Priced from estimated hours at the $30/hour baseline, with about 12 % contingency. Upwork's fee
(0-15 % per contract) comes out of these amounts.

| | Basic: Lead Capture Workflow | Standard: AI Lead Qualification & CRM Sync | Advanced: Secure Lead Automation System |
|---|---|---|---|
| Price | **$420** | **$1,080** | **$2,280** |
| Estimated hours | 14 | 36 | 76 |
| Delivery | 5 days | 10 days | 21 days |
| Revisions | 1 | 2 | 3 |
| Lead sources | 1 form **or** 1 webhook | 1 | up to 3 (forms/webhooks, agreed in writing) |
| Validation, normalisation | ✓ | ✓ | ✓ |
| Duplicate handling | idempotent intake, same-email check | + same-email updates and conflicts | + review queue for possible duplicates |
| n8n workflows | 1 | lead pipeline + error workflow | lead pipeline, delivery, error workflow |
| Destination | 1: a sheet/Airtable or 1 CRM (create/update) | 1 CRM: contact, company, note | 1 CRM with custom field mapping |
| AI qualification (structured output + your rules) | - | ✓ (one rubric, written with you) | ✓ + evaluation on up to 50 of your sample leads |
| Model setup | - | Ollama on your server or OpenAI (your key) | same, plus a data-flow review |
| Follow-up draft + human approval | - | ✓ (drafts, approval in n8n or email) | ✓ dashboard for review, approval, audit log |
| Retries and error handling | basic | ✓ backoff, permanent vs transient | ✓ + outbox, replay, dead-letter view |
| Backend | n8n only | n8n + small FastAPI service | FastAPI + PostgreSQL + n8n (this project's design) |
| Deployment | into your existing n8n | Docker Compose on your server | Docker Compose, hardened, backups, TLS proxy if you have a domain |
| Tests and evidence | test run with 10 sample leads | workflow tests incl. replays and failures | end-to-end test report + benchmark on your sample leads |
| Handover | setup notes | docs + 45 min call | docs, threat model, 2 calls |

### Hour breakdown

- **Basic (14 h):** scope and access 1.5, intake + validation/normalisation 3, idempotency and
  same-email check 2, one workflow 1.5, destination connector 2.5, test with 10 sample leads and
  setup notes 1.5 = 12; contingency 2.
- **Standard (36 h):** kickoff and rubric workshop 3, intake + validation + idempotency 4, AI
  qualification with schema, rules and prompt hardening 7, CRM upsert (contact, company, note)
  6, retry and error workflow 3, draft + approval step 4, workflow tests (replays, CRM and model
  failures) 3, docs and handover 2 = 32; contingency 4.
- **Advanced (76 h):** kickoff and data-flow review 4, up to 3 sources 8, FastAPI service with
  outbox and n8n orchestration 10, lifecycle and audit log 6, duplicate review queue 6, CRM with
  custom field mapping 8, review/approval dashboard and delivery 6, hardened deployment and
  backups 8, end-to-end test report and evaluation on your sample leads 8, handover 4 = 68;
  contingency 8.

## Description

```text
New leads arrive from your website or partners, but someone still copies them into the CRM, misses duplicates and answers late. I build a workflow that does the busywork and leaves the decisions to your team.

- Leads come in through your form or a signed webhook; fields are validated and normalised
- Repeated submissions and webhook retries never create a second lead or CRM contact
- An AI model returns structured fields (intent, fit, urgency, what is missing); your rules turn them into a score and decision; anything uncertain goes to a person
- Qualified leads are created or updated in your CRM with a short note
- A reply is drafted, and nothing is sent until your team approves it
- Failures are retried with backoff; permanent errors stop for a person

Built with self-hosted n8n and Python (FastAPI) in Docker. The model can run on your own server (Ollama) or use OpenAI with your key.

Based on my open-source reference project, tested with 15 end-to-end scenarios and an n8n workflow harness.

Not included: CRM or model costs, migrating old records, voice or WhatsApp, messages to prospects without your approval. Message me to confirm your CRM.
```

## Requirements from the buyer

1. Which form, website or system captures your leads today (and can it send a webhook)?
2. Which CRM do you use, and can you give me access to a test account or sandbox with API access?
3. Which fields should be captured, and which are required?
4. What makes a lead "qualified" for you? Share any existing criteria or examples of good and poor leads.
5. Which follow-up channel do you use, and should replies stay as drafts?
6. Where should n8n and the backend run (your server, your cloud account, or your existing n8n)?
7. Self-hosted model or an external provider such as OpenAI? Any data-transfer restrictions?
8. Any data retention, consent or privacy requirements I must follow?

## FAQ

- **Which CRMs are supported?** HubSpot is the reference integration. Pipedrive, Zoho, Airtable
  or another CRM with a documented REST API can be done in the same pattern; I confirm before you
  order. I need API access to a test account or sandbox first, never your live customers.
- **Do I need n8n already?** No. I can install self-hosted n8n with Docker on your server, or
  build into your existing n8n (Basic).
- **Where does lead data go?** With a self-hosted model, only to your own server and your CRM.
  With OpenAI, the inquiry's business fields (not name or email) are sent to OpenAI under your
  account's terms.
- **Can it merge duplicates automatically?** Exact repeats are handled automatically. Possible
  duplicates (same name at the same company, a different email) go to a review step; they are
  never merged without a person.
- **Will it email my leads automatically?** Not by default. Replies are drafts until someone
  approves them; sending is enabled only if you ask for it in writing.
- **How is it tested?** With synthetic test leads first, including repeats and simulated CRM and
  model failures; then a short supervised run with your real form before you switch it on.
- **What is not included?** See the description; anything outside the agreed package is quoted
  separately before work starts.

## Gallery (1600 × 1200)

1. `upwork-catalog-hero.png`: hero in the portfolio series style.
2. `upwork-catalog-workflow.png`: the lead pipeline in n8n with what each step does.
3. `upwork-catalog-crm-sync.png`: lead page with the qualification and CRM sync result.
4. `upwork-catalog-human-approval.png`: follow-up draft and approval.

All from the real application (see `assets/branding/` in the repository).
