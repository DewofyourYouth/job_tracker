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
PORTALS_PATH = Path("data/portals.yaml")
REQUIRED_KEYS = [
    "weights", "tolerances", "role_fit",
    "seniority", "location_remote", "tech_stack", "avoid", "compensation",
]


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_system_prompt() -> str:
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

- role_fit.strong_keywords: 2–4 word noun phrases that are specific enough to \
discriminate the candidate's target roles from generic engineering jobs. A phrase \
qualifies ONLY if it appears in 2 or more exact_archetypes, OR is a specialised \
compound that rarely appears outside the target domain (e.g., "Platform Engineer", \
"Infrastructure Engineer", "Developer Platform", "Internal Tools"). Do NOT include \
generic phrases such as "Backend Engineer" or "Software Engineer" — those match \
thousands of unrelated postings and belong in weak_keywords instead. Keep this list \
short (2–5 items).

- role_fit.weak_keywords: single words or short phrases that are a weak positive \
signal (e.g., "Backend", "Infrastructure", "Backend Engineer"). These fire when \
nothing stronger matched. Keep this list to 3–6 items.

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


def build_user_message(
    cv_text: str,
    profile_text: str,
    portals_title_filter: dict | None = None,
) -> str:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    message = (
        f"generated_at: {now}\n\n"
        f"--- CV (Markdown) ---\n{cv_text}\n\n"
        f"--- Profile (YAML) ---\n{profile_text}\n"
    )
    if portals_title_filter:
        message += (
            "\n--- portals.yaml title_filter context ---\n"
            f"{yaml.dump({'title_filter': portals_title_filter}, allow_unicode=True, sort_keys=False)}"
        )
    return message


# ---------------------------------------------------------------------------
# Post-processing
# ---------------------------------------------------------------------------

def strip_fences(raw: str) -> str:
    """Remove markdown code fences if the model wrapped the YAML output."""
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        raw = "\n".join(lines[1:])
        raw = raw.rsplit("```", 1)[0].strip()
    return raw


def inject_meta(raw_yaml: str, generated_at: str) -> str:
    """
    Ensure meta.generated_at and meta.source_files are correct regardless
    of what the model emitted.
    """
    parsed = yaml.safe_load(raw_yaml)
    parsed.setdefault("meta", {})
    parsed["meta"]["generated_at"] = generated_at
    parsed["meta"]["source_files"] = ["data/cv.md", "data/profile.yaml"]
    return yaml.dump(parsed, allow_unicode=True, sort_keys=False)


def inject_title_filter(raw_yaml: str, title_filter: dict | None) -> str:
    """Ensure generated criteria preserves the portal title filter when provided."""
    if not title_filter:
        return raw_yaml
    parsed = yaml.safe_load(raw_yaml)
    if not parsed.get("title_filter"):
        parsed["title_filter"] = title_filter
    return yaml.dump(parsed, allow_unicode=True, sort_keys=False)


def validate_criteria(parsed: dict) -> list[str]:
    """
    Sanity-check the generated YAML. Returns warning strings (empty = clean).
    Warnings are surfaced to the user; they don't abort the write.
    """
    warnings: list[str] = []

    for key in REQUIRED_KEYS:
        if key not in parsed:
            warnings.append(f"missing top-level key: {key!r}")

    archetypes = parsed.get("role_fit", {}).get("exact_archetypes", [])
    if not archetypes:
        warnings.append("role_fit.exact_archetypes is empty")

    tech_kws = parsed.get("tech_stack", {}).get("keywords", [])
    if not tech_kws:
        warnings.append("tech_stack.keywords is empty")

    avoid = parsed.get("avoid", {}).get("hard_disqualify")
    if avoid is None:
        warnings.append("avoid.hard_disqualify key missing")

    weights = parsed.get("weights", {})
    for name, val in weights.items():
        try:
            f = float(val)
            if not 0.0 <= f <= 1.0:
                warnings.append(f"weights.{name} = {val} is outside [0.0, 1.0]")
        except (TypeError, ValueError):
            warnings.append(f"weights.{name} is not a number: {val!r}")

    minimum = parsed.get("compensation", {}).get("minimum", 0)
    if not minimum or float(minimum) <= 0:
        warnings.append("compensation.minimum is zero or missing")

    return warnings


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
    "--portals", "portals_path",
    default=str(PORTALS_PATH),
    show_default=True,
    type=click.Path(exists=True),
    help="Path to portals.yaml; its title_filter is copied into generated criteria.",
)
@click.option(
    "--model",
    default="gpt-4o",
    show_default=True,
    help="OpenAI model to use. gpt-4o is recommended for richer extraction.",
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
    portals_path: str,
    model: str,
    dry_run: bool,
    force: bool,
) -> None:
    """Generate data/scoring_criteria.yaml from your CV and profile via the OpenAI API."""

    output = Path(output_path)

    if output.exists() and not dry_run and not force:
        click.confirm(f"{output} already exists. Overwrite?", abort=True)

    cv_text = Path(cv_path).read_text()
    profile_text = Path(profile_path).read_text()
    portals_config = yaml.safe_load(Path(portals_path).read_text()) or {}
    portals_title_filter = portals_config.get("title_filter")

    console.print(f"[bold cyan]Generating scoring criteria[/] using [bold]{model}[/]...")

    client = OpenAI()
    response = client.chat.completions.create(
        model=model,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": build_system_prompt()},
            {
                "role": "user",
                "content": build_user_message(
                    cv_text,
                    profile_text,
                    portals_title_filter,
                ),
            },
        ],
    )

    raw_yaml = strip_fences(response.choices[0].message.content or "")
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    final_yaml = inject_meta(raw_yaml, generated_at)
    final_yaml = inject_title_filter(final_yaml, portals_title_filter)

    parsed = yaml.safe_load(final_yaml)
    warns = validate_criteria(parsed)
    for w in warns:
        console.print(f"  [yellow]Warning:[/] {w}")

    if dry_run:
        console.print(Syntax(final_yaml, "yaml", theme="monokai"))
        return

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(final_yaml)
    console.print(f"[green]✓[/] Written to [bold]{output}[/]")
    console.print(
        f"  {len(parsed.get('role_fit', {}).get('exact_archetypes', []))} archetypes | "
        f"{len(parsed.get('tech_stack', {}).get('keywords', []))} tech keywords | "
        f"{len(parsed.get('avoid', {}).get('hard_disqualify', []))} hard-avoid roles"
    )
