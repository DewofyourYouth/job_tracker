"""
`evaluate` command — score and LLM-evaluate a specific job posting URL.

Fetches the listing directly from the URL, runs it through the rule scorer,
then the LLM evaluator, and displays the result. Useful for evaluating jobs
found outside the pipeline portals (e.g. from a LinkedIn tip or direct referral).

Rules disqualification is shown as a warning but does not block LLM evaluation —
you asked for it explicitly.
"""

from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import click
import httpx
import yaml
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from classify.llm import DEFAULT_LLM_MAX_TOKENS, evaluate_listing
from classify.rules import (
    DEFAULT_TUNING_PATH,
    RawListing,
    ScoredListing,
    config_from_criteria,
    load_criteria,
    load_tuning_config,
    score_listing,
)
from commands.pipeline import _extract_candidate_summary
from commands.scan import CRITERIA_PATH, display_results, load_csv_index, upsert_listings_csv
from providers import get_client

console = Console()

_FETCH_TIMEOUT = 15
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

PROFILE_PATH = Path("data/profile.yaml")


# ---------------------------------------------------------------------------
# URL → RawListing fetchers
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def _company_from_url(url: str) -> str:
    parsed = urlparse(url)
    hostname = parsed.hostname or ""
    path_parts = [p for p in parsed.path.strip("/").split("/") if p]
    ats_hosts = {
        "job-boards.greenhouse.io", "boards.greenhouse.io",
        "job-boards.eu.greenhouse.io",
        "jobs.lever.co", "jobs.ashbyhq.com", "apply.workable.com",
    }
    if hostname in ats_hosts and path_parts:
        return path_parts[0].replace("-", " ").title()
    parts = hostname.split(".")
    return parts[-2].title() if len(parts) >= 2 else hostname


def _fetch_greenhouse(url: str) -> RawListing:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 3 and parts[-2] == "jobs":
        board_slug = parts[0]
        job_id = parts[-1]
        api_url = f"https://boards-api.greenhouse.io/v1/boards/{board_slug}/jobs/{job_id}"
        resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        loc = data.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else None
        return RawListing(
            title=data.get("title", "Unknown"),
            company=_company_from_url(url),
            url=url,
            location=location,
            source="manual",
            description=_strip_html(data.get("content", "")),
            raw=data,
        )
    return _fetch_html(url)


def _fetch_lever(url: str) -> RawListing:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2:
        slug, uuid = parts[0], parts[1]
        api_url = f"https://api.lever.co/v0/postings/{slug}/{uuid}"
        resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        categories = data.get("categories", {})
        location = categories.get("location") or (
            (categories.get("allLocations") or [None])[0]
        )
        text_parts = [_strip_html(data.get("description", ""))]
        for section in data.get("lists", []):
            text_parts.append(section.get("text", ""))
            for item in section.get("content", []):
                text_parts.append(f"- {_strip_html(item)}")
        return RawListing(
            title=data.get("text", "Unknown"),
            company=_company_from_url(url),
            url=data.get("hostedUrl", url),
            location=location,
            source="manual",
            description="\n".join(p for p in text_parts if p),
            raw=data,
        )
    return _fetch_html(url)


def _fetch_ashby(url: str) -> RawListing:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 2:
        company_slug, job_id = parts[0], parts[1]
        api_url = (
            f"https://api.ashbyhq.com/posting-api/job-board/{company_slug}/posting/{job_id}"
        )
        try:
            resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            job = data.get("job", data)
            workplace = job.get("workplaceType", "")
            loc = job.get("location") or ""
            _WORKPLACE_LABELS = {
                "Remote": "Remote",
                "OnSite": "Onsite",
                "Hybrid": "Hybrid",
            }
            label = _WORKPLACE_LABELS.get(workplace, "")
            if label and loc:
                location: str | None = f"{loc} ({label})"
            elif label:
                location = label
            else:
                location = loc or None
            desc_html = job.get("descriptionHtml") or job.get("description") or ""
            return RawListing(
                title=job.get("title", "Unknown"),
                company=company_slug.replace("-", " ").title(),
                url=url,
                location=location,
                source="manual",
                description=_strip_html(desc_html),
                raw=job,
            )
        except Exception:
            pass
    return _fetch_html(url)


def _fetch_workable(url: str) -> RawListing:
    parsed = urlparse(url)
    parts = [p for p in parsed.path.strip("/").split("/") if p]
    if len(parts) >= 3 and parts[1] == "j":
        company_slug, shortcode = parts[0], parts[2]
        api_url = (
            f"https://apply.workable.com/api/v3/accounts/{company_slug}/jobs/{shortcode}"
        )
        try:
            resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            loc = data.get("location") or {}
            city = loc.get("city") if isinstance(loc, dict) else None
            country = loc.get("country") if isinstance(loc, dict) else None
            remote_str = "Remote" if data.get("remote") else None
            location = ", ".join(p for p in [city, country, remote_str] if p) or None
            desc_html = data.get("description") or data.get("full_description") or ""
            return RawListing(
                title=data.get("title", "Unknown"),
                company=company_slug.replace("-", " ").title(),
                url=url,
                location=location,
                source="manual",
                description=_strip_html(desc_html),
                raw=data,
            )
        except Exception:
            pass
    return _fetch_html(url)


def _fetch_html(url: str) -> RawListing:
    """Fallback: fetch the HTML page and extract what we can."""
    resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    html = resp.text
    title_match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    raw_title = _strip_html(title_match.group(1)) if title_match else "Unknown Position"
    # Strip common "Job Title | Company - Jobs" suffixes from page titles
    for sep in [" | ", " — ", " - ", " at "]:
        if sep in raw_title:
            raw_title = raw_title.split(sep)[0].strip()
            break
    return RawListing(
        title=raw_title or "Unknown Position",
        company=_company_from_url(url),
        url=url,
        source="manual",
        description=_strip_html(html)[:4000],
    )


def fetch_listing_from_url(url: str) -> RawListing:
    """Fetch a RawListing from a job posting URL, using the ATS API where possible."""
    hostname = urlparse(url).hostname or ""
    if "greenhouse.io" in hostname:
        return _fetch_greenhouse(url)
    if "lever.co" in hostname:
        return _fetch_lever(url)
    if "ashbyhq.com" in hostname:
        return _fetch_ashby(url)
    if "workable.com" in hostname:
        return _fetch_workable(url)
    return _fetch_html(url)


# ---------------------------------------------------------------------------
# Rule violations display
# ---------------------------------------------------------------------------

_VIOLATION_THRESHOLDS: dict[str, float] = {
    "location_remote": 0.6,
    "avoid_penalty": 1.0,
    "role_fit": 0.3,
    "seniority": 0.01,  # 0.0 means total mismatch
}

_CRITERION_LABELS: dict[str, str] = {
    "location_remote": "Location",
    "avoid_penalty": "Avoid-title penalty",
    "role_fit": "Role fit",
    "seniority": "Seniority",
    "tech_stack": "Tech stack",
}


def _display_rule_violations(scored: ScoredListing) -> None:
    lines: list[str] = []

    if scored.disqualified:
        lines.append(f"DISQUALIFIED: {scored.disqualify_reason}")

    for name, threshold in _VIOLATION_THRESHOLDS.items():
        cs = scored.criteria.get(name)
        if cs is None:
            continue
        if cs.raw_score < threshold:
            label = _CRITERION_LABELS.get(name, name)
            lines.append(f"{label}: {cs.raw_score:.2f} — {cs.reason}")

    if not lines:
        return

    body = Text()
    for i, line in enumerate(lines):
        if i:
            body.append("\n")
        body.append(line, style="bold yellow")

    console.print(
        Panel(
            body,
            title="[bold red] RULES OVERRIDDEN [/bold red]",
            border_style="red",
            padding=(0, 1),
        )
    )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("evaluate")
@click.argument("urls", nargs=-1, required=True, metavar="URL...")
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Re-evaluate via API even if a cached result exists.",
)
@click.option(
    "--model",
    default="gpt-4o",
    show_default=True,
    help="LLM model to use for evaluation.",
)
@click.option(
    "--save/--no-save",
    default=True,
    show_default=True,
    help="Save the result to data/listings.csv.",
)
@click.option(
    "--criteria",
    "criteria_path",
    default=str(CRITERIA_PATH),
    show_default=True,
    type=click.Path(),
    help="Path to scoring-criteria.yaml.",
)
@click.option(
    "--title",
    default=None,
    help="Override the job title (useful when the page can't be scraped).",
)
@click.option(
    "--company",
    default=None,
    help="Override the company name.",
)
@click.option(
    "--location",
    default=None,
    help="Override the location string (e.g. 'Tel Aviv (Hybrid)').",
)
@click.option(
    "--description",
    "description_text",
    default=None,
    help="Supply the job description text directly, bypassing URL scraping. Pass '-' to read from stdin.",
)
def evaluate_command(
    urls: tuple[str, ...],
    no_cache: bool,
    model: str,
    save: bool,
    criteria_path: str,
    title: str | None,
    company: str | None,
    location: str | None,
    description_text: str | None,
) -> None:
    """Fetch and evaluate one or more job posting URLs directly."""
    import sys

    if description_text == "-":
        description_text = sys.stdin.read()

    criteria = load_criteria(Path(criteria_path))
    tuning = load_tuning_config(DEFAULT_TUNING_PATH)
    config = config_from_criteria(criteria, tuning)

    profile_text = PROFILE_PATH.read_text() if PROFILE_PATH.exists() else ""
    candidate_summary = _extract_candidate_summary(profile_text)

    cost_cfg_path = Path("data/api-cost-config.yaml")
    cost_cfg = yaml.safe_load(cost_cfg_path.read_text()) if cost_cfg_path.exists() else {}
    client = get_client(cost_cfg, stage="llm_evaluation")

    evaluated: list[tuple] = []

    csv_index = load_csv_index()

    for url in urls:
        console.print(f"\n[bold cyan]Fetching:[/] {url}")

        if csv_index.get(url, {}).get("Status") == "dead":
            console.print("  [yellow]Skipped:[/] previously returned 404 — URL is marked dead.")
            continue

        if description_text is not None:
            listing = RawListing(
                title=title or "Unknown Position",
                company=company or _company_from_url(url),
                url=url,
                source="manual",
                location=location,
                description=description_text,
            )
            console.print("  [dim](description supplied manually — skipping URL fetch)[/]")
        else:
            try:
                listing = fetch_listing_from_url(url)
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 404:
                    console.print("  [red]404 Not Found[/] — marking URL as dead (will skip in future runs).")
                    upsert_listings_csv(dead_urls={url})
                else:
                    console.print(f"  [red]HTTP {exc.response.status_code}:[/] {exc}")
                continue
            except Exception as exc:
                console.print(f"  [red]Error:[/] {exc}")
                continue

            if title:
                listing.title = title
            if company:
                listing.company = company
            if location:
                listing.location = location

        console.print(
            f"  [bold]{listing.title}[/] @ {listing.company}"
            + (f"  [{listing.location}]" if listing.location else "")
        )

        scored = score_listing(listing, criteria, config)
        console.print(f"  Rule score: [bold]{scored.total_score:.3f}[/]  ", end="")
        for name, c in scored.criteria.items():
            console.print(f"{name}={c.raw_score:.2f} ", end="")
        console.print()

        _display_rule_violations(scored)

        console.print(f"  [dim]LLM evaluation ({model})...[/]")
        evaluation = evaluate_listing(
            client,
            scored,
            criteria,
            model=model,
            use_cache=not no_cache,
            candidate_summary=candidate_summary,
        )
        evaluated.append((scored, evaluation))

    if not evaluated:
        return

    console.print()
    display_results(evaluated)

    if save:
        upsert_listings_csv(evaluated=evaluated)
