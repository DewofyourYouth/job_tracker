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

from pathlib import Path

import click
from openai import OpenAI
from rich.console import Console
from rich.table import Table

from classify.llm import batch_evaluate
from classify.rules import (
    ScoringConfig,
    ScoringTolerances,
    ScoringWeights,
    config_from_criteria,
    load_criteria,
    rank_and_narrow,
)
from injest.job_boards import fetch_description, ingest_all, load_portals_config

console = Console()

CRITERIA_PATH = Path("data/scoring_criteria.yaml")

# Sentinel value used to detect "user did not pass this flag" so we can fall
# back to the YAML value rather than overriding it with the dataclass default.
_UNSET = object()

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
              help="Write full results JSON to this path.")
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

    # -- Load scoring criteria (personal rules) from YAML --
    criteria = load_criteria(Path(criteria_path))

    # Start from YAML-derived config, then apply any CLI overrides.
    config = config_from_criteria(criteria)

    # Apply CLI overrides only for flags the user explicitly passed.
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

    # TODO:
    #   raw_listings = ingest_all(portals_config)
    #   console.print(f"  Found [bold]{len(raw_listings)}[/] raw listings.")

    # -----------------------------------------------------------------------
    # Stage 2: Rule-based filter and rank
    # -----------------------------------------------------------------------
    console.print("[bold cyan]Stage 2/3:[/] Scoring and narrowing...")

    # TODO:
    #   top, rest = rank_and_narrow(raw_listings, criteria, config)
    #   disqualified = [s for s in rest if s.disqualified]
    #   console.print(f"  [green]{len(top)}[/] qualify for LLM | "
    #                 f"[yellow]{len(rest) - len(disqualified)}[/] below threshold | "
    #                 f"[red]{len(disqualified)}[/] hard-disqualified")
    #
    #   if fetch_descriptions:
    #       console.print("  Fetching descriptions for top listings...")
    #       # fetch descriptions then re-score so tech_stack has full text to match against
    #       ...

    # -----------------------------------------------------------------------
    # Stage 3: LLM evaluation
    # -----------------------------------------------------------------------
    if not skip_llm:
        console.print(f"[bold cyan]Stage 3/3:[/] LLM evaluation ({llm_model})...")

        # TODO:
        #   client = OpenAI()  # reads OPENAI_API_KEY from env
        #   evaluated = batch_evaluate(client, top, criteria, model=llm_model, use_cache=not no_cache)
        #   display_results(evaluated)
        #   if output_json:
        #       write_json_output(evaluated, config, Path(output_json))
    else:
        console.print("[dim]Skipping LLM stage (--skip-llm).[/]")
        # TODO: display_scored_only(top)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def display_results(evaluated: list) -> None:
    """
    Render the evaluated listings as a rich table, sorted by fit_score desc.

    Columns: Rank | Score | Company | Title | Location | Rec | Red Flags
    Color coding:
      - fit_score ≥ 8  → green
      - fit_score 6–7  → yellow
      - fit_score ≤ 5  → red
    """
    # TODO: build a rich Table and print it.
    # Also print full fit_summary + red_flags for the top 5 as an expanded view.
    raise NotImplementedError


def display_scored_only(scored_listings: list) -> None:
    """
    Render rule-scored listings (no LLM evaluation) as a table.
    Used with --skip-llm to debug weight tuning without spending API tokens.

    Columns: Rank | Total | Company | Title | role_fit | seniority | location | tech | avoid
    """
    # TODO: build a rich Table showing per-criterion scores.
    raise NotImplementedError


def write_json_output(evaluated: list, path: Path) -> None:
    """
    Write full pipeline results to a JSON file for downstream processing.

    Structure:
      {
        "run_at": "<ISO timestamp>",
        "config": { weights, tolerances },
        "results": [
          {
            "rank": 1,
            "listing": { title, company, url, location, source },
            "rule_scores": { role_fit, seniority, ... },
            "total_score": 0.82,
            "evaluation": { fit_score, fit_summary, strengths, red_flags, recommendation }
          },
          ...
        ]
      }
    """
    # TODO: serialise and write. Use default=str for datetime fields.
    raise NotImplementedError
