# Portfolio style audit

Done before designing any asset for this project, by inspecting the existing covers of the
series (items 01-07, 1600 × 1200 PNG) and the HTML template they are rendered from. The goal is
that item 08 is recognisably part of the same series.

## What the existing covers share

| Element | Observed |
|---|---|
| Canvas | 1600 × 1200 px (4:3), the size Upwork shows best for portfolio thumbnails |
| Background | near-black `#0D1117` with two soft radial glows: green bottom-left (`rgba(46,160,67,.30)`), cyan top-right (`rgba(56,189,248,.22)`); a faint dot grid (44 px pitch) fading towards the edges |
| Brand mark | top left: green rounded square `#2EA043` with a white "A", then **ALI F.** in wide-tracked bold caps and "Python · AI · DevOps" in dim grey `#6E7681` |
| Category label | top right, monospace caps, cyan `#38BDF8`, with a cyan dot (e.g. "AI · SELF-HOSTED RAG", "AI AUTOMATION · SELF-HOSTED") |
| Title | 112 px, weight 800, tight tracking (-0.04 em), white, with the key phrase in green `#3FB950` |
| Subtitle | 38 px, `#C9D1D9`, one or two lines, a plain description of what the system does |
| Metric cards | bottom left, stacked: first card green border + green number (92 px mono), second card neutral border + cyan number (72 px mono); each with a short muted caption. Only measured results |
| Tags | four monospace chips (`#161B22` fill, `#30363D` border) along the bottom left |
| Product image | a real screenshot of the running software in a dark browser frame (three dots), tilted in 3-D (rotateY -14°, rotateX 3°), bleeding off the bottom-right corner |
| Typefaces | Inter / Segoe UI Variable Display for text, JetBrains Mono / Cascadia Code for numbers, labels and chips |
| Tone | engineering, factual, no stock imagery, no gradients on text, no emoji |

The application UIs in the series use the same palette family: GitHub-dark sidebar `#0D1117`,
green primary `#1F883D`/`#3FB950`, cyan accents `#0284C7`/`#38BDF8`.

## Decisions for item 08

- Same template (`cover.html` in the portfolio source folder, new entry `p=8`), same canvas,
  background, brand mark, type scale and card layout.
- Category label: **"AI AUTOMATION · n8n + FastAPI"** (cyan).
- Title: **"AI lead *qualification* & CRM sync"** with "qualification" in green: this project's
  own emphasis.
- Product image: the real lead detail page (qualification score, grounded evidence, draft
  awaiting approval), because it shows the human-in-the-loop idea at a glance; the n8n canvas is
  used in the gallery images instead.
- Metrics: two numbers measured in this project's own runs (benchmark, workflow harness or
  end-to-end tests), chosen after the runs: never planned figures.
- Tags: FastAPI · n8n · Ollama · PostgreSQL.
- Gallery images, social preview and diagrams reuse the palette and fonts; diagrams use the
  dark background with green for the happy path and cyan for system boundaries, amber only for
  "needs a person".

## Checks before publishing an asset

- Text readable at Upwork's thumbnail size (about 400 px wide): title ≥ 100 px on the canvas.
- No cropped headings or logos; consistent 80-88 px margins.
- Screenshots are of the real application with synthetic data only; no host names, internal
  addresses, tokens, real emails or personal data.
- Spelling and grammar checked.
