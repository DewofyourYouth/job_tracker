"""Generate tailored CV and cover letter HTML for a specific job listing."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

import click
import yaml
from rich.console import Console
from rich.table import Table

from prompts.render import render_prompt

console = Console()

LISTINGS_CSV = Path("data/listings.csv")
CV_PATH = Path("data/cv.md")
PROFILE_PATH = Path("data/profile.yaml")
TEMPLATES_DIR = Path("templates")
APPLICATIONS_DIR = Path("output/applications")
API_COST_CONFIG_PATH = Path("data/api-cost-config.yaml")

DEFAULT_APPLY_MODEL = "gpt-4.1-mini"
DEFAULT_APPLY_MAX_TOKENS = 3500


# ---------------------------------------------------------------------------
# Listing lookup
# ---------------------------------------------------------------------------

def _load_listings() -> list[dict]:
    if not LISTINGS_CSV.exists():
        return []
    with LISTINGS_CSV.open(encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _find_listing_by_url(url: str) -> dict | None:
    for row in _load_listings():
        if row.get("Url") == url:
            return row
    return None


def _pick_listing_interactively() -> dict | None:
    rows = _load_listings()

    # Only surface listings the pipeline has evaluated and not marked skip.
    candidates = [
        r for r in rows
        if r.get("Score") and r.get("Recommendation") in ("apply", "maybe")
    ]
    candidates.sort(key=lambda r: float(r.get("Score") or 0), reverse=True)

    if not candidates:
        console.print(
            "[yellow]No pipeline-evaluated listings found.[/] "
            "Run [bold]job pipeline[/] first to generate recommendations."
        )
        return None

    top = candidates[:20]

    table = Table(title="Pipeline recommendations — select a listing to apply to", show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Fit", width=4, justify="right")
    table.add_column("Rec", width=7)
    table.add_column("Title", min_width=26)
    table.add_column("Company", min_width=16)
    table.add_column("Location", min_width=16)
    table.add_column("Summary", min_width=32)

    for i, row in enumerate(top, 1):
        rec = row.get("Recommendation", "")
        rec_markup = f"[green]{rec}[/]" if rec == "apply" else f"[yellow]{rec}[/]"
        summary = row.get("Fit Summary", "") or ""
        summary_short = summary[:55].rstrip() + ("…" if len(summary) > 55 else "")
        table.add_row(
            str(i),
            row.get("Score", "—"),
            rec_markup,
            row.get("Job Title", ""),
            row.get("Company", ""),
            row.get("Location", "") or "—",
            summary_short,
        )

    console.print(table)
    choice = click.prompt("Select listing number", type=int, default=1)
    if not (1 <= choice <= len(top)):
        console.print("[red]Invalid selection.[/]")
        return None

    return top[choice - 1]


# ---------------------------------------------------------------------------
# LLM content generation
# ---------------------------------------------------------------------------

def generate_application_content(
    client,
    listing: dict,
    cv_text: str,
    profile_text: str,
    *,
    model: str,
    max_tokens: int,
    include_cover_letter: bool = True,
) -> dict:
    description = listing.get("Description", "") or "not available"
    if len(description) > 3000:
        description = description[:3000].strip() + "..."

    user_msg = render_prompt(
        "apply_user.md",
        job_title=listing.get("Job Title", ""),
        company=listing.get("Company", ""),
        location=listing.get("Location", "") or "not specified",
        salary=listing.get("Salary", "") or "not specified",
        description=description,
        fit_summary=listing.get("Fit Summary", "") or "",
        strengths=listing.get("Strengths", "") or "",
        red_flags=listing.get("Red Flags", "") or "",
        cv_text=cv_text,
        profile_text=profile_text,
        include_cover_letter=include_cover_letter,
    )

    raw = client.chat(
        [
            {"role": "system", "content": render_prompt("apply_system.md")},
            {"role": "user", "content": user_msg},
        ],
        model=model,
        max_tokens=max_tokens,
        json_mode=True,
    ) or "{}"
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


# ---------------------------------------------------------------------------
# HTML rendering helpers
# ---------------------------------------------------------------------------

def _esc(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _render_competencies(tags: list) -> str:
    return "\n      ".join(
        f'<div class="competency-tag">{_esc(tag)}</div>' for tag in tags
    )


def _render_experience(jobs: list) -> str:
    blocks = []
    for job in jobs:
        bullets = "\n".join(
            f"        <li>{_esc(b)}</li>" for b in (job.get("bullets") or [])
        )
        blocks.append(
            f'  <div class="job">\n'
            f'    <div class="job-header">\n'
            f'      <span class="job-company">{_esc(job.get("company", ""))}</span>\n'
            f'      <span class="job-period">{_esc(job.get("period", ""))}</span>\n'
            f'    </div>\n'
            f'    <div class="job-role">{_esc(job.get("role", ""))}</div>\n'
            f'    <div class="job-location">{_esc(job.get("location", ""))}</div>\n'
            f'    <ul>\n{bullets}\n    </ul>\n'
            f'  </div>'
        )
    return "\n".join(blocks)


def _render_projects(projects: list) -> str:
    blocks = []
    for p in projects:
        badge = (
            f'<span class="project-badge">{_esc(p["badge"])}</span>'
            if p.get("badge")
            else ""
        )
        tech = (
            f'    <div class="project-tech">{_esc(p["tech"])}</div>\n'
            if p.get("tech")
            else ""
        )
        blocks.append(
            f'  <div class="project">\n'
            f'    <div><span class="project-title">{_esc(p.get("title", ""))}</span>{badge}</div>\n'
            f'    <div class="project-desc">{_esc(p.get("description", ""))}</div>\n'
            f'{tech}'
            f'  </div>'
        )
    return "\n".join(blocks)


def _render_education(items: list) -> str:
    blocks = []
    for e in items:
        blocks.append(
            f'  <div class="edu-item">\n'
            f'    <div class="edu-header">\n'
            f'      <span class="edu-title">{_esc(e.get("degree", ""))}'
            f'&nbsp;<span class="edu-org">{_esc(e.get("institution", ""))}</span></span>\n'
            f'      <span class="edu-year">{_esc(str(e.get("year", "")))}</span>\n'
            f'    </div>\n'
            f'  </div>'
        )
    return "\n".join(blocks)


def _render_certifications(certs: list) -> str:
    blocks = []
    for c in certs:
        blocks.append(
            f'  <div class="cert-item">\n'
            f'    <span class="cert-title">{_esc(c.get("title", ""))}'
            f'&nbsp;<span class="cert-org">{_esc(c.get("issuer", ""))}</span></span>\n'
            f'    <span class="cert-year">{_esc(str(c.get("year", "")))}</span>\n'
            f'  </div>'
        )
    return "\n".join(blocks)


def _render_skills(categories: list) -> str:
    return "\n".join(
        f'  <div class="skill-item">'
        f'<span class="skill-category">{_esc(cat.get("category", ""))}</span>'
        f': {_esc(cat.get("skills", ""))}'
        f'</div>'
        for cat in categories
    )


def _render_cover_letter_body(paragraphs: list) -> str:
    return "\n".join(f"    <p>{_esc(p)}</p>" for p in paragraphs)


# ---------------------------------------------------------------------------
# Template filling
# ---------------------------------------------------------------------------

def _fill_template(template_text: str, tokens: dict[str, str]) -> str:
    for key, value in tokens.items():
        template_text = template_text.replace(f"{{{{{key}}}}}", value)
    return template_text


def render_cv_html(data: dict, profile: dict) -> str:
    candidate = profile.get("candidate", {})
    template = (TEMPLATES_DIR / "cv-template.html").read_text(encoding="utf-8")

    linkedin_url = candidate.get("linkedin", "")
    portfolio_url = candidate.get("portfolio_url", "")

    tokens = {
        "LANG": "en",
        "NAME": candidate.get("full_name", ""),
        "SUBTITLE": data.get("subtitle", ""),
        "PHONE": candidate.get("phone", ""),
        "EMAIL": candidate.get("email", ""),
        "LINKEDIN_URL": linkedin_url,
        "LINKEDIN_DISPLAY": "/" + linkedin_url.split("linkedin.com", 1)[-1].strip("/") if "linkedin.com" in linkedin_url else linkedin_url,
        "PORTFOLIO_URL": portfolio_url,
        "PORTFOLIO_DISPLAY": portfolio_url.replace("https://", "").rstrip("/"),
        "LOCATION": candidate.get("location", ""),
        "PAGE_WIDTH": "210mm",
        "SECTION_SUMMARY": "Professional Summary",
        "SUMMARY_TEXT": data.get("summary", ""),
        "SECTION_COMPETENCIES": "Core Competencies",
        "COMPETENCIES": _render_competencies(data.get("competencies") or []),
        "SECTION_EXPERIENCE": "Professional Experience",
        "EXPERIENCE": _render_experience(data.get("experience") or []),
        "SECTION_PROJECTS": "Projects",
        "PROJECTS": _render_projects(data.get("projects") or []),
        "SECTION_EDUCATION": "Education",
        "EDUCATION": _render_education(data.get("education") or []),
        "SECTION_CERTIFICATIONS": "Certifications",
        "CERTIFICATIONS": _render_certifications(data.get("certifications") or []),
        "SECTION_SKILLS": "Technical Skills",
        "SKILLS": _render_skills(data.get("skills") or []),
    }
    return _fill_template(template, tokens)


def render_cover_letter_html(data: dict, profile: dict, listing: dict) -> str:
    candidate = profile.get("candidate", {})
    template = (TEMPLATES_DIR / "cl-template.html").read_text(encoding="utf-8")
    cl = data.get("cover_letter") or {}

    portfolio_url = candidate.get("portfolio_url", "")
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")

    tokens = {
        "LANG": "en",
        "NAME": candidate.get("full_name", ""),
        "SUBTITLE": data.get("subtitle", ""),
        "PHONE": candidate.get("phone", ""),
        "EMAIL": candidate.get("email", ""),
        "PORTFOLIO_URL": portfolio_url,
        "PORTFOLIO_DISPLAY": portfolio_url.replace("https://", "").rstrip("/"),
        "LOCATION": candidate.get("location", ""),
        "PAGE_WIDTH": "210mm",
        "DATE": date_str,
        "RECIPIENT": cl.get("recipient") or f"Hiring Team, {listing.get('Company', '')}",
        "RE_LINE": cl.get("re_line") or f"{listing.get('Job Title', '')} at {listing.get('Company', '')}",
        "BODY": _render_cover_letter_body(cl.get("body_paragraphs") or []),
    }
    return _fill_template(template, tokens)


# ---------------------------------------------------------------------------
# Output path
# ---------------------------------------------------------------------------

def _output_slug(listing: dict) -> str:
    company = re.sub(r"[^a-z0-9]+", "-", (listing.get("Company") or "").lower()).strip("-")
    title = re.sub(r"[^a-z0-9]+", "-", (listing.get("Job Title") or "").lower()).strip("-")
    url_hash = hashlib.sha256((listing.get("Url") or "").encode()).hexdigest()[:8]
    stem = f"{company}-{title}-{url_hash}"
    return stem[:80]


# ---------------------------------------------------------------------------
# PDF export via Playwright + system Chrome
# ---------------------------------------------------------------------------

def html_to_pdf(
    html_path: Path,
    pdf_path: Path,
    *,
    margin_top: str = "14mm",
    margin_bottom: str = "14mm",
    margin_left: str = "18mm",
    margin_right: str = "18mm",
) -> bool:
    """Convert an HTML file to PDF using Playwright with system Chrome.

    Uses CDP Page.printToPDF so displayHeaderFooter can be set to False —
    the only reliable way to suppress Chrome's date/title/page-number overlay
    in Chrome 112+ where --print-to-pdf-no-header is no longer honoured.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        console.print("[red]playwright is not installed. Run: pip install playwright[/]")
        return False

    with sync_playwright() as p:
        browser = p.chromium.launch(channel="chrome")
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri())
        page.wait_for_load_state("networkidle")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            print_background=True,
            display_header_footer=False,
            margin={
                "top": margin_top,
                "bottom": margin_bottom,
                "left": margin_left,
                "right": margin_right,
            },
        )
        browser.close()

    return pdf_path.exists()


# ---------------------------------------------------------------------------
# Cost config
# ---------------------------------------------------------------------------

def _load_cost_config() -> dict:
    if not API_COST_CONFIG_PATH.exists():
        return {}
    return yaml.safe_load(API_COST_CONFIG_PATH.read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@click.command("apply")
@click.argument("url", required=False)
@click.option("--no-cover-letter", is_flag=True, help="Generate CV only, skip cover letter.")
@click.option("--pdf", is_flag=True, help="Export HTML files to PDF via Chrome headless.")
@click.option("--model", default=None, help="Override LLM model.")
@click.option(
    "--output-dir",
    default=None,
    type=click.Path(),
    help="Override output directory (default: output/applications/<slug>/).",
)
@click.option("--open", "open_browser", is_flag=True, help="Open generated files in browser.")
@click.option("--notes", default=None, help="Extra context appended to the CV before generation (e.g. skills relevant only to this role).")
def apply_command(url, no_cover_letter, pdf, model, output_dir, open_browser, notes):
    """Generate a tailored CV and cover letter for a job listing.

    URL is optional — omit to pick interactively from listings.csv.
    Outputs HTML by default; add --pdf to export PDFs via Chrome headless.
    """
    from providers import get_client, validate_provider
    from commands.auth import AuthError

    cost_config = _load_cost_config()
    try:
        validate_provider(cost_config, stage="apply_generation")
    except AuthError as exc:
        console.print(f"[red]{exc}[/]")
        raise click.Abort() from exc

    # resolve listing
    if url:
        listing = _find_listing_by_url(url)
        if listing is None:
            console.print(f"[yellow]URL not found in listings.csv — proceeding with URL only.[/]")
            listing = {"Url": url, "Job Title": "", "Company": "", "Location": ""}
    else:
        listing = _pick_listing_interactively()
        if listing is None:
            raise click.Abort()

    title = listing.get("Job Title", "") or "(untitled)"
    company = listing.get("Company", "") or "(unknown company)"
    console.print(f"\n[bold]Generating application materials:[/] {title} @ {company}")

    if not CV_PATH.exists():
        console.print(f"[red]CV not found at {CV_PATH}[/]")
        raise click.Abort()
    if not PROFILE_PATH.exists():
        console.print(f"[red]Profile not found at {PROFILE_PATH}[/]")
        raise click.Abort()

    cv_text = CV_PATH.read_text(encoding="utf-8")
    if notes:
        cv_text += f"\n\n## Additional Context (for this application only)\n\n{notes}"
    profile_text = PROFILE_PATH.read_text(encoding="utf-8")
    profile = yaml.safe_load(profile_text) or {}

    apply_cfg = cost_config.get("apply_generation", {})
    resolved_model = model or apply_cfg.get("model", DEFAULT_APPLY_MODEL)
    resolved_max_tokens = int(apply_cfg.get("max_tokens", DEFAULT_APPLY_MAX_TOKENS))
    include_cover_letter = not no_cover_letter

    client = get_client(cost_config, stage="apply_generation")

    console.print(f"  Calling {resolved_model} (max {resolved_max_tokens} tokens)...")
    content = generate_application_content(
        client,
        listing,
        cv_text,
        profile_text,
        model=resolved_model,
        max_tokens=resolved_max_tokens,
        include_cover_letter=include_cover_letter,
    )

    if not content:
        console.print("[red]LLM returned empty or unparseable response.[/]")
        raise click.Abort()

    out_dir = Path(output_dir) if output_dir else APPLICATIONS_DIR / _output_slug(listing)
    out_dir.mkdir(parents=True, exist_ok=True)

    candidate_name = profile.get("candidate", {}).get("full_name", "")
    name_slug = re.sub(r"[^a-z0-9]+", "-", candidate_name.lower()).strip("-") if candidate_name else "cv"

    cv_html = render_cv_html(content, profile)
    cv_path = out_dir / f"{name_slug}-cv.html"
    cv_path.write_text(cv_html, encoding="utf-8")
    console.print(f"  [green]✓[/] CV → [bold]{cv_path}[/]")

    cl_path = None
    if include_cover_letter and content.get("cover_letter"):
        cl_html = render_cover_letter_html(content, profile, listing)
        cl_path = out_dir / f"{name_slug}-cover-letter.html"
        cl_path.write_text(cl_html, encoding="utf-8")
        console.print(f"  [green]✓[/] Cover letter → [bold]{cl_path}[/]")
    elif include_cover_letter:
        console.print("[yellow]  Cover letter section missing from LLM response — skipped.[/]")

    if pdf:
        console.print("  Exporting PDFs via Playwright...")
        cv_pdf = cv_path.with_suffix(".pdf")
        if html_to_pdf(cv_path, cv_pdf):
            console.print(f"  [green]✓[/] CV PDF → [bold]{cv_pdf}[/]")
        else:
            console.print("  [yellow]PDF export failed.[/]")

        if cl_path:
            cl_pdf = cl_path.with_suffix(".pdf")
            if html_to_pdf(cl_path, cl_pdf, margin_top="18mm", margin_bottom="18mm", margin_left="20mm", margin_right="20mm"):
                console.print(f"  [green]✓[/] Cover letter PDF → [bold]{cl_pdf}[/]")
            else:
                console.print("  [yellow]Cover letter PDF export failed.[/]")

    if open_browser:
        if pdf and (cv_path.with_suffix(".pdf")).exists():
            webbrowser.open(cv_path.with_suffix(".pdf").resolve().as_uri())
            if cl_path and (cl_path.with_suffix(".pdf")).exists():
                webbrowser.open(cl_path.with_suffix(".pdf").resolve().as_uri())
        else:
            webbrowser.open(cv_path.resolve().as_uri())
            if cl_path:
                webbrowser.open(cl_path.resolve().as_uri())
