# job-tracker

## What This Is

A local-first career coach console — the active workspace for job searching. It ingests job listings, scores and evaluates them, generates tailored CV and cover letter materials, tracks applications, and analyzes job search performance.

When Jacob is in job-search mode, this is the tool he works in.

## Relationship to personal_ops

`personal_ops` is a separate project — an AI executive assistant for broader life/work management (habits, goals, calendar, reminders). Its current main branch has retired job tracking: the formerly documented `ops/jobs.py` bridge is absent, and its `VPS_MIGRATION.md` records the June 3 decision to remove job-tracker coupling. Do not assume an active CSV consumer or bidirectional sync. A separately deployed older version has not been verified.

**Domain boundary:** All job search logic lives here. personal_ops only consumes a summary — it does not contain job search features. New job search features (analysis, coaching, application strategy) belong here as `job <command>`.

The resolution difference is intentional:
- **job_tracker**: full detail — fit analysis, company-level tracking, CV framing, rejection patterns
- **personal_ops**: broader life-ops; job-tracking summaries are retired in current source.

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

- `data/applications.csv` — manually maintained local runtime ledger of actual applications. Google Sheets is the active working tracker; reconcile verified milestones into this file with provenance, preserving local history. See `docs/application-reconciliation-2026-10-17.md` for the audited baseline and unresolved limits.
- `data/listings.csv` — pipeline-discovered listings (auto-generated, large)
- `data/cv.md` — full CV text (private)
- `data/profile.yaml` — structured candidate profile (private)
- `data/positioning.yaml` — role archetypes → fit tier (scoring bias) + CV framing (headline, lead pillar, guardrails); read by `classify/positioning.py`. Optional — absent = feature off.
- `output/reports/` — per-listing markdown fit reports + analysis reports
- `output/applications/` — generated CV and cover letter files

## Conventions

- URL is the dedup key for listings.csv
- applications.csv is human-maintained. No active automatic sync with Google Sheets or personal_ops has been verified. Prefer dated, verified milestones over older statuses; preserve historical notes and unknown dates. Never import unapplied leads as applications or replace a separate local ledger without comparing its uncommitted/ignored data first.
- Report files are named `<company>-<title>-<url-hash>.md`; analysis reports are `analysis-YYYY-MM-DD.md`
- All LLM calls go through `providers/` (OpenAI default, Anthropic optional); model/token config in `data/api-cost-config.yaml`
- ATS-aware fetching: Greenhouse, Lever, Ashby, Workable, Workday — add new ATS fetchers to `commands/evaluate.py`
- Positioning: `data/positioning.yaml` is the single source for role archetypes. Tier → a soft, **promotion-only** rule-score bias (strength tuned in `data/scoring-tuning.yaml` → `archetype_bias`) plus CV framing in `job apply`. Validate against history with `python scripts/validate_positioning.py`.
