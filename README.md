# job-tracker

A local-first, cost-aware job search pipeline built around structured orchestration and selective LLM evaluation. It ingests job listings from ATS APIs and search sources, filters and scores them deterministically, and routes only the highest-confidence candidates to an LLM for deeper evaluation and report generation.

---

## Why I Built This

I'm a backend and infrastructure engineer in the middle of a deliberate career pivot — from Kubernetes-based platform engineering toward forward-deployed and AI systems integration roles. The job market for this kind of move is noisy: a lot of postings with overlapping titles, inconsistent seniority signals, irrelevant tech stacks, and compensation that varies enormously by location and remote policy.

Rather than triaging listings manually or throwing every job description at an LLM and hoping for a useful score, I built a structured pipeline that does the cheap work cheaply and the expensive work sparingly. The result is a reproducible, configurable workflow that surfaces genuinely relevant opportunities and produces detailed fit reports — without burning API budget on listings that a keyword rule could have eliminated in milliseconds.

---

## What This Demonstrates

- **Structured AI-assisted workflows** — LLM evaluation is one stage in a multi-stage pipeline, not the whole system. Local rules handle volume; the model handles judgment.
- **Deterministic pre-filtering before AI evaluation** — hard filters, rule scoring, and semantic similarity run locally. The LLM only sees listings that survived meaningful local triage.
- **Cost-aware orchestration** — per-stage model selection, token budgets, concurrency limits, and top-N volume controls are externalised to a YAML config. Cheaper models handle high-volume stages; better models handle report generation.
- **Evaluation-oriented thinking** — the pipeline is designed to be auditable. Every stage has structured outputs. LLM results are cached by input hash so reruns don't re-spend. Scoring logic is separated from criteria so you can tune one without touching the other.
- **Local-first data ownership** — all personal data (CV, profile, listings, reports, LLM cache) stays on disk. Nothing is sent to an external service except the LLM API call itself.
- **Human-in-the-loop by default** — the pipeline surfaces candidates and writes reports, but apply/skip decisions remain manual. Profile review changes require explicit confirmation before writing.
- **End-to-end application material generation** — `job apply` calls the LLM once per listing to produce tailored CV content and a cover letter, renders them into HTML templates, and exports PDFs via Playwright's CDP interface to system Chrome (the only reliable way to suppress Chrome's date/title overlay in headless mode).
- **Backend automation patterns** — concurrent LLM evaluation with bounded thread pools, URL-keyed CSV upserts, file-based caching, Jinja2 prompt templates, Click CLI with per-stage overrides.
- **Separation of concerns** — scoring weights live in one file, scoring criteria in another, API cost config in a third. Each can be modified independently without touching code.

---

## Pipeline Overview

```
Job board APIs + Brave Search
        │
        ▼
  Normalise + dedupe
  (URL-keyed RawListing objects)
        │
        ▼
  Hard title/avoid filter
  (keyword rules, no API call)
        │
        ▼
  Weighted rule scoring
  (role fit, seniority, location, tech stack, compensation)
        │
        ▼
  Top N → fetch full descriptions
  (Greenhouse API, web fetch)
        │
        ▼
  Optional: local semantic scoring
  (sentence-transformers, all-MiniLM-L6-v2)
        │
        ▼
  Quick LLM evaluation  [API call × top_n]
  (fit_score /10, summary, strengths, red flags)
  Results cached by input hash
        │
        ▼
  Detailed report generation  [API call × min_score survivors]
  (structured markdown, reused if report already exists)
        │
        ▼
  listings.csv  +  output/reports/
```

Each stage writes structured output. The CSV is the persistent state store — it accumulates across runs, URL is the dedupe key, and each stage only writes the fields it owns.

---

## Design Principles

**LLM calls should be earned.**
Every job description that reaches the LLM has already passed a hard title filter, a keyword scoring pass, and a configurable score threshold. The model sees a curated shortlist, not raw feed volume.

**Local-first data ownership.**
The CV, profile, all listings data, LLM responses, and generated reports live on disk. The pipeline reads and writes plain files. Nothing is stored in a third-party service. Sensitive personal data never leaves the machine except as part of a deliberate API call.

**Determinism before probability.**
The scoring system uses explicit, auditable rules — YAML-defined role archetypes, keyword lists, score ladders, and weighted dimensions. These are intentionally transparent so you can read why a listing scored the way it did and adjust the knobs without guessing.

**Cost is a first-class concern.**
Model selection, token budgets, concurrency, and how many listings reach each stage are all externalised to `data/api-cost-config.yaml`. Different stages use different models. The cheapest model that produces adequate results at a given stage is the right model for that stage.

**Human-in-the-loop, not autopilot.**
The pipeline produces ranked listings and fit reports. It does not apply to jobs, filter your email, or take actions on your behalf. Decisions remain with the operator.

**Structured systems over prompt magic.**
The LLM prompt is a Jinja2 template in `prompts/`. The system prompt, user message, and scoring context are all explicit. If evaluation quality degrades, the fix is in the template, the criteria file, or the scoring config — not in the hope that a better prompt string will emerge from iteration.

---

## Architecture

```
job-tracker/
├── entrypoint.py              # Click CLI entry point  →  `job <command>`
├── commands/
│   ├── pipeline.py            # End-to-end orchestration
│   ├── scan.py                # Discovery + scoring + LLM evaluation
│   ├── generate_criteria.py   # LLM-assisted criteria generation from CV + profile
│   ├── report.py              # Detailed markdown report generation
│   ├── profile_review.py      # CV ↔ profile consistency review
│   └── apply.py               # Tailored CV + cover letter generation and PDF export
├── classify/
│   ├── score.py               # Rule-based scoring engine
│   ├── llm.py                 # Quick LLM evaluation (cached)
│   └── semantic.py            # Local semantic scoring
├── providers/
│   ├── base.py                # LLMClient ABC + LLMRateLimitError / LLMAPIError
│   ├── openai_client.py       # OpenAI implementation
│   └── anthropic_client.py    # Anthropic implementation (requires anthropic extra)
├── fetch/                     # ATS API clients (Greenhouse, Lever, Workable, Ashby)
├── prompts/
│   ├── render.py              # Jinja2 template renderer
│   ├── llm_eval_system.md     # Quick eval system prompt
│   ├── llm_eval_user.md       # Quick eval user message template
│   ├── report_system.md       # Report generation system prompt
│   ├── report_user.md         # Report generation user message template
│   ├── criteria_system.md     # Criteria generation system prompt
│   ├── criteria_user.md       # Criteria generation user message template
│   ├── apply_system.md        # Application material generation system prompt
│   └── apply_user.md          # Application material generation user message template
├── templates/
│   ├── cv-template.html       # HTML/CSS CV template (Space Grotesk + DM Sans, print-optimised)
│   └── cl-template.html       # HTML/CSS cover letter template
└── data/
    ├── cv.md                  # Private — full CV text
    ├── profile.yaml           # Private — structured candidate profile
    ├── portals.yaml           # Private — search/ATS source configuration
    ├── scoring-criteria.yaml  # Private — generated scoring rules
    ├── scoring-tuning.yaml    # Private — numeric scoring overrides
    └── api-cost-config.yaml   # Private — per-stage model/token/volume config
```

**Key data flows:**

- `data/cv.md` + `data/profile.yaml` → `generate-criteria` → `data/scoring-criteria.yaml`
- `data/portals.yaml` → discovery → `data/listings.csv` (upserted each run)
- `data/scoring-criteria.yaml` + `data/scoring-tuning.yaml` → rule scoring
- `data/api-cost-config.yaml` → provider selection, model, token budgets, concurrency, volume controls
- `output/llm_cache/` → hash-keyed evaluation cache (skips re-evaluation on rerun)
- `output/reports/` → URL-keyed markdown reports (skips regeneration if report exists)
- `data/listings.csv` + `data/cv.md` → `apply` → `output/applications/<slug>/cv.pdf` + `cover-letter.pdf`

---

## Setup

Python 3.14+ is required. The repo uses a [pyenv](https://github.com/pyenv/pyenv) virtualenv named `jobs-env`. `.python-version` contains the virtualenv name so pyenv activates it automatically on `cd`.

### With pyenv (recommended)

```bash
pyenv install 3.14.0          # skip if already installed
pyenv virtualenv 3.14.0 jobs-env
```

From the project root — pyenv auto-activates `jobs-env` via `.python-version`:

```bash
pip install -e .
```

The editable install registers the `job` CLI entry point.

### Without pyenv

Create and activate any Python 3.14+ virtual environment, then:

```bash
pip install -r requirements.txt
pip install -e .
```

### Private data files

```bash
cp data/cv.example.md data/cv.md
cp data/profile.example.yaml data/profile.yaml
cp data/portals.example.yaml data/portals.yaml
cp data/scoring-tuning.example.yaml data/scoring-tuning.yaml
cp data/api-cost-config.example.yaml data/api-cost-config.yaml
```

Edit `data/cv.md` with your CV text. Edit `data/profile.yaml` with your contact details, target roles, preferences, compensation, and location constraints.

### API keys

The default provider is OpenAI. Set the key for whichever provider you configure in `data/api-cost-config.yaml`:

```bash
export OPENAI_API_KEY="sk-..."        # provider: openai (default)
export ANTHROPIC_API_KEY="sk-ant-..." # provider: anthropic
```

This project does not load `.env` automatically.

To use Anthropic, install the optional dependency:

```bash
pip install "job-tracker[anthropic]"
```

---

## Quickstart

```bash
job generate-criteria        # derive scoring rules from CV + profile
job pipeline                 # scan → score → evaluate → report
```

---

## Commands

```bash
job --help
```

### `generate-criteria`

Sends `data/cv.md` and `data/profile.yaml` to the LLM and writes a structured `data/scoring-criteria.yaml` containing target role archetypes, keyword lists, location rules, compensation assumptions, and avoid terms.

```bash
job generate-criteria
job generate-criteria --dry-run
job generate-criteria --force
job generate-criteria --model gpt-4.1-mini
```

### `profile-review`

Compares `data/cv.md` to `data/profile.yaml` and returns structured recommendations. Saves the proposed profile to disk before asking whether to apply changes — `--apply` writes without a second API call.

```bash
job profile-review
job profile-review --feedback "Avoid DevOps-only roles."
job profile-review --json
job profile-review --apply
job profile-review --discard
job profile-review --model gpt-4.1-mini
```

Required profile sections: `candidate`, `target_roles`, `narrative`, `preferences`, `compensation`, `location`.

### `scan`

Runs discovery, rule scoring, optional description fetching, and quick LLM evaluation. Writes/upserts `data/listings.csv`.

```bash
job scan
job scan --skip-llm
job scan --top-n 150
job scan --fetch-descriptions
job scan --llm-concurrency 8
job scan --output-json output/scan.json
```

### `pipeline`

End-to-end orchestration: loads or generates criteria → scans → filters → scores → fetches descriptions → LLM evaluation → detailed report generation.

```bash
job pipeline
job pipeline --llm-concurrency 8
job pipeline --reports-top-n 25
job pipeline --min-report-score 6
job pipeline --tuning-config data/scoring-tuning.yaml
job pipeline --output-json output/pipeline.json
```

### `apply`

Generates a tailored CV and cover letter for a specific job listing. One LLM call produces a structured JSON payload — tailored summary, competency tags, reweighted experience bullets, and a 3-paragraph cover letter — which is rendered into the HTML templates and exported to PDF via Playwright.

URL is optional. Omit it to pick interactively from the pipeline's recommendations. The picker shows only LLM-evaluated listings with a recommendation of `apply` or `maybe`, sorted by fit score, with the pipeline's fit summary visible so you can remind yourself why a listing scored well before committing. Listings marked `skip` are excluded. Run `job pipeline` before `job apply` to populate these recommendations.

```bash
job apply <url>                      # apply to a specific listing
job apply                            # pick interactively from listings.csv
job apply <url> --pdf                # also export PDFs
job apply <url> --pdf --open         # export PDFs and open them
job apply <url> --no-cover-letter    # CV only
job apply <url> --model gpt-4.1      # override model for this run
job apply <url> --output-dir ~/Desktop/application
```

Output goes to `output/applications/<company>-<title>-<hash>/`:
- `cv.html` / `cv.pdf`
- `cover-letter.html` / `cover-letter.pdf`

PDF export requires Playwright (`pip install playwright`) using your installed Google Chrome — no separate browser binary is downloaded.

The model, token budget, and default margins are configurable in `data/api-cost-config.yaml` under `apply_generation`.

---

## Configuration

### Scoring tuning (`data/scoring-tuning.yaml`)

Numeric overrides for the rule scoring engine. Safe to hand-edit; not regenerated by the LLM.

| Key                                    | Controls                                                |
| -------------------------------------- | ------------------------------------------------------- |
| `weights.*`                            | Relative weight of each scoring dimension               |
| `tolerances.min_score_threshold`       | Local score cutoff before LLM stage                     |
| `score_ladders.role_fit.*`             | Raw scores for exact/strong/weak/no-match role signals  |
| `score_ladders.acceptable_location.*`  | Scores for remote/hybrid/onsite in acceptable locations |
| `adjustments.salary.below_min_penalty` | Penalty for listings below salary minimum               |

### API cost config (`data/api-cost-config.yaml`)

Per-stage model selection, token budgets, concurrency, volume controls, and provider selection.

```yaml
# provider: openai | anthropic  (per-stage override also supported)
# OpenAI ↔ Anthropic rough equivalents:
#   gpt-4.1-nano  →  claude-haiku-4-5-20251001   (fast / cheap)
#   gpt-4.1-mini  →  claude-sonnet-4-6            (balanced)
#   gpt-4.1       →  claude-opus-4-7              (best quality)
provider: openai

criteria_generation:
  model: gpt-4.1-mini        # anthropic: claude-sonnet-4-6
  max_tokens: 2048

llm_evaluation:
  model: gpt-4.1-nano        # anthropic: claude-haiku-4-5-20251001
  max_tokens: 512
  concurrency: 5
  top_n: 20

report_generation:
  model: gpt-4.1-mini        # anthropic: claude-sonnet-4-6
  max_tokens: 1500
  min_score: 7
  top_n: 5

semantic:
  model: all-MiniLM-L6-v2   # local; no API call

apply_generation:
  model: gpt-4.1-mini        # anthropic: claude-sonnet-4-6
  max_tokens: 3500
```

To switch a single stage to Anthropic while keeping the rest on OpenAI:

```yaml
provider: openai

report_generation:
  provider: anthropic
  model: claude-opus-4-7
  max_tokens: 1500
  min_score: 7
  top_n: 5
```

CLI flags override model and volume values for a single run. Provider selection is config-only.

---

## Planned Improvements

### Evaluation and observability

- Structured evals for the LLM evaluation stage — ground truth comparisons against manually reviewed listings to measure scoring drift over time.
- Per-run tracing and token spend logging so cost per pipeline run is visible and attributable by stage.

### Retrieval and matching

- Retrieval-assisted matching using profile embeddings against listing corpora to surface structurally similar roles that keyword rules miss.
- Richer semantic similarity: embedding the full CV narrative against job descriptions rather than just keyword presence.

### Structured outputs

- Migrate quick LLM evaluation to structured JSON output mode (currently parsed from free-text JSON in the response body).
- Schema validation on LLM output at each stage rather than optimistic parsing.

### Application tracking

- Status lifecycle: `DISCOVERED → TRIAGED → APPLY_READY → APPLIED → CONFIRMED → INTERVIEW → OFFER/REJECTED/ARCHIVED`.
- Email-based status detection for application receipts, rejections, and interview requests.

### TUI

- Textual-based local dashboard for reviewing ranked listings, inspecting LLM rationale, marking status, and triggering report generation — sitting on top of the same command/service layer as the CLI.

---

## Git Hygiene

Private files are gitignored:

- `data/cv.md`, `data/profile.yaml`, `data/portals.yaml`
- `data/scoring-criteria.yaml`, `data/api-cost-config.yaml`
- `data/listings.csv`
- `output/`

Example files (`data/*.example.*`) are committed. Personal data and API keys are not.

---

Inspired by [career-ops](https://github.com/santifer/career-ops) by [santifer](https://github.com/santifer).
