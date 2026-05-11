"""
LLM-based deep evaluation of pre-filtered, top-ranked job listings.

Pipeline position: STAGE 3 (receives top N from rules.rank_and_narrow)

We only call the API for listings that survived the rules filter and
ranked in the top N. Each listing gets one API call; results are cached
by URL so re-runs don't re-spend tokens on listings we've already seen.

Model: gpt-4o by default (flagship model for high-quality fit scoring).
"""


import hashlib
import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from openai import OpenAI

from classify.rules import ScoredListing


# ---------------------------------------------------------------------------
# Output model
# ---------------------------------------------------------------------------

@dataclass
class LLMEvaluation:
    listing_url: str
    fit_score: int           # 1 – 10; 7+ = worth applying
    fit_summary: str         # 2–3 sentence overall fit assessment
    strengths: list[str]     # bullet reasons this is a strong match
    red_flags: list[str]     # bullet concerns or mismatches
    recommendation: str      # "apply" | "maybe" | "skip"
    raw_response: str        # full model output for debugging
    cached: bool = False     # True if loaded from disk cache rather than API


# ---------------------------------------------------------------------------
# Prompt construction
# ---------------------------------------------------------------------------

def build_system_prompt() -> str:
    return (
        "You are a senior technical recruiter evaluating software engineering job listings.\n"
        "\n"
        "SCORING RUBRIC (fit_score 1–10):\n"
        "  9–10  Exceptional match — candidate should prioritise this.\n"
        "  7–8   Good match OR strong wildcard/pivot opportunity.\n"
        "  5–6   Mixed signals — apply only if pipeline is thin.\n"
        "  1–4   Poor fit — skip.\n"
        "\n"
        "OUTPUT: reply with only valid JSON matching exactly this schema:\n"
        "{\n"
        '  "fit_score": <integer 1-10>,\n'
        '  "fit_summary": "<2-3 sentence overall assessment>",\n'
        '  "strengths": ["<strength>", ...],\n'
        '  "red_flags": ["<concern>", ...],\n'
        '  "recommendation": "<apply|maybe|skip>"\n'
        "}\n"
        "\n"
        "CONSTRAINTS:\n"
        "- strengths and red_flags: max 3 items each, ≤25 words per item.\n"
        "- Flag as red flags: visa sponsorship requirements, severe stack mismatch,\n"
        "  ops/admin-heavy roles, explicit on-site in non-preferred location,\n"
        "  compensation clearly below candidate minimum.\n"
        "- WILDCARD POTENTIAL: Be open to non-obvious fits. If the role requires \n"
        "  skills the candidate doesn't explicitly have, but they are clearly a \n"
        "  strong engineer who could adapt, or if the role sounds like an \n"
        "  interesting pivot that leverages their core strengths, boost the \n"
        "  fit_score and note it as a 'wildcard' strength.\n"
        "- Do not repeat the candidate profile back verbatim.\n"
        "- Output only the JSON object, no surrounding prose."
    )


def build_evaluation_prompt(scored: ScoredListing, criteria: dict) -> str:
    """
    Build the user-turn prompt for a single listing evaluation.

    Includes a compact YAML-ish block:
      CANDIDATE: target roles, avoid list, compensation, acceptable locations.
      LISTING: title, company, location, rule scores, description (first 800 chars).
      TASK: evaluate fit, return JSON.
    """
    listing = scored.listing
    comp = criteria.get("compensation", {})
    rf = criteria.get("role_fit", {})
    avoid = criteria.get("avoid", {})
    loc_cfg = criteria.get("location_remote", {})

    target_roles = rf.get("exact_archetypes", [])[:6]
    avoid_roles = avoid.get("hard_disqualify", [])
    acceptable_locs = loc_cfg.get("acceptable_onsite_locations", [])
    min_comp = comp.get("minimum", 0)
    target_comp = comp.get("target", 0)
    currency = comp.get("currency", "")

    rule_scores = "  ".join(
        f"{name}: {c.raw_score:.2f}" for name, c in scored.criteria.items()
    )

    desc = listing.description
    if desc:
        desc = desc[:800].strip()
        if len(listing.description) > 800:
            desc += "..."
    else:
        desc = "not fetched"

    return (
        f"CANDIDATE:\n"
        f"  target_roles: {target_roles}\n"
        f"  avoid: {avoid_roles}\n"
        f"  acceptable_locations: {acceptable_locs}\n"
        f"  compensation: min {min_comp} {currency}, target {target_comp} {currency}\n"
        f"\n"
        f"LISTING:\n"
        f"  title: {listing.title}\n"
        f"  company: {listing.company}\n"
        f"  location: {listing.location or 'not specified'}\n"
        f"  salary_hint: {listing.salary_hint or 'not specified'}\n"
        f"  rule_scores: {rule_scores}\n"
        f"  total_rule_score: {scored.total_score:.3f}\n"
        f"  description: |\n"
        f"    {desc}\n"
        f"\n"
        f"TASK: Evaluate fit. Return JSON only."
    )


# ---------------------------------------------------------------------------
# Disk cache (avoid re-spending tokens on seen listings)
# ---------------------------------------------------------------------------

CACHE_DIR = Path("output/llm_cache")


def _cache_key(scored: ScoredListing, criteria: dict, model: str) -> str:
    """Fingerprint the full evaluation context, not just the listing URL."""
    listing = scored.listing
    payload = {
        "url": listing.url,
        "title": listing.title,
        "company": listing.company,
        "location": listing.location,
        "salary_hint": listing.salary_hint,
        "description": listing.description or "",
        "rule_scores": {
            name: {
                "raw_score": c.raw_score,
                "weighted": c.weighted,
                "reason": c.reason,
            }
            for name, c in scored.criteria.items()
        },
        "total_rule_score": scored.total_score,
        "criteria": criteria,
        "model": model,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _load_cached(scored: ScoredListing, criteria: dict, model: str) -> Optional[LLMEvaluation]:
    path = CACHE_DIR / f"{_cache_key(scored, criteria, model)}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return LLMEvaluation(**data, cached=True)


def _save_cached(evaluation: LLMEvaluation, scored: ScoredListing, criteria: dict, model: str) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(scored, criteria, model)}.json"
    data = {k: v for k, v in evaluation.__dict__.items() if k != "cached"}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2))


# ---------------------------------------------------------------------------
# Single-listing evaluation
# ---------------------------------------------------------------------------

def evaluate_listing(
    client: OpenAI,
    scored: ScoredListing,
    criteria: dict,
    *,
    model: str = "gpt-4o",
    use_cache: bool = True,
) -> LLMEvaluation:
    """
    Evaluate one listing via the OpenAI API.

    Cache check happens before the API call; result is written to cache after.
    On JSON parse failure, returns a minimal LLMEvaluation with recommendation="maybe"
    rather than raising — callers always get a result.
    """
    if use_cache:
        cached = _load_cached(scored, criteria, model)
        if cached:
            return cached

    response = client.chat.completions.create(
        model=model,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": build_system_prompt()},
            {"role": "user", "content": build_evaluation_prompt(scored, criteria)},
        ],
        max_tokens=512,
    )

    raw = response.choices[0].message.content or ""

    try:
        data = json.loads(raw)
        evaluation = LLMEvaluation(
            listing_url=scored.listing.url,
            fit_score=int(data.get("fit_score", 5)),
            fit_summary=data.get("fit_summary", ""),
            strengths=data.get("strengths", []),
            red_flags=data.get("red_flags", []),
            recommendation=data.get("recommendation", "maybe"),
            raw_response=raw,
        )
    except (json.JSONDecodeError, KeyError, ValueError) as e:
        evaluation = LLMEvaluation(
            listing_url=scored.listing.url,
            fit_score=5,
            fit_summary="Response parse error — manual review needed.",
            strengths=[],
            red_flags=[f"Parse error: {e}"],
            recommendation="maybe",
            raw_response=raw,
        )

    _save_cached(evaluation, scored, criteria, model)
    return evaluation


# ---------------------------------------------------------------------------
# Batch evaluation with progress
# ---------------------------------------------------------------------------

def batch_evaluate(
    client: OpenAI,
    top_listings: list[ScoredListing],
    criteria: dict,
    *,
    model: str = "gpt-4o",
    use_cache: bool = True,
) -> list[tuple[ScoredListing, LLMEvaluation]]:
    """
    Evaluate all top listings and return (scored, evaluation) pairs sorted by
    fit_score descending. A failed evaluation is captured as a "maybe" rather
    than aborting the batch.
    """
    try:
        from tqdm import tqdm
        iterator = tqdm(top_listings, desc="LLM evaluation")
    except ImportError:
        iterator = top_listings  # type: ignore[assignment]

    results: list[tuple[ScoredListing, LLMEvaluation]] = []
    for scored in iterator:
        try:
            evaluation = evaluate_listing(
                client, scored, criteria, model=model, use_cache=use_cache
            )
        except Exception as e:
            warnings.warn(f"LLM evaluation failed for {scored.listing.url}: {e}")
            evaluation = LLMEvaluation(
                listing_url=scored.listing.url,
                fit_score=5,
                fit_summary="Evaluation failed — manual review needed.",
                strengths=[],
                red_flags=[f"API error: {e}"],
                recommendation="maybe",
                raw_response="",
            )
        results.append((scored, evaluation))

    return sorted(results, key=lambda p: p[1].fit_score, reverse=True)
