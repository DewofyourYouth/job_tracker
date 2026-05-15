"""
`scan` CLI command — orchestrates the full job discovery pipeline.

Usage:
  python entrypoint.py scan [OPTIONS]

Requires data/scoring_criteria.yaml to exist (generate with `generate-criteria`).
CLI weight/tolerance flags override the YAML values for one-off experiments.

Full pipeline:
  1. Load scoring_criteria.yaml + optional scoring_tuning.yaml.
  2. Load portals.yaml for ingestion sources.
  3. Ingest all enabled sources → list[RawListing].
  4. Rule-based pre-filter + weighted scoring → list[ScoredListing].
  5. Narrow to top N for LLM evaluation.
  6. Optionally fetch descriptions for top N (improves LLM quality).
  7. LLM batch evaluation → list[(ScoredListing, LLMEvaluation)].
  8. Display results with rich; optionally write JSON output.
"""



import csv
import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import click
import yaml
from rich.console import Console
from rich.table import Table

from classify.llm import DEFAULT_LLM_CONCURRENCY, LLMEvaluation, batch_evaluate
from classify.rules import (
    DEFAULT_TUNING_PATH,
    RawListing,
    ScoredListing,
    ScoringConfig,
    ScoringTolerances,
    ScoringWeights,
    config_from_criteria,
    load_criteria,
    load_tuning_config,
    rank_and_narrow,
    score_listing,
)
from injest.job_boards import fetch_description, ingest_all, load_portals_config
from providers import get_client

console = Console()

CRITERIA_PATH = Path("data/scoring_criteria.yaml")
LISTINGS_CSV_PATH = Path("data/listings.csv")
_CSV_FIELDS = [
    "Company",
    "Job Title",
    "Url",
    "Location",
    "Department",
    "Employment Type",
    "Workplace Type",
    "Salary",
    "Description",
    "Date Posted",
    "Source",
    "First Seen",
    "Last Seen",
    "Rule Score",
    "Score",
    "Recommendation",
    "Fit Summary",
    "Strengths",
    "Red Flags",
    "Report Path",
]
_DESCRIPTION_PREVIEW_CHARS = 500


def apply_portals_title_filter(criteria: dict, portals_config: dict) -> dict:
    """Use portals.yaml title_filter when generated criteria omitted it.

    Criteria generation predates the broader portal-level filter in some local
    files. Without this merge, the hard title gate falls back to role_fit terms,
    which is intentionally narrow and can discard good adjacent roles before
    scoring.
    """
    if criteria.get("title_filter"):
        return criteria

    title_filter = portals_config.get("title_filter")
    if not title_filter:
        return criteria

    merged = dict(criteria)
    merged["title_filter"] = title_filter
    console.print("  [dim]Using title_filter from portals.yaml.[/]")
    return merged


def _blank_csv_row() -> dict[str, str]:
    return {field: "" for field in _CSV_FIELDS}


def _format_csv_value(value: Any) -> str:
    """Convert heterogeneous ATS values into a readable single CSV cell."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        parts = [_format_csv_value(item) for item in value]
        return "; ".join(part for part in parts if part)
    if isinstance(value, dict):
        for key in (
            "name",
            "title",
            "text",
            "label",
            "value",
            "displayName",
            "description",
            "summary",
        ):
            if key in value:
                text = _format_csv_value(value[key])
                if text:
                    return text
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


def _first_nonempty(*values: Any) -> str:
    for value in values:
        text = _format_csv_value(value)
        if text:
            return text
    return ""


def _format_posted_date(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        timestamp = float(value) / 1000 if value > 10_000_000_000 else float(value)
        try:
            return datetime.fromtimestamp(timestamp, timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return _format_csv_value(value)

    text = _format_csv_value(value)
    if len(text) >= 10 and text[4:5] == "-" and text[7:8] == "-":
        return text[:10]
    return text


def _extract_posted_date(raw: dict) -> str:
    for key in (
        "published_at",
        "publishedAt",
        "publishedDate",
        "published_on",
        "datePosted",
        "date_posted",
        "created_at",
        "createdAt",
        "created",
        "firstPublished",
        "openedAt",
        "updated_at",
        "updatedAt",
    ):
        if key in raw:
            text = _format_posted_date(raw[key])
            if text:
                return text
    return ""


def _extract_department(raw: dict) -> str:
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    return _first_nonempty(
        raw.get("department"),
        raw.get("departmentName"),
        raw.get("department_name"),
        raw.get("team"),
        raw.get("teamName"),
        raw.get("team_name"),
        categories.get("department"),
        categories.get("team"),
        raw.get("departments"),
        raw.get("teams"),
    )


def _extract_employment_type(raw: dict) -> str:
    categories = raw.get("categories") if isinstance(raw.get("categories"), dict) else {}
    return _first_nonempty(
        raw.get("employmentType"),
        raw.get("employment_type"),
        raw.get("employmentTypeName"),
        raw.get("workType"),
        raw.get("worktype"),
        raw.get("commitment"),
        categories.get("commitment"),
    )


def _extract_workplace_type(raw: dict) -> str:
    remote = raw.get("remote")
    remote_text = "Remote" if remote is True else ""
    return _first_nonempty(
        raw.get("workplaceType"),
        raw.get("workplace_type"),
        raw.get("workplace"),
        raw.get("remotePolicy"),
        remote_text,
    )


def _extract_salary(raw: dict, salary_hint: str | None) -> str:
    return _first_nonempty(
        salary_hint,
        raw.get("salary"),
        raw.get("salaryRange"),
        raw.get("salary_range"),
        raw.get("compensation"),
        raw.get("compensationTierSummary"),
        raw.get("payRange"),
        raw.get("pay_range"),
        raw.get("baseSalary"),
    )


def _set_csv_value(row: dict[str, str], field: str, value: Any, *, replace: bool = True) -> None:
    text = _format_csv_value(value)
    if text and (replace or not row.get(field)):
        row[field] = text


def _description_preview(description: str | None) -> str:
    text = _format_csv_value(description)
    return text[:_DESCRIPTION_PREVIEW_CHARS]


def _apply_listing_to_row(row: dict[str, str], listing: RawListing, seen_at: str) -> None:
    raw = listing.raw if isinstance(listing.raw, dict) else {}
    _set_csv_value(row, "Company", listing.company)
    _set_csv_value(row, "Job Title", listing.title)
    _set_csv_value(row, "Url", listing.url)
    _set_csv_value(row, "Location", listing.location)
    _set_csv_value(row, "Department", _extract_department(raw))
    _set_csv_value(row, "Employment Type", _extract_employment_type(raw))
    _set_csv_value(row, "Workplace Type", _extract_workplace_type(raw))
    _set_csv_value(row, "Salary", _extract_salary(raw, listing.salary_hint))
    _set_csv_value(row, "Date Posted", _extract_posted_date(raw))
    _set_csv_value(row, "Source", listing.source)

    if not row.get("First Seen"):
        row["First Seen"] = seen_at
    row["Last Seen"] = seen_at

    preview = _description_preview(listing.description)
    if preview and len(preview) > len(row.get("Description", "")):
        row["Description"] = preview


def _apply_scored_to_row(row: dict[str, str], scored: ScoredListing, seen_at: str) -> None:
    _apply_listing_to_row(row, scored.listing, seen_at)
    row["Rule Score"] = f"{scored.total_score:.4f}"


def _join_csv_list(values: list[str]) -> str:
    parts = [_format_csv_value(value) for value in values]
    return "; ".join(part for part in parts if part)


def upsert_listings_csv(
    *,
    raw: list[RawListing] | None = None,
    scored: list[ScoredListing] | None = None,
    evaluated: list[tuple[ScoredListing, LLMEvaluation]] | None = None,
    report_paths: dict[str, str] | None = None,
    csv_path: Path = LISTINGS_CSV_PATH,
) -> None:
    """Upsert job listing data into the CSV. URL is the dedup key.

    Call with any combination of inputs — only the fields each source knows
    about are written; existing values for other fields are preserved.
    """
    seen_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows: dict[str, dict] = {}
    if csv_path.exists():
        with csv_path.open(newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                url = row.get("Url", "")
                if url:
                    rows[url] = {field: row.get(field, "") for field in _CSV_FIELDS}

    def _ensure(listing: RawListing) -> dict[str, str]:
        if listing.url not in rows:
            rows[listing.url] = _blank_csv_row()
        _apply_listing_to_row(rows[listing.url], listing, seen_at)
        return rows[listing.url]

    if raw:
        for listing in raw:
            _ensure(listing)

    if scored:
        for item in scored:
            row = _ensure(item.listing)
            _apply_scored_to_row(row, item, seen_at)

    if evaluated:
        for scored, evaluation in evaluated:
            row = _ensure(scored.listing)
            _apply_scored_to_row(row, scored, seen_at)
            row["Score"] = str(evaluation.fit_score)
            row["Recommendation"] = evaluation.recommendation
            row["Fit Summary"] = _format_csv_value(evaluation.fit_summary)
            row["Strengths"] = _join_csv_list(evaluation.strengths)
            row["Red Flags"] = _join_csv_list(evaluation.red_flags)

    if report_paths:
        for url, path in report_paths.items():
            if url in rows:
                rows[url]["Report Path"] = path

    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=_CSV_FIELDS, quoting=csv.QUOTE_ALL)
        writer.writeheader()
        writer.writerows(rows.values())

    console.print(f"  [dim]listings.csv updated ({len(rows)} entries)[/]")

# Expose built-in fallback values for help text only. Runtime values come from
# scoring_criteria.yaml and are overridden by scoring_tuning.yaml.
_W = ScoringWeights()
_T = ScoringTolerances()


@click.command("scan")
# -- Weight overrides (persistent values come from criteria/tuning YAML) --
@click.option("--weight-role-fit",   default=None, type=float,
              help=f"Override role archetype match weight for this run (fallback: {_W.role_fit}).")
@click.option("--weight-seniority",  default=None, type=float,
              help=f"Override seniority level weight for this run (fallback: {_W.seniority}).")
@click.option("--weight-location",   default=None, type=float,
              help=f"Override location/remote weight for this run (fallback: {_W.location_remote}).")
@click.option("--weight-tech-stack", default=None, type=float,
              help=f"Override tech stack weight for this run (fallback: {_W.tech_stack}).")
@click.option("--weight-avoid",      default=None, type=float,
              help=f"Override avoid-role penalty weight for this run (fallback: {_W.avoid_penalty}).")
# -- Tolerance overrides --
@click.option("--min-score",         default=None, type=float,
              help=f"Override min score threshold for this run (fallback: {_T.min_score_threshold}).")
@click.option("--top-n",             default=None, type=int,
              help=f"Override top-N for LLM for this run (fallback: {_T.top_n_for_llm}).")
@click.option("--salary-tolerance",  default=None, type=float,
              help="Override salary-below-min tolerance fraction.")
@click.option("--location-override", default=None, type=float,
              help="Override role-fit threshold that forgives a location mismatch.")
# -- Pipeline behaviour --
@click.option("--fetch-descriptions/--no-fetch-descriptions", default=False,
              help="Fetch full job descriptions before LLM evaluation (slower, better quality).")
@click.option("--skip-llm/--no-skip-llm", default=False,
              help="Stop after rule scoring. Useful for tuning weights without spending tokens.")
@click.option("--llm-model",  default="gpt-4o", show_default=True,
              help="OpenAI model to use for evaluation.")
@click.option("--llm-concurrency", default=DEFAULT_LLM_CONCURRENCY, show_default=True,
              type=click.IntRange(1, 32),
              help="Concurrent LLM evaluations to run.")
@click.option("--no-cache",   is_flag=True, default=False,
              help="Ignore disk cache and re-evaluate all listings via API.")
@click.option("--output-json", type=click.Path(), default=None,
              help="Write full results JSON to this path (LLM stage required).")
@click.option("--criteria", "criteria_path",
              default=str(CRITERIA_PATH), show_default=True,
              type=click.Path(),
              help="Path to scoring_criteria.yaml (generate with generate-criteria).")
@click.option("--tuning-config", "tuning_path",
              default=str(DEFAULT_TUNING_PATH), show_default=True,
              type=click.Path(),
              help="Path to scoring_tuning.yaml with user-adjustable numeric weights and score ladders.")
@click.option("--portals-config", "portals_path",
              default=str(Path("data/portals.yaml")), show_default=True,
              type=click.Path(exists=True),
              help="Path to portals.yaml config.")
def scan_command(
    weight_role_fit: float | None,
    weight_seniority: float | None,
    weight_location: float | None,
    weight_tech_stack: float | None,
    weight_avoid: float | None,
    min_score: float | None,
    top_n: int | None,
    salary_tolerance: float | None,
    location_override: float | None,
    fetch_descriptions: bool,
    skip_llm: bool,
    llm_model: str,
    llm_concurrency: int,
    no_cache: bool,
    output_json: str | None,
    criteria_path: str,
    tuning_path: str,
    portals_path: str,
) -> None:
    """Scan job boards, score listings, and evaluate top results with LLM."""

    criteria = load_criteria(Path(criteria_path))
    portals_config = load_portals_config(Path(portals_path))
    criteria = apply_portals_title_filter(criteria, portals_config)
    tuning_file = Path(tuning_path)
    tuning_required = tuning_file != DEFAULT_TUNING_PATH
    tuning = load_tuning_config(tuning_file, required=tuning_required)
    config = config_from_criteria(criteria, tuning)

    if weight_role_fit is not None:
        config.weights.role_fit = weight_role_fit
    if weight_seniority is not None:
        config.weights.seniority = weight_seniority
    if weight_location is not None:
        config.weights.location_remote = weight_location
    if weight_tech_stack is not None:
        config.weights.tech_stack = weight_tech_stack
    if weight_avoid is not None:
        config.weights.avoid_penalty = weight_avoid
    if min_score is not None:
        config.tolerances.min_score_threshold = min_score
    if top_n is not None:
        config.tolerances.top_n_for_llm = top_n
    if salary_tolerance is not None:
        config.tolerances.salary_below_min_tolerance_pct = salary_tolerance
    if location_override is not None:
        config.tolerances.location_override_role_fit = location_override

    # -----------------------------------------------------------------------
    # Stage 1: Ingest
    # -----------------------------------------------------------------------
    console.print("[bold cyan]Stage 1/3:[/] Ingesting job listings...")
    raw_listings = ingest_all(portals_config)
    console.print(f"  Found [bold]{len(raw_listings)}[/] raw listings.")

    # -----------------------------------------------------------------------
    # Stage 2: Rule-based filter and rank
    # -----------------------------------------------------------------------
    console.print("[bold cyan]Stage 2/3:[/] Scoring and narrowing...")
    if config.weights.semantic_fit > 0:
        from classify.semantic import scorer_from_criteria

        console.print(
            "  [dim]Loading semantic scorer because semantic_fit weight is enabled.[/]"
        )
        config.semantic_scorer = scorer_from_criteria(criteria)

    top, rest = rank_and_narrow(raw_listings, criteria, config)
    survived_cut = [s for s in rest if not s.disqualified]
    disqualified = [s for s in rest if s.disqualified]
    console.print(
        f"  [bold]{len(raw_listings)}[/] raw → "
        f"[green]{len(top)}[/] for LLM, "
        f"[yellow]{len(survived_cut)}[/] surviving (cut by top-N), "
        f"[red]{len(disqualified)}[/] disqualified"
    )

    # Write all listings that survived scoring to the CSV.
    upsert_listings_csv(scored=top + survived_cut)

    if fetch_descriptions and top:
        for i, s in enumerate(top):
            try:
                updated_listing = fetch_description(s.listing)
                top[i] = score_listing(updated_listing, criteria, config)
            except Exception as e:
                console.print(f"  [yellow]Warning:[/] couldn't fetch description: {e}")
        top.sort(key=lambda s: s.total_score, reverse=True)
        upsert_listings_csv(scored=top)

    # -----------------------------------------------------------------------
    # Stage 3: LLM evaluation
    # -----------------------------------------------------------------------
    if skip_llm:
        console.print("[dim]Skipping LLM stage (--skip-llm).[/]")
        if output_json:
            console.print("[yellow]--output-json requires LLM stage; skipping JSON write.[/]")
        display_scored_only(top)
        return

    console.print(f"[bold cyan]Stage 3/3:[/] LLM evaluation ({llm_model})...")
    _cost_cfg_path = Path("data/api-cost-config.yaml")
    _cost_cfg = yaml.safe_load(_cost_cfg_path.read_text()) if _cost_cfg_path.exists() else {}
    client = get_client(_cost_cfg, stage="llm_evaluation")
    evaluated = batch_evaluate(
        client,
        top,
        criteria,
        model=llm_model,
        use_cache=not no_cache,
        concurrency=llm_concurrency,
    )
    display_results(evaluated)
    upsert_listings_csv(evaluated=evaluated)

    if output_json:
        write_json_output(evaluated, config, Path(output_json))


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def display_results(evaluated: list[tuple[ScoredListing, LLMEvaluation]]) -> None:
    """
    Render evaluated listings as a rich table sorted by fit_score desc,
    then print expanded details for the top 5.
    """
    table = Table(title="Top Job Listings", show_lines=True)
    table.add_column("#", style="dim", width=3)
    table.add_column("Score", width=6)
    table.add_column("Company", style="bold", max_width=22, no_wrap=True)
    table.add_column("Title", max_width=40, no_wrap=True)
    table.add_column("Location", max_width=20, no_wrap=True)
    table.add_column("Rec", width=6)
    table.add_column("Red Flags", max_width=44, no_wrap=True)

    for rank, (scored, evaluation) in enumerate(evaluated, 1):
        score = evaluation.fit_score
        score_style = "bold green" if score >= 8 else ("yellow" if score >= 6 else "red")
        rec_style = {"apply": "green", "maybe": "yellow", "skip": "red"}.get(
            evaluation.recommendation, "white"
        )
        flags = "; ".join(evaluation.red_flags[:2])

        table.add_row(
            str(rank),
            f"[{score_style}]{score}/10[/{score_style}]",
            scored.listing.company,
            scored.listing.title,
            scored.listing.location or "—",
            f"[{rec_style}]{evaluation.recommendation}[/{rec_style}]",
            flags,
        )

    console.print(table)

    console.print("\n[bold]Top 5 — details:[/]")
    for rank, (scored, evaluation) in enumerate(evaluated[:5], 1):
        console.print(
            f"\n[bold cyan]{rank}.[/] [bold]{scored.listing.title}[/] "
            f"@ {scored.listing.company}"
        )
        console.print(f"   [dim]{scored.listing.url}[/]")
        console.print(f"   {evaluation.fit_summary}")
        if evaluation.strengths:
            console.print("   [green]Strengths:[/]")
            for s in evaluation.strengths:
                console.print(f"     + {s}")
        if evaluation.red_flags:
            console.print("   [red]Red flags:[/]")
            for f in evaluation.red_flags:
                console.print(f"     - {f}")


def display_scored_only(scored_listings: list[ScoredListing]) -> None:
    """
    Render rule-scored listings as a table.  Used with --skip-llm to debug
    weight tuning without spending API tokens.
    """
    table = Table(title="Rule-Scored Listings", show_lines=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Total", width=7)
    table.add_column("Company", style="bold", max_width=22, no_wrap=True)
    table.add_column("Title", max_width=40, no_wrap=True)
    table.add_column("role_fit", width=9)
    table.add_column("senior", width=7)
    table.add_column("location", width=9)
    table.add_column("tech", width=6)
    table.add_column("avoid", width=6)

    for rank, s in enumerate(scored_listings, 1):
        def fmt(name: str) -> str:
            c = s.criteria.get(name)
            return f"{c.raw_score:.2f}" if c else "—"

        total_style = "green" if s.total_score >= 0.6 else ("yellow" if s.total_score >= 0.4 else "red")
        table.add_row(
            str(rank),
            f"[{total_style}]{s.total_score:.3f}[/{total_style}]",
            s.listing.company,
            s.listing.title,
            fmt("role_fit"),
            fmt("seniority"),
            fmt("location_remote"),
            fmt("tech_stack"),
            fmt("avoid_penalty"),
        )

    console.print(table)


def write_json_output(
    evaluated: list[tuple[ScoredListing, LLMEvaluation]],
    config: ScoringConfig,
    path: Path,
) -> None:
    data = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "config": {
            "weights": asdict(config.weights),
            "tolerances": asdict(config.tolerances),
            "score_ladders": {
                "role_fit": asdict(config.role_fit_ladder),
                "acceptable_location": asdict(config.acceptable_location_ladder),
                "avoid": asdict(config.avoid_penalty_ladder),
                "tech_stack": asdict(config.tech_stack_ladder),
            },
            "adjustments": {
                "salary": asdict(config.salary_adjustments),
            },
        },
        "results": [
            {
                "rank": rank,
                "listing": {
                    "title": scored.listing.title,
                    "company": scored.listing.company,
                    "url": scored.listing.url,
                    "location": scored.listing.location,
                    "source": scored.listing.source,
                },
                "rule_scores": {
                    name: {
                        "raw_score": c.raw_score,
                        "weighted": c.weighted,
                        "reason": c.reason,
                    }
                    for name, c in scored.criteria.items()
                },
                "total_score": scored.total_score,
                "evaluation": {
                    "fit_score": evaluation.fit_score,
                    "fit_summary": evaluation.fit_summary,
                    "strengths": evaluation.strengths,
                    "red_flags": evaluation.red_flags,
                    "recommendation": evaluation.recommendation,
                    "cached": evaluation.cached,
                },
            }
            for rank, (scored, evaluation) in enumerate(evaluated, 1)
        ],
    }

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))
    console.print(f"  Results written to [bold]{path}[/]")
