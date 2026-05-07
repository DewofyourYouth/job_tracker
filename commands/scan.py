"""
`scan` CLI command — orchestrates the full job discovery pipeline.

Usage:
  python entrypoint.py scan [OPTIONS]

Requires data/scoring_criteria.yaml to exist (generate with `generate-criteria`).
CLI weight/tolerance flags override the YAML values for one-off experiments.

Full pipeline:
  1. Load scoring_criteria.yaml → base config + all scoring rules.
  2. Load portals.yaml for ingestion sources.
  3. Ingest all enabled sources → list[RawListing].
  4. Rule-based pre-filter + weighted scoring → list[ScoredListing].
  5. Narrow to top N for LLM evaluation.
  6. Optionally fetch descriptions for top N (improves LLM quality).
  7. LLM batch evaluation → list[(ScoredListing, LLMEvaluation)].
  8. Display results with rich; optionally write JSON output.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import click
from openai import OpenAI
from rich.console import Console
from rich.table import Table

from classify.llm import LLMEvaluation, batch_evaluate
from classify.rules import (
    ScoredListing,
    ScoringConfig,
    ScoringTolerances,
    ScoringWeights,
    config_from_criteria,
    load_criteria,
    rank_and_narrow,
    score_listing,
)
from injest.job_boards import fetch_description, ingest_all, load_portals_config

console = Console()

CRITERIA_PATH = Path("data/scoring_criteria.yaml")

# Expose default values for help text only — actual defaults come from YAML.
_W = ScoringWeights()
_T = ScoringTolerances()


@click.command("scan")
# -- Weight overrides (default shown is the dataclass default; actual default comes from YAML) --
@click.option("--weight-role-fit",   default=None, type=float,
              help=f"Override role archetype match weight (YAML default: {_W.role_fit}).")
@click.option("--weight-seniority",  default=None, type=float,
              help=f"Override seniority level weight (YAML default: {_W.seniority}).")
@click.option("--weight-location",   default=None, type=float,
              help=f"Override location/remote weight (YAML default: {_W.location_remote}).")
@click.option("--weight-tech-stack", default=None, type=float,
              help=f"Override tech stack weight (YAML default: {_W.tech_stack}).")
@click.option("--weight-avoid",      default=None, type=float,
              help=f"Override avoid-role penalty weight (YAML default: {_W.avoid_penalty}).")
# -- Tolerance overrides --
@click.option("--min-score",         default=None, type=float,
              help=f"Override min score threshold (YAML default: {_T.min_score_threshold}).")
@click.option("--top-n",             default=None, type=int,
              help=f"Override top-N for LLM (YAML default: {_T.top_n_for_llm}).")
@click.option("--salary-tolerance",  default=None, type=float,
              help="Override salary-below-min tolerance fraction.")
@click.option("--location-override", default=None, type=float,
              help="Override role-fit threshold that forgives a location mismatch.")
# -- Pipeline behaviour --
@click.option("--fetch-descriptions/--no-fetch-descriptions", default=False,
              help="Fetch full job descriptions before LLM evaluation (slower, better quality).")
@click.option("--skip-llm/--no-skip-llm", default=False,
              help="Stop after rule scoring. Useful for tuning weights without spending tokens.")
@click.option("--llm-model",  default="gpt-4o-mini", show_default=True,
              help="OpenAI model to use for evaluation.")
@click.option("--no-cache",   is_flag=True, default=False,
              help="Ignore disk cache and re-evaluate all listings via API.")
@click.option("--output-json", type=click.Path(), default=None,
              help="Write full results JSON to this path (LLM stage required).")
@click.option("--criteria", "criteria_path",
              default=str(CRITERIA_PATH), show_default=True,
              type=click.Path(),
              help="Path to scoring_criteria.yaml (generate with generate-criteria).")
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
    no_cache: bool,
    output_json: str | None,
    criteria_path: str,
    portals_path: str,
) -> None:
    """Scan job boards, score listings, and evaluate top results with LLM."""

    criteria = load_criteria(Path(criteria_path))
    config = config_from_criteria(criteria)

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

    portals_config = load_portals_config(Path(portals_path))

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
    top, rest = rank_and_narrow(raw_listings, criteria, config)
    survived_cut = [s for s in rest if not s.disqualified]
    disqualified = [s for s in rest if s.disqualified]
    console.print(
        f"  [bold]{len(raw_listings)}[/] raw → "
        f"[green]{len(top)}[/] for LLM, "
        f"[yellow]{len(survived_cut)}[/] surviving (cut by top-N), "
        f"[red]{len(disqualified)}[/] disqualified"
    )

    if fetch_descriptions and top:
        console.print("  Fetching descriptions for top listings...")
        for i, s in enumerate(top):
            try:
                updated_listing = fetch_description(s.listing)
                top[i] = score_listing(updated_listing, criteria, config)
            except Exception as e:
                console.print(f"  [yellow]Warning:[/] couldn't fetch description: {e}")
        top.sort(key=lambda s: s.total_score, reverse=True)

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
    client = OpenAI()
    evaluated = batch_evaluate(client, top, criteria, model=llm_model, use_cache=not no_cache)
    display_results(evaluated)

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
    table.add_column("Company", style="bold", max_width=22)
    table.add_column("Title", max_width=38)
    table.add_column("Location", max_width=20)
    table.add_column("Rec", width=6)
    table.add_column("Red Flags", max_width=42)

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
    table.add_column("Company", style="bold", max_width=22)
    table.add_column("Title", max_width=38)
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
            "weights": {
                "role_fit": config.weights.role_fit,
                "seniority": config.weights.seniority,
                "location_remote": config.weights.location_remote,
                "tech_stack": config.weights.tech_stack,
                "avoid_penalty": config.weights.avoid_penalty,
            },
            "tolerances": {
                "min_score_threshold": config.tolerances.min_score_threshold,
                "top_n_for_llm": config.tolerances.top_n_for_llm,
                "salary_below_min_tolerance_pct": config.tolerances.salary_below_min_tolerance_pct,
                "location_override_role_fit": config.tolerances.location_override_role_fit,
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
