"""
`generate-criteria` command — derive data/scoring_criteria.yaml from cv.md + profile.yaml.

This command reads your CV and profile, sends them to the OpenAI API, and asks it
to extract a structured YAML file of scoring criteria. The output file is gitignored
and must be regenerated whenever your profile or CV changes significantly.

The generated file defines ALL personal parameters used by classify/rules.py:
role archetypes, tech keywords, location rules, avoid patterns, compensation thresholds,
weights, and tolerances. Nothing personal is hardcoded in the Python source.

Usage:
  python entrypoint.py generate-criteria
  python entrypoint.py generate-criteria --output data/scoring_criteria.yaml
  python entrypoint.py generate-criteria --model gpt-4o   # richer extraction
  python entrypoint.py generate-criteria --dry-run        # print YAML, don't write
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import click
import yaml
from openai import OpenAI
from rich.console import Console
from rich.syntax import Syntax

console = Console()

CV_PATH = Path("data/cv.md")
PROFILE_PATH = Path("data/profile.yaml")
DEFAULT_OUTPUT = Path("data/scoring_criteria.yaml")
EXAMPLE_PATH = Path("data/scoring_criteria.example.yaml")


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_system_prompt() -> str:
    """
    System prompt for criteria extraction.

    Instructs the model to act as a structured-data extractor: read the
    candidate's CV and profile, then emit a YAML document that conforms to
    the scoring_criteria.example.yaml schema. The model must NOT invent
    information — every field must be derivable from the inputs.
    """
    # Load the example YAML as the schema reference so the model sees
    # the exact structure it must produce. This avoids schema drift.
    schema = EXAMPLE_PATH.read_text() if EXAMPLE_PATH.exists() else ""

    return f"""You are a structured-data extraction assistant. Your job is to read a \
candidate's CV (Markdown) and job-search profile (YAML), then produce a \
scoring_criteria.yaml document that a job-listing evaluation pipeline will use \
to score listings against this candidate.

## Output format

Produce ONLY valid YAML. No prose, no markdown fences, no explanation.
The output must conform exactly to the following schema (use it as a template):

{schema}

## Extraction rules

- meta.generated_at: use the ISO 8601 timestamp you are given in the user message.
- meta.source_files: always ["data/cv.md", "data/profile.yaml"].

- weights / tolerances: start from the schema defaults. Adjust only if the profile \
strongly implies a different balance (e.g., a candidate who lists compensation as \
non-negotiable might warrant a higher avoid_penalty weight). Explain nothing — just \
emit the values.

- role_fit.exact_archetypes: copy from profile.target_roles.primary and all \
profile.target_roles.archetypes[].name values where fit == "primary".

- role_fit.strong_keywords: extract the 2–3 word noun phrases that appear most \
frequently across the exact_archetypes list (e.g., "Platform Engineer", \
"Backend Engineer"). Include only phrases that a job title would realistically contain.

- role_fit.weak_keywords: single words that are a weaker positive signal \
(e.g., "Engineer", "Backend", "Infrastructure"). Keep this list short (3–6 items).

- seniority.target_level: the level field from profile.target_roles.archetypes \
(e.g., "Senior"). level_scores: use schema defaults unless the profile implies \
the candidate would accept mid-level roles.

- location_remote.patterns: derive acceptable_onsite_locations from \
profile.location.city and profile.location.country. The score ladder must \
follow the schema structure; adjust the "acceptable_onsite_locations" list only.

- tech_stack.keywords: extract technology names from the CV's Technical Skills \
section and from profile.narrative.superpowers. Include only concrete tool/language \
names (e.g., "Kubernetes", "FastAPI", "ArgoCD"), not abstract phrases.

- avoid.hard_disqualify: copy from profile.preferences.avoid_roles.
- avoid.soft_penalise: extract 2–4 keywords that would appear in job titles for \
those avoid roles but are not always disqualifying (e.g., a role titled \
"Platform & DevOps Engineer" might score low but not be hard-disqualified).

- compensation.minimum: profile.compensation.minimum (numeric, no currency symbol).
- compensation.target: midpoint of profile.compensation.target_range if present, \
otherwise same as minimum.
- compensation.currency: profile.compensation.currency.

- title_filter: if portals.yaml is not provided, omit this section. If it is \
provided as context, copy title_filter.positive and title_filter.negative verbatim.

## Constraints

- Do not invent fields not in the schema.
- Do not include any prose commentary in the output.
- Do not include the candidate's name, email, phone, or any PII in comments.
- Output must be parseable by PyYAML without errors.
"""


def build_user_message(cv_text: str, profile_text: str) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return f"""generated_at: {now}

--- CV (Markdown) ---
{cv_text}

--- Profile (YAML) ---
{profile_text}
"""


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def inject_meta(raw_yaml: str, generated_at: str) -> str:
    """
    Ensure meta.generated_at is set to the actual generation time,
    in case the model used a placeholder value.
    """
    # TODO:
    #   parsed = yaml.safe_load(raw_yaml)
    #   parsed.setdefault("meta", {})["generated_at"] = generated_at
    #   parsed["meta"]["source_files"] = ["data/cv.md", "data/profile.yaml"]
    #   return yaml.dump(parsed, allow_unicode=True, sort_keys=False)
    raise NotImplementedError


def validate_criteria(parsed: dict) -> list[str]:
    """
    Sanity-check the generated YAML before writing it to disk.

    Returns a list of warning strings (empty = all good). Does not raise —
    warnings are surfaced to the user who can decide whether to accept or regenerate.

    Checks:
      - Required top-level keys present: weights, tolerances, role_fit,
        seniority, location_remote, tech_stack, avoid, compensation.
      - role_fit.exact_archetypes is a non-empty list.
      - tech_stack.keywords is a non-empty list.
      - avoid.hard_disqualify is a list (may be empty).
      - All weight values are floats in [0.0, 1.0].
      - compensation.minimum is a positive number.
    """
    # TODO: implement checks; return list of warning strings.
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

@click.command("generate-criteria")
@click.option(
    "--output", "output_path",
    default=str(DEFAULT_OUTPUT),
    show_default=True,
    type=click.Path(),
    help="Where to write the generated scoring_criteria.yaml.",
)
@click.option(
    "--cv", "cv_path",
    default=str(CV_PATH),
    show_default=True,
    type=click.Path(exists=True),
    help="Path to your CV markdown file.",
)
@click.option(
    "--profile", "profile_path",
    default=str(PROFILE_PATH),
    show_default=True,
    type=click.Path(exists=True),
    help="Path to your profile.yaml file.",
)
@click.option(
    "--model",
    default="gpt-4o-mini",
    show_default=True,
    help="OpenAI model to use. Use gpt-4o for richer extraction.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Print the generated YAML to stdout without writing the file.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite output file without confirmation if it already exists.",
)
def generate_criteria_command(
    output_path: str,
    cv_path: str,
    profile_path: str,
    model: str,
    dry_run: bool,
    force: bool,
) -> None:
    """Generate data/scoring_criteria.yaml from your CV and profile via the OpenAI API."""

    output = Path(output_path)

    # Guard against accidental overwrites of a tuned criteria file.
    if output.exists() and not dry_run and not force:
        click.confirm(
            f"{output} already exists. Overwrite?",
            abort=True,
        )

    cv_text = Path(cv_path).read_text()
    profile_text = Path(profile_path).read_text()

    console.print(f"[bold cyan]Generating scoring criteria[/] using [bold]{model}[/]...")

    # TODO:
    #   client = OpenAI()  # reads OPENAI_API_KEY from env
    #
    #   response = client.chat.completions.create(
    #       model=model,
    #       max_tokens=2048,
    #       messages=[
    #           {"role": "system", "content": build_system_prompt()},
    #           {"role": "user",   "content": build_user_message(cv_text, profile_text)},
    #       ],
    #   )
    #
    #   raw_yaml = response.choices[0].message.content.strip()
    #
    #   # Strip markdown fences if the model wrapped the output.
    #   if raw_yaml.startswith("```"):
    #       raw_yaml = "\n".join(raw_yaml.split("\n")[1:])
    #       raw_yaml = raw_yaml.rsplit("```", 1)[0].strip()
    #
    #   generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    #   final_yaml = inject_meta(raw_yaml, generated_at)
    #   parsed = yaml.safe_load(final_yaml)
    #   warnings = validate_criteria(parsed)
    #   for w in warnings:
    #       console.print(f"  [yellow]Warning:[/] {w}")
    #
    #   if dry_run:
    #       console.print(Syntax(final_yaml, "yaml", theme="monokai"))
    #       return
    #
    #   output.write_text(final_yaml)
    #   console.print(f"[green]✓[/] Written to [bold]{output}[/]")
    #   console.print(
    #       f"  {len(parsed.get('role_fit', {}).get('exact_archetypes', []))} archetypes | "
    #       f"{len(parsed.get('tech_stack', {}).get('keywords', []))} tech keywords | "
    #       f"{len(parsed.get('avoid', {}).get('hard_disqualify', []))} hard-avoid roles"
    #   )
    raise NotImplementedError
