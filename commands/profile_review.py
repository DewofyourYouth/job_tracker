"""Compare a CV markdown file with the structured candidate profile."""

import json
import os
from pathlib import Path

import click
import yaml

try:
    from commands.auth import AuthError, get_openai_api_key
except ModuleNotFoundError as exc:
    if exc.name != "commands":
        raise
    from auth import AuthError, get_openai_api_key


DEFAULT_MODEL = "gpt-4.1-mini"
REQUIRED_PROFILE_KEYS = {
    "candidate",
    "target_roles",
    "narrative",
    "preferences",
    "compensation",
    "location",
}


class ProfileReviewError(RuntimeError):
    """Raised when profile review or application fails."""


def review_profile(
    cv_path,
    profile_path,
    feedback=None,
    model=None,
):
    """Return structured recommendations and a proposed profile."""

    cv_text = Path(cv_path).read_text(encoding="utf-8")
    profile_text = Path(profile_path).read_text(encoding="utf-8")
    profile = yaml.safe_load(profile_text)

    if not isinstance(profile, dict):
        raise ProfileReviewError(f"{profile_path} must contain a YAML mapping.")

    get_openai_api_key()
    try:
        from openai import APIError, OpenAI, RateLimitError
    except ImportError as exc:
        raise ProfileReviewError(
            "The openai package is not installed. Install requirements.txt first."
        ) from exc

    client = OpenAI()

    try:
        response = client.chat.completions.create(
            model=model or os.getenv("OPENAI_MODEL", DEFAULT_MODEL),
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You compare a candidate CV with a structured YAML profile "
                        "used by a job-search scanner. Recommend changes that make "
                        "the profile more accurate, specific, and useful for job "
                        "classification. Do not invent facts that are unsupported "
                        "by the CV. Keep contact details unchanged unless the CV "
                        "clearly contradicts them. Preserve explicit role dislikes "
                        "and avoidance preferences. Return only JSON."
                    ),
                },
                {
                    "role": "user",
                    "content": _build_prompt(cv_text, profile_text, feedback),
                },
            ],
        )
    except RateLimitError as exc:
        error_code = _openai_error_code(exc)
        if error_code == "insufficient_quota":
            raise ProfileReviewError(
                "OpenAI rejected the request because the API key has insufficient quota. "
                "Check billing/quota for the key, use a different OPENAI_API_KEY, or "
                "set OPENAI_MODEL to a cheaper available model."
            ) from exc

        raise ProfileReviewError(f"OpenAI rate limit error: {exc}") from exc
    except APIError as exc:
        raise ProfileReviewError(f"OpenAI API error: {exc}") from exc

    content = response.choices[0].message.content
    if not content:
        raise ProfileReviewError("OpenAI returned an empty profile review.")

    try:
        result = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ProfileReviewError(f"OpenAI returned invalid JSON: {exc}") from exc

    _validate_review_result(result)
    return result


def apply_profile_review(result, profile_path):
    """Write the proposed profile from a review result to disk."""

    proposed_profile = result["proposed_profile"]
    _validate_profile(proposed_profile)

    yaml_text = yaml.safe_dump(
        proposed_profile,
        sort_keys=False,
        allow_unicode=True,
        width=100,
    )
    Path(profile_path).write_text(yaml_text, encoding="utf-8")


def render_review(result):
    """Render structured review results for a terminal."""

    lines = []
    summary = result.get("summary")
    if summary:
        lines.extend(["Summary", summary, ""])

    recommendations = result.get("recommendations", [])
    if recommendations:
        lines.append("Recommendations")
        for index, item in enumerate(recommendations, start=1):
            section = item.get("section", "profile")
            issue = item.get("issue", "")
            rationale = item.get("rationale", "")
            change = item.get("change", "")
            lines.append(f"{index}. {section}: {issue}")
            if rationale:
                lines.append(f"   Why: {rationale}")
            if change:
                lines.append(f"   Change: {change}")
        lines.append("")

    cautions = result.get("cautions", [])
    if cautions:
        lines.append("Cautions")
        for caution in cautions:
            lines.append(f"- {caution}")
        lines.append("")

    return "\n".join(lines).strip()


def _build_prompt(cv_text, profile_text, feedback):
    feedback_text = feedback or "No additional user feedback."
    return f"""
Compare this CV markdown against this YAML profile.

User feedback to honor:
{feedback_text}

Return JSON with this shape:
{{
  "summary": "short overall assessment",
  "recommendations": [
    {{
      "section": "target_roles|narrative|preferences|compensation|location|candidate",
      "issue": "what is wrong or missing",
      "rationale": "why the CV supports the recommendation",
      "change": "specific proposed change"
    }}
  ],
  "cautions": ["claims or target roles that are weakly supported"],
  "proposed_profile": {{
    "candidate": {{}},
    "target_roles": {{}},
    "narrative": {{}},
    "preferences": {{}},
    "compensation": {{}},
    "location": {{}}
  }}
}}

The proposed_profile must be a complete replacement for the current profile,
not a patch. Use YAML-compatible JSON values only.

CV markdown:
---CV---
{cv_text}
---END CV---

Current profile YAML:
---PROFILE---
{profile_text}
---END PROFILE---
""".strip()


def _validate_review_result(result):
    if not isinstance(result, dict):
        raise ProfileReviewError("Review result must be a JSON object.")
    if "proposed_profile" not in result:
        raise ProfileReviewError("Review result is missing proposed_profile.")
    _validate_profile(result["proposed_profile"])


def _validate_profile(profile):
    if not isinstance(profile, dict):
        raise ProfileReviewError("proposed_profile must be an object.")

    missing = REQUIRED_PROFILE_KEYS - set(profile)
    if missing:
        missing_text = ", ".join(sorted(missing))
        raise ProfileReviewError(f"proposed_profile is missing: {missing_text}")


def _openai_error_code(exc):
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        return body.get("code")
    return None


@click.command("profile-review")
@click.option(
    "--cv",
    "cv_path",
    default="data/cv.md",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="CV markdown file to compare.",
)
@click.option(
    "--profile",
    "profile_path",
    default="data/profile.yaml",
    show_default=True,
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Profile YAML file to review.",
)
@click.option(
    "--feedback",
    default=None,
    help="Extra guidance to incorporate, for example role preferences.",
)
@click.option(
    "--feedback-file",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    help="Read extra guidance from a file.",
)
@click.option(
    "--model",
    default=None,
    help=f"OpenAI model to use. Defaults to OPENAI_MODEL or {DEFAULT_MODEL}.",
)
@click.option(
    "--apply",
    "apply_changes",
    is_flag=True,
    help="Overwrite the profile YAML with the proposed profile.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    help="Print the raw structured review JSON.",
)
def profile_review_command(
    cv_path,
    profile_path,
    feedback,
    feedback_file,
    model,
    apply_changes,
    json_output,
):
    """Compare cv.md to profile.yaml and recommend or apply profile changes."""

    if feedback_file:
        file_feedback = feedback_file.read_text(encoding="utf-8").strip()
        feedback = "\n\n".join(part for part in [feedback, file_feedback] if part)

    try:
        result = review_profile(
            cv_path=cv_path,
            profile_path=profile_path,
            feedback=feedback,
            model=model,
        )

        if apply_changes:
            apply_profile_review(result, profile_path)

    except (AuthError, ProfileReviewError) as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        click.echo(render_review(result))

    if apply_changes:
        click.echo(f"\nApplied proposed profile to {profile_path}.")
    else:
        click.echo("\nNo files changed. Re-run with --apply to update the profile.")


if __name__ == "__main__":
    profile_review_command()
