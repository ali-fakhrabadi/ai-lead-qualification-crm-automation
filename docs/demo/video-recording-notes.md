# Walkthrough video: how it was recorded

`assets/demo/08-ai-lead-qualification-crm-automation-demo.mp4` is a screen recording of the
running application, n8n and the mail sink on the lab deployment, with a synthetic lead
("Isla Morgan, Fernbrook Veterinary Clinics", `.example` domain). Nothing in it is staged or
animated: every screen shows what really happened to that lead.

## Method

1. **Recording:** `e2e/record_demo.sh` runs `e2e/record_demo.py` in the Playwright container
   on the stack's internal network. Each scene is a fresh browser context with Playwright's
   built-in video recording (`record_video_dir`, 1440 × 900, Chromium headless).
2. **Waiting is cut, not faked:** between scenes the script waits (off camera) for the real
   pipeline: the model's qualification, the CRM sync, the draft, the delivery after approval.
   The video therefore jumps from "submitted" to "qualified" instead of showing minutes of CPU
   inference.
3. **Captions:** a caption bar is drawn over the page with a small script (`setCaption`), so
   viewers know what each screen shows. It never changes the application's content.
4. **Cards:** the title and closing cards are rendered from
   `assets/branding/source/video-card.html` in the portfolio series style.
5. **Editing:** ffmpeg joins the cards (still images) and the scene recordings in order and
   encodes H.264.

## Scenes

| # | Scene | What is shown |
|---|---|---|
| 0 | Title card | project name and the flow |
| 1 | Form | the public form filled in and submitted; `202 Accepted` with the reference |
| 2 | Duplicate | the same request submitted again: recognised, no second lead |
| 3 | Qualification | dashboard, lead page: score, rules, evidence, model summary, CRM sync, submissions |
| 4 | Approval | draft edited, saved, approved with a note |
| 5 | Mail sink | the delivered message in Mailpit (the only place it goes) |
| 6 | n8n | the pipeline execution for this lead |
| 7 | Audit log | decisions and events |
| 8 | Closing card | repository name |

## Settings and result

| Item | Value |
|---|---|
| Recorded | 2026-11-19, lab host `24.179`, model `llama3.1` on `24.176` |
| Capture | Playwright 1.64 (Chromium, headless), 1440 × 900, 25 fps WebM per scene |
| Raw scenes | 7 scenes, 85.0 s in total (form 13.6 s, duplicate 6.4 s, qualification 25.6 s, approval 12.9 s, mail sink 4.9 s, n8n 15.2 s, audit 6.7 s) |
| Cuts | the sign-in at the start of scenes 3, 4 and 7 (1.5 s each) and the n8n sign-in and page load (5.5 s); nothing inside a scene was cut |
| Cards | title and closing card, 5 s each, rendered from `assets/branding/source/video-card.html` |
| Transitions | 0.3-0.4 s fades between scenes |
| Encoding | ffmpeg 8.0, H.264 (libx264, preset slow, CRF 22), yuv420p, 30 fps, `+faststart`, no audio |
| Final file | `08-ai-lead-qualification-crm-automation-demo.mp4`, 85.2 s, 2.98 MB, 1440 × 900 |
| Real waiting time skipped | the model's qualification of this lead, the CRM sync, the draft and the delivery after approval happened between scenes (their timings are on the lead page) |

## Privacy check

Before publishing, the frames were checked for passwords, tokens, cookies, internal addresses
and personal data: passwords are typed into password fields (masked), all people and companies
are invented, the browser is headless (no address bar), and no host address appears. The mail
sink's message list shows only synthetic addresses from the test runs.
