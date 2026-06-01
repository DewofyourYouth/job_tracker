"""Analyze job search performance and generate career coaching recommendations."""

from __future__ import annotations

import csv
import json
from collections import Counter
from datetime import date, datetime, timezone
from pathlib import Path

import click
import yaml
from rich.console import Console

console = Console()

APPLICATIONS_CSV = Path("data/applications.csv")
CV_PATH = Path("data/cv.md")
PROFILE_PATH = Path("data/profile.yaml")
API_COST_CONFIG_PATH = Path("data/api-cost-config.yaml")
REPORTS_DIR = Path("output/reports")

DEFAULT_ANALYZE_MODEL = "gpt-4.1"
DEFAULT_ANALYZE_MAX_TOKENS = 3000

STATUS_ORDER = ["applied", "phone_screen", "interview", "offer", "rejected", "withdrew"]


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def _load_applications() -> list[dict]:
    if not APPLICATIONS_CSV.exists():
        return []
    with APPLICATIONS_CSV.open(encoding="utf-8", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("Company", "").strip()]


def _build_funnel_summary(apps: list[dict]) -> str:
    counts = Counter(r.get("Status", "").strip().lower() for r in apps)
    total = len(apps)
    lines = [f"Total applications: {total}"]
    for status in STATUS_ORDER:
        n = counts.get(status, 0)
        if n:
            pct = f" ({n/total:.0%} of total)" if status != "applied" else ""
            lines.append(f"  {status.replace('_', ' ').title()}: {n}{pct}")
    return "\n".join(lines)


def _build_source_breakdown(apps: list[dict]) -> str:
    by_source: dict[str, Counter] = {}
    for r in apps:
        src = r.get("Source", "").strip() or "Direct/Unknown"
        status = r.get("Status", "").strip().lower()
        if src not in by_source:
            by_source[src] = Counter()
        by_source[src][status] += 1

    lines = []
    for src, counts in sorted(by_source.items(), key=lambda x: -sum(x[1].values())):
        total = sum(counts.values())
        interviews = counts.get("interview", 0) + counts.get("phone_screen", 0)
        offers = counts.get("offer", 0)
        lines.append(
            f"  {src}: {total} applied, {interviews} reached interview, {offers} offers"
        )
    return "\n".join(lines) if lines else "  No source data."


def _build_rejection_notes(apps: list[dict]) -> str:
    rejections = [
        r for r in apps
        if r.get("Status", "").strip().lower() == "rejected" and r.get("Notes", "").strip()
    ]
    if not rejections:
        return "  No rejection notes recorded."
    lines = []
    for r in rejections:
        company = r.get("Company", "")
        title = r.get("Job Title", "")
        note = r.get("Notes", "").strip()
        lines.append(f"  - {company} ({title}): {note}")
    return "\n".join(lines)


def _build_interview_detail(apps: list[dict]) -> str:
    active = [
        r for r in apps
        if r.get("Status", "").strip().lower() in ("phone_screen", "interview", "offer")
    ]
    if not active:
        return "  None currently active."
    lines = []
    for r in active:
        status = r.get("Status", "").strip().lower().replace("_", " ")
        lines.append(
            f"  - {r.get('Company', '')} ({r.get('Job Title', '')}): {status}"
            + (f" — {r.get('Notes','').strip()}" if r.get("Notes","").strip() else "")
        )
    return "\n".join(lines)


def _build_role_breakdown(apps: list[dict]) -> str:
    """Summarise which job titles are getting traction vs. stalling."""
    title_status: dict[str, list[str]] = {}
    for r in apps:
        title = r.get("Job Title", "Unknown").strip()
        status = r.get("Status", "").strip().lower()
        title_status.setdefault(title, []).append(status)

    lines = []
    for title, statuses in sorted(title_status.items()):
        interviews = sum(1 for s in statuses if s in ("phone_screen", "interview", "offer"))
        rejected = statuses.count("rejected")
        applied = len(statuses)
        lines.append(f"  {title}: {applied} applied, {interviews} interviews, {rejected} rejected")
    return "\n".join(lines) if lines else "  No role data."


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are an expert career coach and job search strategist. You will be given a \
candidate's full CV, profile, and detailed job search history including \
application statuses, rejection notes, and interview outcomes.

Your job is to produce a frank, specific, actionable analysis. Do not be vague \
or generic. Every recommendation must be grounded in the specific data provided.

Respond with valid JSON matching this schema:
{
  "headline": "One-sentence overall assessment of where the search stands",
  "funnel_analysis": "2-3 paragraphs on conversion rates and where candidates drop off",
  "whats_working": ["bullet 1", "bullet 2", ...],
  "whats_not_working": ["bullet 1", "bullet 2", ...],
  "positioning_gaps": ["specific gap or mismatch identified from rejection data", ...],
  "quick_wins": ["actionable change that could improve results within 1 week", ...],
  "strategic_recommendations": ["longer-term positioning or targeting change", ...],
  "cv_cover_letter_notes": "Specific observations on how application materials could be stronger",
  "where_to_focus": "Which role types, companies, or channels deserve more attention and why"
}
"""


def _build_user_prompt(
    cv_text: str,
    profile_text: str,
    apps: list[dict],
) -> str:
    today = date.today().isoformat()
    funnel = _build_funnel_summary(apps)
    sources = _build_source_breakdown(apps)
    rejections = _build_rejection_notes(apps)
    interviews = _build_interview_detail(apps)
    roles = _build_role_breakdown(apps)

    # Recent timeline — last 30 days
    recent = []
    for r in apps:
        d = r.get("Applied Date", "").strip()
        if d:
            try:
                days_ago = (date.today() - date.fromisoformat(d)).days
                if days_ago <= 30:
                    recent.append(
                        f"  {d}: {r.get('Company','')} — {r.get('Job Title','')} "
                        f"[{r.get('Status','').strip()}]"
                    )
            except ValueError:
                pass
    recent_str = "\n".join(sorted(recent, reverse=True)) or "  No recent activity with dates."

    return f"""\
Today: {today}

## Candidate CV
{cv_text[:4000].strip()}

## Candidate Profile
{profile_text[:2000].strip()}

## Application Funnel
{funnel}

## By Source / Channel
{sources}

## Role Types Applied To
{roles}

## Active Interviews / Offers
{interviews}

## Rejection Notes (where provided)
{rejections}

## Activity — Last 30 Days
{recent_str}

Analyze this job search. Be specific and direct. Reference actual companies, \
role titles, and rejection notes where relevant. Do not pad with generic advice.
"""


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render_report(data: dict, apps: list[dict]) -> str:
    today = date.today().isoformat()
    total = len(apps)
    active = sum(1 for r in apps if r.get("Status","").lower() in ("phone_screen","interview","offer"))
    rejected = sum(1 for r in apps if r.get("Status","").lower() == "rejected")

    def bullets(items: list) -> str:
        return "\n".join(f"- {i}" for i in items) if items else "_None identified._"

    return f"""# Job Search Analysis — {today}

**{data.get('headline', '')}**

---

## Funnel

{_build_funnel_summary(apps)}

{data.get('funnel_analysis', '')}

---

## What's Working

{bullets(data.get('whats_working', []))}

---

## What's Not Working

{bullets(data.get('whats_not_working', []))}

---

## Positioning Gaps

{bullets(data.get('positioning_gaps', []))}

---

## Quick Wins (this week)

{bullets(data.get('quick_wins', []))}

---

## Strategic Recommendations

{bullets(data.get('strategic_recommendations', []))}

---

## Application Materials

{data.get('cv_cover_letter_notes', '_No observations._')}

---

## Where to Focus

{data.get('where_to_focus', '_No recommendation._')}
""".strip()


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@click.command("analyze")
@click.option("--model", default=None, help="Override LLM model.")
@click.option(
    "--output",
    default=None,
    type=click.Path(),
    help="Override output path (default: output/reports/analysis-YYYY-MM-DD.md).",
)
@click.option("--print-only", is_flag=True, help="Print report to stdout, don't write to disk.")
def analyze_command(model, output, print_only):
    """Analyze job search performance and get career coaching recommendations.

    Reads data/applications.csv, your CV, and profile, then calls the LLM to
    produce a frank analysis of what's working, what isn't, positioning gaps,
    and specific next steps.
    """
    from providers import get_client, validate_provider
    from commands.auth import AuthError

    if not APPLICATIONS_CSV.exists():
        console.print("[red]No applications.csv found.[/] Run [bold]job track add[/] to start tracking.")
        raise click.Abort()

    apps = _load_applications()
    if not apps:
        console.print("[yellow]No applications found in applications.csv.[/]")
        raise click.Abort()

    if not CV_PATH.exists():
        console.print(f"[red]CV not found at {CV_PATH}[/]")
        raise click.Abort()
    if not PROFILE_PATH.exists():
        console.print(f"[red]Profile not found at {PROFILE_PATH}[/]")
        raise click.Abort()

    cost_config = {}
    if API_COST_CONFIG_PATH.exists():
        cost_config = yaml.safe_load(API_COST_CONFIG_PATH.read_text(encoding="utf-8")) or {}

    try:
        validate_provider(cost_config, stage="report_generation")
    except AuthError as exc:
        console.print(f"[red]{exc}[/]")
        raise click.Abort() from exc

    analyze_cfg = cost_config.get("analyze", {})
    resolved_model = model or analyze_cfg.get("model", DEFAULT_ANALYZE_MODEL)
    resolved_max_tokens = int(analyze_cfg.get("max_tokens", DEFAULT_ANALYZE_MAX_TOKENS))

    cv_text = CV_PATH.read_text(encoding="utf-8")
    profile_text = PROFILE_PATH.read_text(encoding="utf-8")

    console.print(f"  Analyzing {len(apps)} applications with [bold]{resolved_model}[/]...")

    client = get_client(cost_config, stage="report_generation")
    raw = client.chat(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": _build_user_prompt(cv_text, profile_text, apps)},
        ],
        model=resolved_model,
        max_tokens=resolved_max_tokens,
        json_mode=True,
    ) or "{}"

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        console.print("[red]LLM returned unparseable response.[/]")
        raise click.Abort()

    report = _render_report(data, apps)

    if print_only:
        console.print(report)
        return

    out_path = Path(output) if output else REPORTS_DIR / f"analysis-{date.today().isoformat()}.md"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(report, encoding="utf-8")
    console.print(f"  [green]✓[/] Analysis → [bold]{out_path}[/]")
    console.print()

    # Print headline and quick wins to console for immediate value
    console.print(f"[bold]{data.get('headline', '')}[/]\n")
    if data.get("quick_wins"):
        console.print("[bold cyan]Quick wins:[/]")
        for item in data["quick_wins"]:
            console.print(f"  • {item}")
