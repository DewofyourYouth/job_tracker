# Job Tracker

Inspired by [career-ops](https://github.com/santifer/career-ops) by [santifer](https://github.com/santifer).

A local-first job search assistant built around the principle that **API calls cost money and should be earned**. Local files, keyword rules, and deduplication handle the volume; the LLM is reserved for the decisions that actually benefit from it — profile review, fit evaluation of shortlisted jobs, and detailed job-fit reporting.

The project is intentionally built around plain files:

- `data/cv.md` - private CV/resume text.
- `data/profile.yaml` - private structured candidate profile used by classifiers.
- `data/portals.yaml` - private portal/search configuration.
- `data/scoring_criteria.yaml` - private generated scoring rules.
- `data/scoring_tuning.yaml` - private user-adjustable numeric scoring knobs.
- `data/listings.csv` - local tracking table for discovered, scored, and reported jobs.
- `output/llm_cache/` - cached quick LLM evaluations.
- `output/reports/` - detailed markdown job-fit reports.
- `data/*.example.*` - safe examples that can be committed.

## Current Quickstart

Use Python 3.14.0, matching `.python-version`.

If you use `pyenv`:

```bash
pyenv install 3.14.0
pyenv local 3.14.0
```

Create and activate a virtual environment:

```bash
python -m venv .venv
source .venv/bin/activate
```

Install requirements:

```bash
pip install -r requirements.txt
```

Create your private local files from the examples:

```bash
cp data/cv.example.md data/cv.md
cp data/profile.example.yaml data/profile.yaml
cp data/portals.example.yaml data/portals.yaml
cp data/scoring_tuning.example.yaml data/scoring_tuning.yaml
```

Edit `data/cv.md` with your real CV/resume information. Then edit
`data/profile.yaml` with your contact details, target roles, role preferences,
compensation expectations, location constraints, and proof points. The profile
review command can help refine this file, but it needs a reasonable starting
point.

Set your OpenAI API key:

```bash
export OPENAI_API_KEY="sk-..."
```

Or put it in `.env` if your shell/tooling loads `.env` automatically. This
project does not currently load `.env` itself.

API access is separate from ChatGPT/Codex access. If the command returns
`insufficient_quota`, the API project for that key needs billing/quota or a
different API key.

The current implementation uses OpenAI directly. A later provider interface
should make the LLM backend swappable so the same workflows can run against
OpenAI, Claude, Gemini, or another service.

Review your profile against your CV:

```bash
python entrypoint.py profile-review
```

This calls the LLM once, prints recommendations, and saves the full proposed
profile to `.job_tracker/profile_review.latest.json`. You are then asked
whether to apply the changes immediately. If you decline, the review is kept
on disk and you can apply it later without making another LLM call:

```bash
python entrypoint.py profile-review --apply
```

If the recommendations are not useful, discard the saved result:

```bash
python entrypoint.py profile-review --discard
```

Generate scoring criteria from the CV/profile and portal title filter:

```bash
python entrypoint.py generate-criteria
```

Run the full job report pipeline:

```bash
python entrypoint.py pipeline
```

By default the pipeline sends up to 100 rule-ranked listings to the quick LLM
evaluation stage, evaluates them with 5 concurrent workers, and writes detailed
reports for evaluated listings with `fit_score >= 5/10`.

## Current Commands

Show commands:

```bash
python entrypoint.py --help
```

### Profile Review

Review the profile against the CV:

```bash
python entrypoint.py profile-review
```

Add extra guidance:

```bash
python entrypoint.py profile-review \
  --feedback "Avoid DevOps-only roles. Prefer backend/platform product work."
```

Print raw JSON instead of the formatted summary:

```bash
python entrypoint.py profile-review --json
```

Apply a previously saved review without making another LLM call:

```bash
python entrypoint.py profile-review --apply
```

Discard the saved review without changing the profile:

```bash
python entrypoint.py profile-review --discard
```

Use a different model:

```bash
python entrypoint.py profile-review --model gpt-4.1-mini
```

The command can also be run directly:

```bash
python commands/profile_review.py
```

### Generate Criteria

Generate `data/scoring_criteria.yaml` from `data/cv.md`, `data/profile.yaml`,
and the optional `title_filter` in `data/portals.yaml`:

```bash
python entrypoint.py generate-criteria
```

Useful options:

```bash
python entrypoint.py generate-criteria --dry-run
python entrypoint.py generate-criteria --force
python entrypoint.py generate-criteria --model gpt-4o
```

The generated criteria controls candidate-specific matching rules: target role
keywords, title filters, location rules, compensation assumptions, and avoid
terms. Numeric ranking behavior can be tuned separately in
`data/scoring_tuning.yaml`, which overrides generated weights, thresholds, score
ladders, and penalties.

### Tune Scoring

Edit `data/scoring_tuning.yaml` when the ranking feels too strict, too loose,
or biased toward the wrong signal. This file is safe to hand-edit and is not
regenerated by the LLM.

Useful knobs:

- `weights.*` - relative importance of role fit, seniority, location, tech stack,
  avoid-role penalties, and optional semantic scoring.
- `tolerances.min_score_threshold` - how aggressively local scoring filters jobs
  before the LLM stage.
- `tolerances.top_n_for_llm` - how many rule-ranked listings reach the LLM.
- `score_ladders.role_fit.*` - raw scores for exact, strong, weak, and no-match
  role-title signals.
- `score_ladders.acceptable_location.*` - raw scores for remote/hybrid/onsite
  listings in acceptable locations.
- `score_ladders.avoid.*` - raw scores for hard/soft avoid matches that survive
  the hard filter.
- `adjustments.salary.below_min_penalty` - score reduction for parseable salary
  hints below your tolerated minimum.

### Scan

Run discovery, rule scoring, optional description fetching, and quick LLM
evaluation:

```bash
python entrypoint.py scan
```

Useful options:

```bash
python entrypoint.py scan --skip-llm
python entrypoint.py scan --top-n 150
python entrypoint.py scan --tuning-config data/scoring_tuning.yaml
python entrypoint.py scan --fetch-descriptions
python entrypoint.py scan --llm-concurrency 8
python entrypoint.py scan --output-json output/scan.json
```

`scan` writes/upserts `data/listings.csv`. URL is the dedupe key. Existing CSV
values are preserved when the current stage does not know a field yet.

### Pipeline

Run the end-to-end report workflow:

```bash
python entrypoint.py pipeline
```

Useful options:

```bash
python entrypoint.py pipeline --llm-concurrency 8
python entrypoint.py pipeline --reports-top-n 25
python entrypoint.py pipeline --tuning-config data/scoring_tuning.yaml
python entrypoint.py pipeline --min-report-score 6
python entrypoint.py pipeline --output-json output/pipeline.json
```

`pipeline` loads or generates scoring criteria, scans job boards, applies local
hard filters, rule-scores surviving listings, fetches full postings for the top
LLM candidates, runs quick LLM evaluation with bounded concurrency, then writes
or reuses detailed reports for evaluated listings with `fit_score >= 5/10`.

## Status

Currently implemented:

- `profile-review`: compares `data/cv.md` to `data/profile.yaml` and recommends
  profile changes.
- Saved pending review flow so `--apply` does not make a second LLM call.
- `generate-criteria`: derives `data/scoring_criteria.yaml` from the CV,
  profile, and portal title filter.
- `scan`: discovers jobs from configured sources, applies rule scoring, writes
  `data/listings.csv`, and can run quick LLM evaluations.
- `pipeline`: end-to-end flow for scanning, scoring, quick LLM evaluation, and
  detailed markdown report generation.
- Greenhouse, Lever, Workable, Ashby, and Brave Search discovery paths.
- Greenhouse detail fetching for direct API listings and Greenhouse URLs found
  through search.
- Enriched `listings.csv` fields such as location, department, employment type,
  workplace type, salary, source, first/last seen, rule score, LLM score,
  recommendation, report path, strengths, and red flags.
- Context-sensitive LLM cache files in `output/llm_cache/`.
- Bounded parallel quick LLM evaluation via `--llm-concurrency`.
- Report reuse by listing URL so reruns do not regenerate the same detailed
  markdown report.
- Environment-only OpenAI API key lookup via `OPENAI_API_KEY`.

Planned:

- Review queue for apply/maybe/skip decisions.
- Tailored CV generation from profile, base CV, and selected job posting.
- Application status tracking and email-based follow-up detection.
- Provider abstraction for using OpenAI, Claude, Gemini, or another LLM service
  behind one local interface.
- Textual-based TUI dashboard for reviewing listings, scores, generated CVs,
  and application statuses.

## Planned TUI

A later Textual dashboard should provide a local interface for the full workflow:

- review discovered jobs and scores,
- inspect LLM rationale and local filter matches,
- mark jobs as skipped, evaluate, apply-ready, or applied,
- generate and preview tailored CV variants,
- track email/application status changes,
- configure portal sources and LLM provider settings.

The TUI should sit on top of the same command/service layer as the CLI so the
workflow remains scriptable and testable.

## Profile Review Behavior

`profile-review` sends the CV and current YAML profile to OpenAI and asks for:

- general comments,
- specific revisions with current version, proposed change, and reasoning,
- cautions for weakly supported claims,
- a complete replacement `proposed_profile`.

It validates that the proposed profile contains the expected top-level sections
before writing anything.

The default flow calls the LLM, prints the structured result, saves it to
`.job_tracker/profile_review.latest.json`, then immediately asks whether to
apply the changes. If you decline, the saved result stays on disk. `--apply`
reads that saved result and updates `data/profile.yaml` without making another
LLM request. `--discard` deletes the saved result without touching the profile.

Required profile sections:

- `candidate`
- `target_roles`
- `narrative`
- `preferences`
- `compensation`
- `location`

## Scanning Design

The pipeline is designed to minimize expensive LLM calls. Job board API/search
requests collect the broad pool, cheap local rules narrow it, and only the
highest-ranked survivors reach the quick LLM evaluation stage.

Default funnel shape:

```text
raw job board/search results
-> deduped RawListing objects
-> hard title/avoid filter
-> weighted local rule scoring
-> top 100 sent to quick LLM evaluation
-> detailed reports for LLM fit_score >= 5/10
```

`top_n_for_llm` defaults to `100`. It can be adjusted persistently in
`data/scoring_tuning.yaml` or overridden for one run with `scan --top-n`.

### Discovery

1. Load portal/search configuration from `data/portals.yaml`.
2. Scan configured sources for job openings.
3. Prefer structured ATS APIs where possible: Greenhouse, Lever, Workable, and Ashby.
4. Use Brave Search for configured search-query discovery when needed.
5. Normalize listings into `RawListing` objects and dedupe by normalized URL.

### Local Triage

1. Filter obvious poor matches locally using:
   - keyword rules for target roles, required skills, avoid terms, seniority,
     location, and compensation,
   - title-filter positives/negatives from `portals.yaml` or generated criteria,
   - numeric tuning from `data/scoring_tuning.yaml` for weights, thresholds,
     score ladders, and soft penalties,
   - grep-style matching over title and fetched descriptions,
   - optional local semantic scoring when enabled in criteria.
2. Write hard-filter survivors into `data/listings.csv`.
3. Rule-score survivors and split them into top LLM candidates, surviving cut
   listings, and below-threshold listings.
4. Preserve cumulative CSV data across runs; URL is the dedupe/upsert key.

### LLM Evaluation (API call per surviving listing)

1. Fetch full postings for top LLM candidates before evaluation. Greenhouse URLs
   found through search are upgraded through the Greenhouse detail API when possible.
2. Send compact job excerpts to the quick LLM evaluator.
3. Evaluate listings with bounded thread-pool concurrency
   (`--llm-concurrency`, default `5`).
4. Ask the LLM for a structured fit evaluation:
   `fit_score` out of 10, summary, strengths, red flags, and recommendation.
5. Cache results in `output/llm_cache/` by listing context, criteria, and model
   so reruns do not re-spend unless relevant inputs change.
6. Sort results by LLM score, preserving local rule order as the tie-breaker.

### Detailed Reports

1. Generate detailed markdown reports for evaluated listings with
   `fit_score >= 5/10` by default.
2. Reports are written to `output/reports/`.
3. Existing reports for the exact listing URL are reused instead of regenerated.
4. New report filenames include a short URL hash so duplicate titles at the
   same company do not overwrite each other.
5. Report paths are written back into `data/listings.csv`.

### Tailored Application Materials (planned)

1. For jobs the candidate chooses to pursue, generate a tailored CV plan from:
   `data/profile.yaml`, `data/cv.md`, and the selected job description.
2. Use the LLM to recommend the strongest wording, ordering, emphasis, and
   omissions for that specific application. The LLM improves wording and
   prioritization but does not invent experience.
3. Render the selected CV variant to HTML through a Jinja template.
4. Export the HTML to a polished PDF or document suitable for application.

### Application Tracking (no API calls)

1. Walk the candidate through the application.
2. When the candidate confirms the application is complete, mark the job as `APPLIED`.
3. Periodically scan a connected email account for replies, receipts,
   rejections, and interview requests.
4. If a company sends an application receipt, mark the job as `CONFIRMED_APPLIED`.
5. Track later statuses such as `INTERVIEW`, `REJECTED`, `OFFER`, and `ARCHIVED`.

Suggested status flow:

```text
DISCOVERED -> TRIAGED -> EVALUATE -> SKIPPED | APPLY_READY
-> APPLIED -> CONFIRMED_APPLIED -> INTERVIEW | REJECTED | OFFER | ARCHIVED
```

## Git Hygiene

Private files are ignored:

- `.env`
- `data/cv.md`
- `data/profile.yaml`
- `data/portals.yaml`
- `data/scoring_criteria.yaml`
- `data/listings.csv`
- `output/`

Commit example files and code, not personal profile data or API keys.
