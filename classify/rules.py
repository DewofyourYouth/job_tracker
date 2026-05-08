"""
Rule-based pre-filter and weighted scoring for job listings.

Pipeline position: STAGE 2 (after ingestion, before LLM evaluation)

All personal scoring parameters are loaded from data/scoring_criteria.yaml.
The Python code here is generic — it implements the algorithm; the YAML
defines what matters for this candidate. Never add personal preferences here.
"""


import re
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
    title: str
    company: str
    url: str
    source: str
    description: Optional[str] = None
    location: Optional[str] = None
    salary_hint: Optional[str] = None
    raw: dict = field(default_factory=dict)


@dataclass
class CriterionScore:
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
# Config — weights and tolerances, loaded from or overridden via criteria YAML
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


def load_criteria(path: Path = CRITERIA_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Scoring criteria not found: {path}\n"
            "Generate it with: python entrypoint.py generate-criteria"
        )
    return yaml.safe_load(path.read_text())


def config_from_criteria(criteria: dict) -> ScoringConfig:
    """Build a ScoringConfig from a loaded criteria dict."""
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
# Matching helpers
# ---------------------------------------------------------------------------

def _phrase_in(phrase: str, text: str) -> bool:
    """Case-insensitive whole-word / whole-phrase match."""
    return bool(re.search(r'\b' + re.escape(phrase) + r'\b', text, re.IGNORECASE))


def _any_phrase_in(phrases: list[str], text: str) -> str | None:
    """Return the first phrase from the list that appears in text, else None."""
    for phrase in phrases:
        if phrase and _phrase_in(phrase, text):
            return phrase
    return None


# ---------------------------------------------------------------------------
# Stage 1: Hard pre-filter
# ---------------------------------------------------------------------------

def passes_title_filter(listing: RawListing, criteria: dict) -> bool:
    """
    Gate on positive keyword presence and negative keyword absence.

    Positive pool: criteria.title_filter.positive if defined, else falls back
    to role_fit.strong_keywords + role_fit.weak_keywords so the filter stays
    coherent when no explicit title_filter section exists in the criteria YAML.

    Negative pool: criteria.title_filter.negative (hard-blocked terms).
    """
    title = listing.title

    tf = criteria.get("title_filter", {})
    negatives = tf.get("negative", [])
    positives = tf.get("positive", [])

    # Fall back to role_fit keywords when no explicit title_filter is defined
    if not positives:
        rf = criteria.get("role_fit", {})
        positives = rf.get("strong_keywords", []) + rf.get("weak_keywords", [])

    if _any_phrase_in(negatives, title):
        return False

    if not positives:
        return True  # nothing to filter on → pass all

    min_hits = criteria.get("tolerances", {}).get("min_title_keyword_hits", 1)
    hits = sum(1 for kw in positives if kw and _phrase_in(kw, title))
    return hits >= min_hits


def passes_hard_rules(
    listing: RawListing, criteria: dict, config: ScoringConfig
) -> tuple[bool, str]:
    """
    Hard disqualification before any scoring runs.

    Rules (short-circuit on first failure):
      1. Title must pass the keyword gate (passes_title_filter).
      2. Avoid hard-disqualify: if any hard_disqualify phrase appears in the title
         AND no role_fit strong_keyword also appears, the listing is a pure avoid-role.
    """
    if not passes_title_filter(listing, criteria):
        return False, f"title failed keyword filter: {listing.title!r}"

    avoid = criteria.get("avoid", {})
    hard = avoid.get("hard_disqualify", [])
    rf = criteria.get("role_fit", {})
    strong = rf.get("strong_keywords", [])

    matched_avoid = _any_phrase_in(hard, listing.title)
    if matched_avoid:
        # Redeem if a strong role keyword is also present ("Platform DevOps Engineer")
        if not _any_phrase_in(strong, listing.title):
            return False, f"avoid role: {matched_avoid!r} in title with no redeeming role keyword"

    return True, ""


# ---------------------------------------------------------------------------
# Stage 2: Weighted scoring
# ---------------------------------------------------------------------------

def score_role_fit(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    0.0 – 1.0 for how closely the title matches target role archetypes.

    Ladder (title checked first, description as fallback for weak signals):
      exact_archetype in title             → 1.0
      ≥2 strong_keywords in title          → 0.75
      1 strong_keyword in title            → 0.50
      any weak_keyword in title or desc    → 0.30
      no match                             → 0.0
    """
    rf = criteria.get("role_fit", {})
    exact = rf.get("exact_archetypes", [])
    strong = rf.get("strong_keywords", [])
    weak = rf.get("weak_keywords", [])
    title = listing.title
    body = f"{title} {listing.description or ''}"

    if _any_phrase_in(exact, title):
        match = _any_phrase_in(exact, title)
        return 1.0, f"exact archetype match: {match!r}"

    strong_hits = [kw for kw in strong if kw and _phrase_in(kw, title)]
    if len(strong_hits) >= 2:
        return 0.75, f"strong keyword matches: {strong_hits}"
    if len(strong_hits) == 1:
        return 0.50, f"strong keyword match: {strong_hits[0]!r}"

    if _any_phrase_in(weak, body):
        match = _any_phrase_in(weak, body)
        return 0.30, f"weak keyword match: {match!r}"

    return 0.0, "no role keyword match"


def score_seniority(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    0.0 – 1.0 for seniority level alignment.

    Iterates criteria.seniority.level_scores in YAML order; first whole-word
    match in the title wins. The empty-string key "" is the no-qualifier fallback.
    """
    level_scores: dict = criteria.get("seniority", {}).get("level_scores", {})
    title = listing.title
    fallback_score = level_scores.get("", 0.4)

    for level, score in level_scores.items():
        if not level:
            continue  # skip fallback key in the main pass
        if _phrase_in(level, title):
            return float(score), f"seniority level: {level!r}"

    return float(fallback_score), "no seniority qualifier found"


def score_location_remote(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    0.0 – 1.0 for location/remote compatibility.

    Acceptable onsite locations (from criteria) get a score floor:
      remote + acceptable location  → 1.0
      hybrid + acceptable location  → 0.85
      onsite + acceptable location  → 0.75

    All other locations are matched against the patterns list in order;
    first substring match wins. Falls back to fallback_score when no
    location is given or nothing matches.
    """
    loc_cfg = criteria.get("location_remote", {})
    patterns = loc_cfg.get("patterns", [])
    fallback = float(loc_cfg.get("fallback_score", 0.50))
    acceptable = [p.lower() for p in loc_cfg.get("acceptable_onsite_locations", [])]

    if not listing.location:
        return fallback, "location not specified"

    loc = listing.location.lower()

    # Acceptable onsite location takes priority over generic pattern matching
    if any(place in loc for place in acceptable):
        if "remote" in loc:
            return 1.0, f"remote in acceptable location ({listing.location})"
        if "hybrid" in loc:
            return 0.85, f"hybrid in acceptable location ({listing.location})"
        return 0.75, f"onsite in acceptable location ({listing.location})"

    # Generic pattern matching (checked in YAML order; first match wins)
    for pattern in patterns:
        score = float(pattern["score"])
        for match_str in (pattern.get("match") or []):
            if match_str and match_str.lower() in loc:
                return score, f"location matches '{match_str}'"

    return fallback, f"location '{listing.location}' — remote status unclear"


def score_tech_stack(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    0.0 – 1.0 for tech keyword overlap with the candidate's stack.

    Searches title + description (if available). Score is:
      max(zero_match_floor, matched_keywords / total_keywords)

    When no description has been fetched (title-only listings), the floor
    is returned as a neutral score — absence of tech keywords in a title
    doesn't mean the role doesn't use that stack.
    """
    ts = criteria.get("tech_stack", {})
    keywords: list[str] = ts.get("keywords", [])
    floor = float(ts.get("zero_match_floor", 0.20))

    if not keywords:
        return floor, "no tech keywords defined"

    if not listing.description:
        return floor, "description not fetched — neutral score"

    body = f"{listing.title} {listing.description}".lower()
    matched = [kw for kw in keywords if kw and kw.lower() in body]
    score = max(floor, len(matched) / len(keywords))
    if matched:
        preview = matched[:4]
        return score, f"{len(matched)}/{len(keywords)} tech keywords: {preview}"
    return floor, "no tech keywords matched (floor applied)"


def score_avoid_penalty(listing: RawListing, criteria: dict) -> tuple[float, str]:
    """
    0.0 – 1.0 where lower means more avoid-role overlap.

    Checks only the title (descriptions are noisy for this signal):
      hard_disqualify phrase in title  → 0.2 (heavy penalty; listing survived
                                              hard filter due to a positive signal)
      soft_penalise keyword in title   → 0.6 (moderate penalty)
      nothing matched                  → 1.0 (clean)
    """
    avoid = criteria.get("avoid", {})
    hard: list[str] = avoid.get("hard_disqualify", [])
    soft: list[str] = avoid.get("soft_penalise", [])
    title = listing.title

    if matched := _any_phrase_in(hard, title):
        return 0.2, f"hard avoid phrase in title: {matched!r}"

    if matched := _any_phrase_in(soft, title):
        return 0.6, f"soft avoid keyword in title: {matched!r}"

    return 1.0, "no avoid keywords"


# ---------------------------------------------------------------------------
# Full listing scorer
# ---------------------------------------------------------------------------

def score_listing(
    listing: RawListing, criteria: dict, config: ScoringConfig
) -> ScoredListing:
    """
    Run hard filter then all weighted criteria; return a ScoredListing.

    Post-scoring adjustments:
      Location override: if role_fit.raw_score >= tolerances.location_override_role_fit,
        the location score is floored at the criteria fallback_score so a perfect-fit
        role in the wrong geography isn't sunk by location alone.

      Salary penalty: if salary_hint is parseable, in the same currency as
        criteria.compensation.currency, and more than salary_below_min_tolerance_pct
        below the minimum, total_score is reduced by 15%.
    """
    passed, reason = passes_hard_rules(listing, criteria, config)
    if not passed:
        return ScoredListing(listing, {}, 0.0, disqualified=True, disqualify_reason=reason)

    scorers = [
        ("role_fit",        score_role_fit,        config.weights.role_fit),
        ("seniority",       score_seniority,        config.weights.seniority),
        ("location_remote", score_location_remote,  config.weights.location_remote),
        ("tech_stack",      score_tech_stack,       config.weights.tech_stack),
        ("avoid_penalty",   score_avoid_penalty,    config.weights.avoid_penalty),
    ]

    criterion_scores: dict[str, CriterionScore] = {}
    for name, fn, weight in scorers:
        raw, reason_str = fn(listing, criteria)
        criterion_scores[name] = CriterionScore(weight, raw, raw * weight, reason_str)

    # Location override: strong role fit forgives a weak location score
    loc_floor = float(
        criteria.get("location_remote", {}).get("fallback_score", 0.50)
    )
    role_raw = criterion_scores["role_fit"].raw_score
    if role_raw >= config.tolerances.location_override_role_fit:
        loc = criterion_scores["location_remote"]
        if loc.raw_score < loc_floor:
            overridden = max(loc.raw_score, loc_floor)
            criterion_scores["location_remote"] = CriterionScore(
                loc.weight, overridden, overridden * loc.weight,
                loc.reason + " [overridden: strong role fit]",
            )

    total = sum(c.weighted for c in criterion_scores.values())

    # Salary soft-penalty (only when hint is parseable and currency matches)
    if listing.salary_hint:
        penalty = _salary_penalty(
            listing.salary_hint,
            criteria.get("compensation", {}),
            config.tolerances.salary_below_min_tolerance_pct,
        )
        if penalty:
            total *= (1.0 - penalty)

    return ScoredListing(listing, criterion_scores, round(total, 4))


def _salary_penalty(hint: str, compensation: dict, tolerance_pct: float) -> float:
    """
    Return a fractional penalty (0.0–0.15) if the salary hint is detectably
    below the minimum after the tolerance band. Returns 0.0 if unparseable
    or if the currency doesn't match (we don't do cross-currency conversion).
    """
    # Only apply when the posting currency matches our criteria currency
    criteria_currency = (compensation.get("currency") or "").upper()
    if criteria_currency and criteria_currency not in hint.upper():
        return 0.0

    m = re.search(r'(\d[\d,]*)', hint.replace(",", ""))
    if not m:
        return 0.0
    value = float(m.group(1).replace(",", ""))
    if "k" in hint.lower():
        value *= 1000

    minimum = float(compensation.get("minimum") or 0)
    if minimum <= 0:
        return 0.0

    floor = minimum * (1.0 - tolerance_pct)
    if value < floor:
        return 0.15
    return 0.0


# ---------------------------------------------------------------------------
# Stage 3: Rank and narrow
# ---------------------------------------------------------------------------

def rank_and_narrow(
    listings: list[RawListing],
    criteria: dict,
    config: ScoringConfig,
) -> tuple[list[ScoredListing], list[ScoredListing]]:
    """
    Score all listings, apply threshold, sort, and split into top-N vs rest.

    Returns (top, rest):
      top  — up to config.tolerances.top_n_for_llm surviving listings, best-first
      rest — everything else (below threshold or hard-disqualified)
    """
    scored = [score_listing(l, criteria, config) for l in listings]

    # Mark below-threshold listings as disqualified
    for s in scored:
        if not s.disqualified and s.total_score < config.tolerances.min_score_threshold:
            s.disqualified = True
            s.disqualify_reason = f"score {s.total_score:.3f} below threshold {config.tolerances.min_score_threshold}"

    survivors = sorted(
        [s for s in scored if not s.disqualified],
        key=lambda s: s.total_score,
        reverse=True,
    )
    disqualified = [s for s in scored if s.disqualified]

    top = survivors[: config.tolerances.top_n_for_llm]
    rest = survivors[config.tolerances.top_n_for_llm :] + disqualified

    return top, rest
