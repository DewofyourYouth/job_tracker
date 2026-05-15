"""
Detailed per-listing job-fit report generator.

Produces one markdown report per listing (see output/reports/sample-report.md
for the expected format). Each report is a rich narrative that references the
candidate's specific CV, profile, and the job posting's details.

Because these reports read the full CV and job description, each one costs a
larger LLM call than the quick-scan evaluation in classify/llm.py. They are
intended only for the top few listings that survived earlier pipeline stages.
"""

from __future__ import annotations

import json
import re
import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml
from openai import OpenAI
from rich.console import Console

from classify.llm import LLMEvaluation
from classify.rules import ScoredListing
from prompts.render import render_prompt

console = Console()

REPORTS_DIR = Path("output/reports")


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class DetailedReport:
    listing_url: str
    title: str
    company: str
    date: str

    legitimacy: str           # "High Confidence" | "Medium Confidence" | "Low Confidence" | "Suspicious"
    global_score: float       # 0.0 – 5.0

    cv_match_bullets: str     # skills / experience / proof points / gaps (prose)
    cv_match_score: float     # 0.0 – 5.0 for CV match specifically
    cv_match_summary: str     # one-sentence bottom line

    north_star: str           # archetype fit, role framing, how to position application

    comp_stated_range: str
    comp_company_rep: str
    comp_location_context: str
    comp_assessment: str

    cultural_remote_policy: str
    cultural_company_size: str
    cultural_engineering: str
    cultural_timezone: str

    red_flags: list[str] = field(default_factory=list)

    global_score_rationale: str = ""
    posting_legitimacy_detail: str = ""
    posting_legitimacy_verdict: str = ""


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_report_system_prompt(candidate_name: str) -> str:
    return render_prompt(
        "report_system.md",
        candidate_name=candidate_name,
        first_name=candidate_name.split()[0],
    )


def build_report_user_prompt(
    scored: ScoredListing,
    evaluation: LLMEvaluation,
    criteria: dict,
    cv_text: str,
    profile_text: str,
) -> str:
    listing = scored.listing

    desc = listing.description or "not fetched"
    if len(desc) > 2000:
        desc = desc[:2000].strip() + "..."

    return render_prompt(
        "report_user.md",
        date=datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        cv_text=cv_text,
        profile_text=profile_text,
        title=listing.title,
        company=listing.company,
        url=listing.url,
        location=listing.location or "not specified",
        salary_hint=listing.salary_hint or "not specified",
        rule_scores="  ".join(
            f"{name}: {c.raw_score:.2f}" for name, c in scored.criteria.items()
        ),
        total_score=f"{scored.total_score:.3f}",
        fit_score=evaluation.fit_score,
        fit_summary=evaluation.fit_summary,
        strengths=evaluation.strengths,
        red_flags=evaluation.red_flags,
        description=desc,
    )


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

DEFAULT_REPORT_MAX_TOKENS = 1500


def generate_detailed_report(
    client: OpenAI,
    scored: ScoredListing,
    evaluation: LLMEvaluation,
    criteria: dict,
    cv_text: str,
    profile_text: str,
    *,
    model: str = "gpt-4o",
    max_tokens: int = DEFAULT_REPORT_MAX_TOKENS,
) -> DetailedReport:
    """Call the LLM to produce a detailed report for one listing."""
    profile = yaml.safe_load(profile_text) or {}
    candidate_name = (
        profile.get("candidate", {}).get("full_name")
        or profile.get("candidate", {}).get("name")
        or "the candidate"
    )

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": build_report_system_prompt(candidate_name)},
            {
                "role": "user",
                "content": build_report_user_prompt(
                    scored, evaluation, criteria, cv_text, profile_text
                ),
            },
        ],
    )

    raw = response.choices[0].message.content or "{}"
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        data = {}

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    cv_match = data.get("cv_match", {})
    comp = data.get("comp", {})
    cultural = data.get("cultural_signals", {})
    legitimacy_block = data.get("posting_legitimacy", {})

    return DetailedReport(
        listing_url=scored.listing.url,
        title=scored.listing.title,
        company=scored.listing.company,
        date=date_str,
        legitimacy=data.get("legitimacy", "Unknown"),
        global_score=float(data.get("global_score", 0.0)),
        cv_match_bullets=cv_match.get("bullets", ""),
        cv_match_score=float(cv_match.get("score", 0.0)),
        cv_match_summary=cv_match.get("summary", ""),
        north_star=data.get("north_star", ""),
        comp_stated_range=comp.get("stated_range", "Not stated"),
        comp_company_rep=comp.get("company_rep", ""),
        comp_location_context=comp.get("location_context", ""),
        comp_assessment=comp.get("assessment", ""),
        cultural_remote_policy=cultural.get("remote_policy", ""),
        cultural_company_size=cultural.get("company_size", ""),
        cultural_engineering=cultural.get("engineering_culture", ""),
        cultural_timezone=cultural.get("timezone", ""),
        red_flags=data.get("red_flags", []),
        global_score_rationale=data.get("global_score_rationale", ""),
        posting_legitimacy_detail=legitimacy_block.get("detail", ""),
        posting_legitimacy_verdict=legitimacy_block.get("verdict", ""),
    )


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def render_report_markdown(report: DetailedReport) -> str:
    """Render a DetailedReport as a markdown string matching the sample format."""
    red_flags_md = "\n".join(f"- {flag}" for flag in report.red_flags) or "- None noted."

    return f"""# Evaluation: {report.title} — {report.company}

**URL:** {report.listing_url}
**Legitimacy:** {report.legitimacy}
**Date:** {report.date}
**Global Score:** {report.global_score}/5

---

## CV Match

{report.cv_match_bullets}

**{report.cv_match_summary}**

---

## North Star Alignment

{report.north_star}

---

## Comp

- **Stated range:** {report.comp_stated_range}
- **Company rep:** {report.comp_company_rep}
- **Location context:** {report.comp_location_context}
- **Assessment:** {report.comp_assessment}

---

## Cultural Signals

- **Remote policy:** {report.cultural_remote_policy}
- **Company size:** {report.cultural_company_size}
- **Engineering culture:** {report.cultural_engineering}
- **Timezone:** {report.cultural_timezone}

---

## Red Flags

{red_flags_md}

---

## Global Score Rationale

**{report.global_score}/5:** {report.global_score_rationale}

---

## Posting Legitimacy

{report.posting_legitimacy_detail}

**Verdict: {report.posting_legitimacy_verdict}**
""".strip()


def _safe_stem(title: str, company: str) -> str:
    """Turn a job title + company into a safe filename stem."""
    raw = f"{company}-{title}".lower()
    return re.sub(r"[^a-z0-9]+", "-", raw).strip("-")[:80]


def _legacy_report_path(title: str, company: str, output_dir: Path) -> Path:
    return output_dir / f"{_safe_stem(title, company)}.md"


def _url_report_path(title: str, company: str, url: str, output_dir: Path) -> Path:
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:8]
    return output_dir / f"{_safe_stem(title, company)}-{url_hash}.md"


def report_path_for_listing(scored: ScoredListing, output_dir: Path = REPORTS_DIR) -> Path:
    """Return the canonical report path for a listing, preserving matching legacy files."""
    listing = scored.listing
    legacy_path = _legacy_report_path(listing.title, listing.company, output_dir)
    if legacy_path.exists():
        try:
            if listing.url in legacy_path.read_text(encoding="utf-8"):
                return legacy_path
        except OSError:
            pass
    return _url_report_path(listing.title, listing.company, listing.url, output_dir)


def write_report_to_disk(report: DetailedReport, output_dir: Path = REPORTS_DIR) -> Path:
    """Write a DetailedReport as a markdown file and return the path."""
    output_dir.mkdir(parents=True, exist_ok=True)
    path = _url_report_path(report.title, report.company, report.listing_url, output_dir)
    path.write_text(render_report_markdown(report), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Batch generation
# ---------------------------------------------------------------------------

def batch_generate_reports(
    client: OpenAI,
    evaluated: list[tuple[ScoredListing, LLMEvaluation]],
    criteria: dict,
    cv_text: str,
    profile_text: str,
    *,
    model: str = "gpt-4o",
    max_tokens: int = DEFAULT_REPORT_MAX_TOKENS,
    top_n: int | None = None,
    min_llm_score: int = 5,
    output_dir: Path = REPORTS_DIR,
    overwrite_existing: bool = False,
) -> dict[str, Path]:
    """
    Generate detailed markdown reports for evaluated listings above the score floor.

    Only generates reports for listings with fit_score >= min_llm_score (default 5).
    Returns a URL -> report path mapping for generated or reused report files.
    """
    report_pool = evaluated if top_n is None else evaluated[:top_n]
    targets = [pair for pair in report_pool if pair[1].fit_score >= min_llm_score]
    if not targets:
        console.print(
            f"  [yellow]No listings scored ≥ {min_llm_score}/10 — skipping detailed reports.[/]"
        )
        return {}
    paths: dict[str, Path] = {}

    for i, (scored, evaluation) in enumerate(targets, 1):
        existing_path = report_path_for_listing(scored, output_dir)
        if existing_path.exists() and not overwrite_existing:
            paths[scored.listing.url] = existing_path
            console.print(
                f"  Reusing report {i}/{len(targets)}: "
                f"[bold]{scored.listing.title}[/] @ {scored.listing.company} "
                f"([dim]{existing_path}[/])"
            )
            continue

        console.print(
            f"  Generating report {i}/{len(targets)}: "
            f"[bold]{scored.listing.title}[/] @ {scored.listing.company}..."
        )
        try:
            report = generate_detailed_report(
                client,
                scored,
                evaluation,
                criteria,
                cv_text,
                profile_text,
                model=model,
                max_tokens=max_tokens,
            )
            path = write_report_to_disk(report, output_dir)
            paths[scored.listing.url] = path
            console.print(f"    [green]✓[/] Written to [bold]{path}[/]")
        except Exception as exc:
            console.print(
                f"    [yellow]Warning:[/] report generation failed for "
                f"{scored.listing.url}: {exc}"
            )

    return paths
