"""
Rule-based pre-filter and weighted scoring for job listings.

Pipeline position: STAGE 2 (after ingestion, before LLM evaluation)

All personal scoring parameters — archetypes, keywords, location rules,
avoid lists, compensation thresholds — are loaded from data/scoring_criteria.yaml,
which is gitignored and generated via:

  python entrypoint.py generate-criteria

The Python code here is generic. It implements the algorithm; the YAML
defines what matters for the candidate. Never add personal preferences to
this file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml


CRITERIA_PATH = Path("data/scoring_criteria.yaml")


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class RawListing:
    """A job listing as returned by ingestion — minimal, not yet evaluated."""
    title: str
    company: str
    url: str
    source: str                        # which search query or tracked-company produced this
    description: Optional[str] = None  # full text if we fetched it; None if title-only
    location: Optional[str] = None
    salary_hint: Optional[str] = None  # raw salary string from the posting, if present
    raw: dict = field(default_factory=dict)


@dataclass
class CriterionScore:
    """Score and explanation for a single weighted criterion."""
    weight: float
    raw_score: float   # 0.0 – 1.0 before weighting
    weighted: float    # raw_score * weight
    reason: str


@dataclass
class ScoredListing:
    listing: RawListing
    criteria: dict[str, CriterionScore]
    total_score: float
    disqualified: bool = False
    disqualify_reason: Optional[str] = None


# ---------------------------------------------------------------------------
# Config — loaded from YAML, not hardcoded
# ---------------------------------------------------------------------------

@dataclass
class ScoringWeights:
    role_fit: float = 0.40
    seniority: float = 0.20
    location_remote: float = 0.15
    tech_stack: float = 0.15
    avoid_penalty: float = 0.10


@dataclass
class ScoringTolerances:
    min_score_threshold: float = 0.25
    top_n_for_llm: int = 20
    salary_below_min_tolerance_pct: float = 0.10
    location_override_role_fit: float = 0.80
    min_title_keyword_hits: int = 1


@dataclass
class ScoringConfig:
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    tolerances: ScoringTolerances = field(default_factory=ScoringTolerances)


DEFAULT_CONFIG = ScoringConfig()


def load_criteria(path: Path = CRITERIA_PATH) -> dict:
    """
    Load scoring_criteria.yaml and return its parsed contents.

    Raises FileNotFoundError with a helpful message if the file doesn't exist,
    since it must be generated before the scan pipeline can run.
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Scoring criteria file not found: {path}\n"
            "Generate it first with: python entrypoint.py generate-criteria"
        )
    return yaml.safe_load(path.read_text())


def config_from_criteria(criteria: dict) -> ScoringConfig:
    """
    Build a ScoringConfig from the weights/tolerances sections of a criteria dict.
    CLI overrides can then replace individual fields after this call.
    """
    w = criteria.get("weights", {})
    t = criteria.get("tolerances", {})
    return ScoringConfig(
        weights=ScoringWeights(
            role_fit=w.get("role_fit", 0.40),
            seniority=w.get("seniority", 0.20),
            location_remote=w.get("location_remote", 0.15),
            tech_stack=w.get("tech_stack", 0.15),
            avoid_penalty=w.get("avoid_penalty", 0.10),
        ),
        tolerances=ScoringTolerances(
            min_score_threshold=t.get("min_score_threshold", 0.25),
            top_n_for_llm=t.get("top_n_for_llm", 20),
            salary_below_min_tolerance_pct=t.get("salary_below_min_tolerance_pct", 0.10),
            location_override_role_fit=t.get("location_override_role_fit", 0.80),
            min_title_keyword_hits=t.get("min_title_keyword_hits", 1),
        ),
    )


# ---------------------------------------------------------------------------
# Stage 1: Hard pre-filter (discard without scoring)
# ---------------------------------------------------------------------------

def passes_title_filter(listing: RawListing, criteria: dict) -> bool:
    """
    Apply title_filter rules from the criteria YAML to a listing's title.

    Returns False (discard) if:
      - fewer than criteria.tolerances.min_title_keyword_hits positive keywords match, OR
      - any negative keyword matches.

    Positive/negative keyword lists come from the criteria YAML (which in turn
    can mirror portals.yaml title_filter, or be its own derived list).
    """
    # TODO:
    #   title_lower = listing.title.lower()
    #   negatives = criteria.get("title_filter", {}).get("negative", [])
    #   if any(kw.lower() in title_lower for kw in negatives):
    #       return False
    #   positives = criteria.get("title_filter", {}).get("positive", [])
    #   min_hits = criteria.get("tolerances", {}).get("min_title_keyword_hits", 1)
    #   hits = sum(1 for kw in positives if kw.lower() in title_lower)
    #   return hits >= min_hits
    raise NotImplementedError


def passes_hard_rules(
    listing: RawListing, criteria: dict, config: ScoringConfig
) -> tuple[bool, str]:
    """
    Hard disqualification rules evaluated before scoring begins.

    Returns (True, "") if the listing survives, or (False, reason) if not.

    Rules applied in order (short-circuit on first failure):
      1. Title filter (positive/negative keyword gate).
      2. Avoid hard-disqualify: title matches criteria.avoid.hard_disqualify
         AND zero strong_keywords from criteria.role_fit match the title.
         (A title like "Senior Platform DevOps Engineer" passes; "DevOps Admin" does not.)
    """
    # TODO: implement each rule using criteria dict keys, not hardcoded strings.
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Stage 2: Weighted scoring — all thresholds come from criteria YAML
# ---------------------------------------------------------------------------

def score_role_fit(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    Score 0.0 – 1.0 for archetype alignment.

    Uses criteria.role_fit.exact_archetypes, strong_keywords, weak_keywords.

    Scoring ladder:
      Any exact_archetype found in title         → 1.0
      ≥ 2 strong_keywords found in title         → 0.75
      1 strong_keyword found in title            → 0.50
      Any weak_keyword found (title or desc)     → 0.30
      No match                                   → 0.0
    """
    raise NotImplementedError


def score_seniority(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    Score 0.0 – 1.0 for seniority alignment.

    Uses criteria.seniority.level_scores mapping (keyword → score).
    Checks title words in order of the mapping; first match wins.
    Falls back to the empty-string key score when no level keyword is found.
    """
    raise NotImplementedError


def score_location_remote(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    Score 0.0 – 1.0 for location / remote compatibility.

    Uses criteria.location_remote.patterns (ordered list of {score, match[]})
    and criteria.location_remote.acceptable_onsite_locations.

    Checks listing.location (case-insensitive substring) against each pattern
    in order; first match wins. Falls back to criteria.location_remote.fallback_score
    when listing.location is None or no pattern matches.
    """
    raise NotImplementedError


def score_tech_stack(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    Score 0.0 – 1.0 for tech keyword overlap.

    Uses criteria.tech_stack.keywords and criteria.tech_stack.zero_match_floor.

    score = max(zero_match_floor, matched / total_keywords)

    Only searches listing.description if it has been fetched; for title-only
    listings returns criteria.tech_stack.zero_match_floor as a neutral score.
    """
    raise NotImplementedError


def score_avoid_penalty(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    Score 0.0 – 1.0 where LOW means the listing looks like an avoid role.

    Uses criteria.avoid.hard_disqualify and criteria.avoid.soft_penalise.

    Logic (checks title only — descriptions are noisy for this signal):
      Any hard_disqualify keyword AND zero role_fit strong_keywords → 0.0
      Any soft_penalise keyword                                     → 0.5
      No avoid keywords present                                     → 1.0
    """
    raise NotImplementedError


def score_listing(
    listing: RawListing, criteria: dict, config: ScoringConfig
) -> ScoredListing:
    """
    Run all criteria, compute weighted total, and return a ScoredListing.

    Hard-disqualified listings are returned with disqualified=True and
    total_score=0.0 — the criteria dict is left empty to signal no scoring ran.

    Salary tolerance: if salary_hint is parseable and falls below
    criteria.compensation.minimum * (1 - tolerance.salary_below_min_tolerance_pct),
    total_score is reduced by 15% as a soft penalty.
    """
    # TODO:
    #   passed, reason = passes_hard_rules(listing, criteria, config)
    #   if not passed:
    #       return ScoredListing(listing, {}, 0.0, disqualified=True, disqualify_reason=reason)
    #
    #   scorers = {
    #       "role_fit":       (score_role_fit,       config.weights.role_fit),
    #       "seniority":      (score_seniority,       config.weights.seniority),
    #       "location_remote":(score_location_remote, config.weights.location_remote),
    #       "tech_stack":     (score_tech_stack,      config.weights.tech_stack),
    #       "avoid_penalty":  (score_avoid_penalty,   config.weights.avoid_penalty),
    #   }
    #   criteria_scores = {}
    #   total = 0.0
    #   for name, (fn, weight) in scorers.items():
    #       raw, reason = fn(listing, criteria)
    #       weighted = raw * weight
    #       criteria_scores[name] = CriterionScore(weight, raw, weighted, reason)
    #       total += weighted
    #
    #   # salary soft-penalty (optional, only when salary_hint is parseable)
    #   ...
    #
    #   return ScoredListing(listing, criteria_scores, total)
    raise NotImplementedError


# ---------------------------------------------------------------------------
# Stage 3: Rank and narrow
# ---------------------------------------------------------------------------

def rank_and_narrow(
    listings: list[RawListing],
    criteria: dict,
    config: ScoringConfig,
) -> tuple[list[ScoredListing], list[ScoredListing]]:
    """
    Score all listings, sort descending, split into top-N and the rest.

    Returns (top, rest):
      top  — up to config.tolerances.top_n_for_llm non-disqualified listings
      rest — everything below the cut, including disqualified listings

    Listings with total_score < config.tolerances.min_score_threshold are
    marked disqualified after scoring (they passed hard rules but weren't
    competitive enough to forward to the LLM).
    """
    # TODO:
    #   scored = [score_listing(l, criteria, config) for l in listings]
    #   for s in scored:
    #       if not s.disqualified and s.total_score < config.tolerances.min_score_threshold:
    #           s.disqualified = True
    #           s.disqualify_reason = f"score {s.total_score:.2f} below threshold"
    #   survivors = sorted(
    #       [s for s in scored if not s.disqualified],
    #       key=lambda s: s.total_score, reverse=True,
    #   )
    #   top = survivors[:config.tolerances.top_n_for_llm]
    #   rest = survivors[config.tolerances.top_n_for_llm:] + [s for s in scored if s.disqualified]
    #   return top, rest
    raise NotImplementedError
