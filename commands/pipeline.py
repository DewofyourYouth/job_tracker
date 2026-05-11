"""
`pipeline` command — end-to-end job report pipeline.

Runs all stages in sequence:
  1. read_profile_and_cv         — load data/cv.md + data/profile.yaml
  2. generate_criteria           — load (or generate) data/scoring_criteria.yaml
  3. scan_for_jobs               — ingest raw listings from portals.yaml sources
  4. eliminate_irrelevant_jobs   — hard-filter: title gate + avoid-role rules
  5. score_relevant_positions    — weighted rule scoring; narrows to top N
  6. analyze_remaining_positions — LLM quick evaluation of top N
  7. generate_reports            — display table + write detailed markdown reports

Numeric scoring tuning is loaded from data/scoring_tuning.yaml during the scan
stage and overrides generated criteria values.

Each stage stores its output on the Pipeline instance so the next stage can
read it. Stages can be skipped or overridden by calling them individually.
"""

from __future__ import annotations

from pathlib import Path

import click
from openai import OpenAI
from rich.console import Console

from classify.llm import DEFAULT_LLM_CONCURRENCY, LLMEvaluation, batch_evaluate
from classify.rules import (
    DEFAULT_TUNING_PATH,
    RawListing,
    ScoredListing,
    ScoringConfig,
    config_from_criteria,
    load_criteria,
    load_tuning_config,
    passes_hard_rules,
    rank_and_narrow,
    score_listing,
)
from commands.generate_criteria import (
    build_system_prompt,
    build_user_message,
    inject_meta,
    strip_fences,
)
from commands.report import REPORTS_DIR, batch_generate_reports
from commands.scan import (
    apply_portals_title_filter,
    display_results,
    upsert_listings_csv,
    write_json_output,
)
from injest.job_boards import fetch_description, ingest_all, load_portals_config

console = Console()

CV_PATH = Path("data/cv.md")
PROFILE_PATH = Path("data/profile.yaml")
CRITERIA_PATH = Path("data/scoring_criteria.yaml")
PORTALS_PATH = Path("data/portals.yaml")


class ReportsPipeline:
    """
    Fluent pipeline for generating a job-fit report.

    Each stage method performs one step and returns self so stages can be
    chained. State flows forward: each stage reads from the previous stage's
    output stored on self.
    """

    def __init__(self) -> None:
        self.cv_text: str = ""
        self.profile_text: str = ""
        self.criteria: dict = {}
        self.tuning: dict = {}
        self.config: ScoringConfig = ScoringConfig()
        self.portals_config: dict = {}
        self.raw_listings: list[RawListing] = []
        self.candidates: list[RawListing] = []   # survives hard filter
        self.top_listings: list[ScoredListing] = []
        self.rest_listings: list[ScoredListing] = []
        self.evaluated: list[tuple[ScoredListing, LLMEvaluation]] = []

    # -------------------------------------------------------------------------
    # Stage 1
    # -------------------------------------------------------------------------

    def read_profile_and_cv(
        self,
        cv_path: Path = CV_PATH,
        profile_path: Path = PROFILE_PATH,
    ) -> ReportsPipeline:
        """Load the candidate CV and profile from disk."""
        self.cv_text = cv_path.read_text()
        self.profile_text = profile_path.read_text()
        console.print("[bold cyan]Stage 1/7:[/] Loaded CV and profile.")
        return self

    # -------------------------------------------------------------------------
    # Stage 2
    # -------------------------------------------------------------------------

    def generate_criteria(
        self,
        criteria_path: Path = CRITERIA_PATH,
        model: str = "gpt-4o",
    ) -> ReportsPipeline:
        """Load existing scoring criteria, or call OpenAI to generate them."""
        if not criteria_path.exists():
            console.print(
                f"[bold cyan]Stage 2/7:[/] Generating scoring criteria "
                f"via OpenAI ({model})..."
            )
            _generate_criteria_file(
                self.cv_text, self.profile_text, criteria_path, model
            )
        else:
            console.print(
                f"[bold cyan]Stage 2/7:[/] Loading criteria from [bold]{criteria_path}[/]."
            )

        self.criteria = load_criteria(criteria_path)
        return self

    # -------------------------------------------------------------------------
    # Stage 3
    # -------------------------------------------------------------------------

    def scan_for_jobs(
        self,
        portals_path: Path = PORTALS_PATH,
        tuning_path: Path = DEFAULT_TUNING_PATH,
    ) -> ReportsPipeline:
        """Fetch raw job listings from all enabled portals.yaml sources."""
        self.portals_config = load_portals_config(portals_path)
        self.criteria = apply_portals_title_filter(self.criteria, self.portals_config)
        self.tuning = load_tuning_config(
            tuning_path,
            required=tuning_path != DEFAULT_TUNING_PATH,
        )
        self.config = config_from_criteria(self.criteria, self.tuning)
        console.print("[bold cyan]Stage 3/7:[/] Scanning job boards...")
        self.raw_listings = ingest_all(self.portals_config)
        # Candidates start as the full raw list; eliminate_irrelevant_jobs narrows them.
        self.candidates = list(self.raw_listings)
        console.print(f"  Found [bold]{len(self.raw_listings)}[/] raw listings.")
        return self

    # -------------------------------------------------------------------------
    # Stage 4
    # -------------------------------------------------------------------------

    def eliminate_irrelevant_jobs(self) -> ReportsPipeline:
        """Hard-filter: remove listings that fail the title gate or avoid rules."""
        before = len(self.candidates)
        self.candidates = [
            listing
            for listing in self.candidates
            if passes_hard_rules(listing, self.criteria, self.config)[0]
        ]
        eliminated = before - len(self.candidates)
        console.print(
            f"[bold cyan]Stage 4/7:[/] Hard filter: "
            f"[red]{eliminated}[/] eliminated, "
            f"[green]{len(self.candidates)}[/] surviving."
        )
        upsert_listings_csv(raw=self.candidates)
        return self

    # -------------------------------------------------------------------------
    # Stage 5
    # -------------------------------------------------------------------------

    def score_relevant_positions(self) -> ReportsPipeline:
        """Apply weighted rule scoring (+ semantic if enabled); sort and narrow to top N for LLM."""
        if self.config.weights.semantic_fit > 0:
            from classify.semantic import scorer_from_criteria
            console.print(
                f"[bold cyan]Stage 5/7:[/] Loading semantic model "
                f"(first run downloads ~80 MB)..."
            )
            self.config.semantic_scorer = scorer_from_criteria(self.criteria)

        self.top_listings, self.rest_listings = rank_and_narrow(
            self.candidates, self.criteria, self.config
        )
        below_threshold = sum(1 for s in self.rest_listings if s.disqualified)
        survived_cut = [s for s in self.rest_listings if not s.disqualified]
        console.print(
            f"[bold cyan]Stage 5/7:[/] Rule scoring: "
            f"[green]{len(self.top_listings)}[/] for LLM, "
            f"[yellow]{len(survived_cut)}[/] surviving cut, "
            f"[red]{below_threshold}[/] below threshold."
        )
        upsert_listings_csv(scored=self.top_listings + survived_cut)
        return self

    # -------------------------------------------------------------------------
    # Stage 6
    # -------------------------------------------------------------------------

    def analyze_remaining_positions(
        self,
        model: str = "gpt-4o",
        use_cache: bool = True,
        concurrency: int = DEFAULT_LLM_CONCURRENCY,
    ) -> ReportsPipeline:
        """Quick LLM evaluation of top-N rule-scored listings."""
        if self.top_listings:
            console.print("[bold cyan]Stage 6/7:[/] Fetching full postings for LLM context...")
            enriched: list[ScoredListing] = []
            for scored in self.top_listings:
                updated_listing = fetch_description(scored.listing)
                enriched.append(score_listing(updated_listing, self.criteria, self.config))
            self.top_listings = sorted(enriched, key=lambda s: s.total_score, reverse=True)
            upsert_listings_csv(scored=self.top_listings)

        console.print(f"[bold cyan]Stage 6/7:[/] LLM evaluation ({model})...")
        client = OpenAI()
        self.evaluated = batch_evaluate(
            client,
            self.top_listings,
            self.criteria,
            model=model,
            use_cache=use_cache,
            concurrency=concurrency,
        )
        return self

    # -------------------------------------------------------------------------
    # Stage 7
    # -------------------------------------------------------------------------

    def generate_reports(
        self,
        output_json: Path | None = None,
        reports_dir: Path = REPORTS_DIR,
        detailed_top_n: int | None = None,
        min_report_score: int = 5,
        report_model: str = "gpt-4o",
    ) -> ReportsPipeline:
        """Display the ranked results table, write detailed markdown reports, and optionally JSON."""
        console.print("[bold cyan]Stage 7/7:[/] Generating reports...")
        display_results(self.evaluated)

        if output_json:
            write_json_output(self.evaluated, self.config, output_json)

        report_paths: dict[str, str] = {}
        if self.evaluated:
            client = OpenAI()
            paths_by_url = batch_generate_reports(
                client,
                self.evaluated,
                self.criteria,
                self.cv_text,
                self.profile_text,
                model=report_model,
                top_n=detailed_top_n,
                min_llm_score=min_report_score,
                output_dir=reports_dir,
            )
            report_paths = {
                url: str(path)
                for url, path in paths_by_url.items()
            }

        upsert_listings_csv(evaluated=self.evaluated, report_paths=report_paths)
        return self


# ---------------------------------------------------------------------------
# Internal helper — criteria generation
# ---------------------------------------------------------------------------

def _generate_criteria_file(
    cv_text: str,
    profile_text: str,
    output_path: Path,
    model: str,
) -> None:
    """
    Call the OpenAI API to write scoring_criteria.yaml from the CV + profile.

    Reuses the prompt builders and post-processors from generate_criteria.py
    so that pipeline-generated criteria are identical to those produced by the
    standalone `generate-criteria` command.
    """
    from datetime import datetime, timezone

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_user_message(cv_text, profile_text)},
        ],
    )

    raw_yaml = strip_fences(response.choices[0].message.content or "")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    final_yaml = inject_meta(raw_yaml, generated_at)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(final_yaml)
    console.print(f"  Written to [bold]{output_path}[/].")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def build_reports_pipeline(
    output_json: Path | None = None,
    llm_model: str = "gpt-4o",
    llm_concurrency: int = DEFAULT_LLM_CONCURRENCY,
    use_cache: bool = True,
    detailed_top_n: int | None = None,
    min_report_score: int = 5,
    reports_dir: Path = REPORTS_DIR,
    tuning_path: Path = DEFAULT_TUNING_PATH,
) -> None:
    """Run the full end-to-end pipeline and write detailed markdown job reports."""
    (
        ReportsPipeline()
        .read_profile_and_cv()
        .generate_criteria()
        .scan_for_jobs(tuning_path=tuning_path)
        .eliminate_irrelevant_jobs()
        .score_relevant_positions()
        .analyze_remaining_positions(
            model=llm_model,
            use_cache=use_cache,
            concurrency=llm_concurrency,
        )
        .generate_reports(
            output_json=output_json,
            reports_dir=reports_dir,
            detailed_top_n=detailed_top_n,
            min_report_score=min_report_score,
        )
    )


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command("pipeline")
@click.option(
    "--output-json",
    type=click.Path(),
    default=None,
    help="Write full results JSON to this path.",
)
@click.option(
    "--llm-model",
    default="gpt-4o",
    show_default=True,
    help="OpenAI model for LLM evaluation and report generation.",
)
@click.option(
    "--llm-concurrency",
    default=DEFAULT_LLM_CONCURRENCY,
    show_default=True,
    type=click.IntRange(1, 32),
    help="Concurrent quick LLM evaluations to run.",
)
@click.option(
    "--no-cache",
    is_flag=True,
    default=False,
    help="Re-evaluate all listings via API, ignoring disk cache.",
)
@click.option(
    "--reports-top-n",
    default=None,
    type=int,
    help="Limit detailed markdown reports to the top N LLM-evaluated listings. Default: no cap.",
)
@click.option(
    "--reports-dir",
    default=str(REPORTS_DIR),
    show_default=True,
    type=click.Path(),
    help="Directory to write detailed markdown reports into.",
)
@click.option(
    "--tuning-config",
    "tuning_path",
    default=str(DEFAULT_TUNING_PATH),
    show_default=True,
    type=click.Path(),
    help="Path to scoring_tuning.yaml with user-adjustable numeric weights and score ladders.",
)
@click.option(
    "--min-report-score",
    default=5,
    show_default=True,
    type=int,
    help="Minimum LLM fit_score (0–10) required to generate a detailed report.",
)
def pipeline_command(
    output_json: str | None,
    llm_model: str,
    llm_concurrency: int,
    no_cache: bool,
    reports_top_n: int | None,
    reports_dir: str,
    tuning_path: str,
    min_report_score: int,
) -> None:
    """Run the full job-report pipeline end-to-end."""
    build_reports_pipeline(
        output_json=Path(output_json) if output_json else None,
        llm_model=llm_model,
        llm_concurrency=llm_concurrency,
        use_cache=not no_cache,
        detailed_top_n=reports_top_n,
        min_report_score=min_report_score,
        reports_dir=Path(reports_dir),
        tuning_path=Path(tuning_path),
    )
