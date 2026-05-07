# Job Tracker

This project was inspired by [career-ops](https://github.com/santifer/career-ops) by [santifer](https://github.com/santifer), which is a great example of a local-first job search assistant. This project is a personal implementation of a similar idea, with a focus on practical LLM integration for profile review and job listing evaluation. The goal is to build a structured workflow around a local candidate profile, CV, and portal configuration, with LLM calls reserved for high-value tasks and a later TUI dashboard for managing the workflow.

A local-first job search assistant for keeping a structured candidate profile,
reviewing that profile against a CV, and eventually scanning job portals without
turning every page into an LLM call.

The project is intentionally built around plain files:

- `data/cv.md` - private CV/resume text.
- `data/profile.yaml` - private structured candidate profile used by classifiers.
- `data/portals.yaml` - private portal/search configuration.
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

## Current Commands

Show commands:

```bash
python entrypoint.py --help
```

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

## Status

Currently implemented:

- `profile-review`: compares `data/cv.md` to `data/profile.yaml` and recommends
  profile changes.
- Saved pending review flow so `--apply` does not make a second LLM call.
- Environment-only OpenAI API key lookup via `OPENAI_API_KEY`.

Planned:

- Token-efficient job listing scan pipeline.
- Local rule-based filtering before LLM classification.
- Listing cache/dedupe storage.
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

The scanner should avoid feeding raw portal pages to an LLM. The pipeline should
be staged so cheap local work handles volume and the LLM is reserved for high
value decisions.

### Discovery

1. Load portal/search configuration from `data/portals.yaml`.
2. Scan configured sources for job openings.
3. Extract cheap listing summaries only:
   title, company, location, URL, posted date, and snippet.
4. Normalize listings into one internal shape.
5. Dedupe by stable key before doing any expensive work.

### Local Triage

6. Filter obvious poor matches locally using:
   - keyword rules for target roles, required skills, avoid terms, seniority,
     location, and compensation,
   - grep-style matching over title/snippet/description,
   - local semantic search or embeddings for CV/profile similarity without an
     LLM subscription.
7. Fetch full job pages only for listings that survive local triage.
8. Store filtered listings with a status such as `DISCOVERED` or `TRIAGED`.

### LLM Evaluation

9. Send compact job excerpts to the LLM only for high value survivors.
10. Ask the LLM for a structured fit evaluation:
    score, rationale, concerns, matched strengths, missing requirements, and
    recommended action.
11. Cache LLM results by profile version, job content hash, and prompt version.
12. Put strong matches into the tracker as `EVALUATE` with a score.
13. Generate an ordered review list by score, recency, source quality, and
    application effort.

### Tailored Application Materials

14. For jobs the candidate chooses to pursue, generate a tailored CV plan from:
    `data/profile.yaml`, `data/cv.md`, and the selected job description.
15. Use the LLM to recommend the strongest wording, ordering, emphasis, and
    omissions for that specific application.
16. Render the selected CV variant to HTML through a Jinja template.
17. Export the HTML to a polished PDF or document suitable for application.

The generated CV should stay grounded in the base CV and profile. The LLM can
improve wording and prioritization, but it should not invent experience.

### Application Tracking

18. Walk the candidate through the application.
19. When the candidate confirms the application is complete, mark the job as
    `APPLIED`.
20. Periodically scan a connected email account for relevant replies, receipts,
    rejections, interview requests, or follow-ups.
21. If a company sends an application receipt, mark the job as `APPLIED` or
    `CONFIRMED_APPLIED`.
22. Track later statuses such as `INTERVIEW`, `REJECTED`, `OFFER`, and
    `ARCHIVED`.

Suggested status flow:

```text
DISCOVERED
-> TRIAGED
-> EVALUATE
-> SKIPPED | APPLY_READY
-> APPLIED
-> CONFIRMED_APPLIED
-> INTERVIEW | REJECTED | OFFER | ARCHIVED
```

Target shape:

```text
1000 raw listings
-> 400 after dedupe
-> 120 after local filters
-> 40 after detail-page fetch
-> 15-30 sent to LLM
```

## Git Hygiene

Private files are ignored:

- `.env`
- `data/cv.md`
- `data/profile.yaml`
- `data/portals.yaml`

Commit example files and code, not personal profile data or API keys.
