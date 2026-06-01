# job-tracker

## What This Is

A local-first career coach console — the active workspace for job searching. It ingests job listings, scores and evaluates them, generates tailored CV and cover letter materials, tracks applications, and analyzes job search performance.

When Jacob is in job-search mode, this is the tool he works in.

## Relationship to personal_ops

`personal_ops` is a separate project — an AI executive assistant for broader life/work management (habits, goals, calendar, reminders). It reads from this project via a bridge script (`personal_ops/ops/jobs.py`) that pulls `data/applications.csv` and generates a low-resolution daily summary for the assistant's context.

**Domain boundary:** All job search logic lives here. personal_ops only consumes a summary — it does not contain job search features. New job search features (analysis, coaching, application strategy) belong here as `job <command>`.

The resolution difference is intentional:
- **job_tracker**: full detail — fit analysis, company-level tracking, CV framing, rejection patterns
- **personal_ops**: life-ops level — "job search is active, N applications, M interviews, keeping pace?" 

## Commands

- `job pipeline` — full scan → score → evaluate → report cycle
- `job evaluate <url>` — evaluate a specific listing, generate fit report
- `job apply <url>` — generate tailored CV + cover letter, generate fit report
- `job track` — track application lifecycle (applied → phone_screen → interview → offer/rejected/withdrew)
- `job analyze` — analyze job search performance and get career coaching recommendations
- `job scan` — discovery and scoring only
- `job generate-criteria` — derive scoring rules from CV + profile
- `job profile-review` — CV ↔ profile consistency check

## Key Files

- `data/applications.csv` — manually tracked application lifecycle (the source of truth for what Jacob has actually applied to)
- `data/listings.csv` — pipeline-discovered listings (auto-generated, large)
- `data/cv.md` — full CV text (private)
- `data/profile.yaml` — structured candidate profile (private)
- `output/reports/` — per-listing markdown fit reports + analysis reports
- `output/applications/` — generated CV and cover letter files

## Conventions

- URL is the dedup key for listings.csv
- applications.csv is human-maintained and bidirectionally synced with the personal_ops daily jobs markdown
- Report files are named `<company>-<title>-<url-hash>.md`; analysis reports are `analysis-YYYY-MM-DD.md`
- All LLM calls go through `providers/` (OpenAI default, Anthropic optional); model/token config in `data/api-cost-config.yaml`
- ATS-aware fetching: Greenhouse, Lever, Ashby, Workable, Workday — add new ATS fetchers to `commands/evaluate.py`
