"""
LLM-based deep evaluation of pre-filtered, top-ranked job listings.

Pipeline position: STAGE 3 (receives top N from rules.rank_and_narrow)

We only call the API for listings that survived the rules filter and
ranked in the top N. Each listing gets one API call; results are cached
by URL so re-runs don't re-spend tokens on listings we've already seen.

Model: gpt-4o-mini by default (cheap, fast, good enough for fit scoring).
Upgrade to gpt-4o for nuanced evaluation of borderline listings.
"""

from __future__ import annotations

import hashlib
import json
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

def build_evaluation_prompt(scored: ScoredListing, profile: dict) -> str:
    """
    Build the user-turn prompt for a single listing evaluation.

    Includes:
      - Candidate summary (headline, superpowers, avoid_roles, compensation).
      - Listing details: title, company, location, description (if available),
        and the rule-based scores as a structured hint so the model can
        calibrate rather than re-derive them from scratch.

    Keep it tight — this is the most token-expensive part of the pipeline.
    The system prompt (built separately) carries the stable instructions;
    only listing-specific data goes here.
    """
    # TODO:
    #   Build a compact YAML-ish block:
    #     CANDIDATE:
    #       headline: ...
    #       target_roles: [...]
    #       avoid: [...]
    #       compensation_min_ILS: ...
    #       location: Israel, remote preferred
    #     LISTING:
    #       title: ...
    #       company: ...
    #       location: ...
    #       rule_scores: {role_fit: 0.8, seniority: 1.0, ...}
    #       description: |
    #         <first 800 chars of description or "not fetched">
    #     TASK:
    #       Evaluate fit. Reply in JSON:
    #         {fit_score, fit_summary, strengths, red_flags, recommendation}
    raise NotImplementedError


def build_system_prompt() -> str:
    """
    Stable system prompt sent as the system role message.

    Includes:
      - Role: senior technical recruiter who evaluates engineer-job fit.
      - Output format contract (JSON schema for LLMEvaluation fields).
      - Scoring rubric for fit_score 1–10.
      - Instruction to be concise (max 3 bullets per list, ≤25 words each).
      - Instruction to flag sponsorship requirements, stack mismatches,
        and ops-only roles as red flags.
    """
    # TODO: write the actual system prompt text here.
    # Structure: role definition → output schema → rubric → constraints.
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Disk cache (avoid re-spending tokens on seen listings)
# ---------------------------------------------------------------------------

CACHE_DIR = Path("output/llm_cache")


def _cache_key(url: str) -> str:
    return hashlib.sha256(url.encode()).hexdigest()[:16]


def _load_cached(url: str) -> Optional[LLMEvaluation]:
    """Return a cached LLMEvaluation if one exists for this URL, else None."""
    path = CACHE_DIR / f"{_cache_key(url)}.json"
    if not path.exists():
        return None
    data = json.loads(path.read_text())
    return LLMEvaluation(**data, cached=True)


def _save_cached(evaluation: LLMEvaluation) -> None:
    """Persist an evaluation to disk so future runs skip this URL."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{_cache_key(evaluation.listing_url)}.json"
    # Exclude 'cached' from the stored payload so loading always sets it fresh.
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
    model: str = "gpt-4o-mini",
    use_cache: bool = True,
) -> LLMEvaluation:
    """
    Evaluate one listing via the OpenAI API.

    Cache check happens before the API call; result is written to cache after.

    Response parsing: request JSON mode (response_format={"type": "json_object"})
    so the model always returns valid JSON. On parse failure, fall back to a
    minimal LLMEvaluation with recommendation="maybe" and a red_flag noting
    the error — we never raise here, callers should always get a result.

    Token budget:
      - System prompt: ~600 tokens
      - User prompt:   ~400 tokens per listing
      - Response:      ~300 tokens
      Total per call:  ~1000 input + 300 output ≈ cheap on gpt-4o-mini
    """
    if use_cache:
        cached = _load_cached(scored.listing.url)
        if cached:
            return cached

    # TODO:
    #   1. Build prompts with build_system_prompt() and build_evaluation_prompt().
    #   2. Call client.chat.completions.create() with:
    #        model=model,
    #        response_format={"type": "json_object"},
    #        messages=[
    #            {"role": "system", "content": build_system_prompt()},
    #            {"role": "user",   "content": build_evaluation_prompt(scored, criteria)},
    #        ],
    #        max_tokens=512,
    #   3. Parse response.choices[0].message.content as JSON.
    #   4. Construct LLMEvaluation and save to cache.
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Batch evaluation with progress
# ---------------------------------------------------------------------------

def batch_evaluate(
    client: OpenAI,
    top_listings: list[ScoredListing],
    criteria: dict,
    *,
    model: str = "gpt-4o-mini",
    use_cache: bool = True,
) -> list[tuple[ScoredListing, LLMEvaluation]]:
    """
    Evaluate all top listings and return (scored, evaluation) pairs.

    Calls are sequential (not concurrent) to keep rate-limit behaviour
    predictable. If a single evaluation fails, log a warning and continue —
    a failed evaluation should not abort the whole batch.

    Progress is reported via tqdm so the user can see API calls happening.

    Returns pairs sorted by evaluation.fit_score descending so callers
    receive results in display-ready order.
    """
    # TODO:
    #   for scored in tqdm(top_listings, desc="LLM evaluation"):
    #       try: eval = evaluate_listing(client, scored, criteria, model=model, use_cache=use_cache)
    #       except Exception as e: log warning, construct minimal eval with red_flags=[str(e)]
    #       results.append((scored, eval))
    #   return sorted(results, key=lambda p: p[1].fit_score, reverse=True)
    raise NotImplementedError
