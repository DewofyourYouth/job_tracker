"""
Rule-based pre-filter and weighted scoring for job listings.

Pipeline position: STAGE 2 (after ingestion, before LLM evaluation)

All personal scoring parameters are loaded from data/scoring-criteria.yaml.
The Python code here is generic — it implements the algorithm; the YAML
defines what matters for this candidate. Never add personal preferences here.
"""


import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import yaml

if TYPE_CHECKING:
    from classify.semantic import SemanticScorer


CRITERIA_PATH = Path("data/scoring-criteria.yaml")
DEFAULT_TUNING_PATH = Path("data/scoring-tuning.yaml")


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
# Config — weights, tolerances, ladders, and adjustments.
# Built-in defaults are overridden by generated criteria and then by tuning YAML.
# ---------------------------------------------------------------------------

@dataclass
class ScoringWeights:
    role_fit: float = 0.40
    seniority: float = 0.20
    location_remote: float = 0.15
    tech_stack: float = 0.15
    avoid_penalty: float = 0.10
    semantic_fit: float = 0.0   # disabled by default; set in criteria/tuning YAML to enable


@dataclass
class ScoringTolerances:
    min_score_threshold: float = 0.35
    top_n_for_llm: int = 100
    salary_below_min_tolerance_pct: float = 0.10
    location_override_role_fit: float = 0.80
    min_title_keyword_hits: int = 1


@dataclass
class RoleFitLadder:
    exact_archetype: float = 1.0
    strong_keyword_multiple: float = 0.75
    strong_keyword_single: float = 0.50
    strong_keyword_multiple_min_hits: int = 2
    weak_keyword: float = 0.30
    no_match: float = 0.0


@dataclass
class AcceptableLocationLadder:
    remote: float = 1.0
    hybrid: float = 0.85
    onsite: float = 0.75


@dataclass
class AvoidPenaltyLadder:
    hard_title: float = 0.20
    soft_title: float = 0.60
    clean: float = 1.0


@dataclass
class TechStackLadder:
    zero_match_floor: float = 0.20


@dataclass
class SalaryAdjustments:
    below_min_penalty: float = 0.15


@dataclass
class ScoringConfig:
    weights: ScoringWeights = field(default_factory=ScoringWeights)
    tolerances: ScoringTolerances = field(default_factory=ScoringTolerances)
    role_fit_ladder: RoleFitLadder = field(default_factory=RoleFitLadder)
    acceptable_location_ladder: AcceptableLocationLadder = field(default_factory=AcceptableLocationLadder)
    avoid_penalty_ladder: AvoidPenaltyLadder = field(default_factory=AvoidPenaltyLadder)
    tech_stack_ladder: TechStackLadder = field(default_factory=TechStackLadder)
    salary_adjustments: SalaryAdjustments = field(default_factory=SalaryAdjustments)
    semantic_scorer: Optional["SemanticScorer"] = None


def load_criteria(path: Path = CRITERIA_PATH) -> dict:
    if not path.exists():
        raise FileNotFoundError(
            f"Scoring criteria not found: {path}\n"
            "Generate it with: python entrypoint.py generate-criteria"
        )
    return yaml.safe_load(path.read_text())


def load_tuning_config(path: Path = DEFAULT_TUNING_PATH, *, required: bool = False) -> dict:
    """
    Load optional user-adjustable score tuning.

    `scoring-criteria.yaml` is generated from the CV/profile and can be
    regenerated. `scoring-tuning.yaml` is hand-edited and overrides numeric
    weights, thresholds, score ladders, and penalties.
    """
    if not path.exists():
        if required:
            raise FileNotFoundError(f"Scoring tuning config not found: {path}")
        return {}

    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a YAML mapping.")
    return data


def _deep_merge(base: dict | None, override: dict | None) -> dict:
    if base is None:
        base = {}
    if override is None:
        override = {}
    if not isinstance(base, dict) or not isinstance(override, dict):
        raise ValueError("Scoring config sections must be YAML mappings.")

    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _float_setting(value, default: float, name: str) -> float:
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be numeric, got {value!r}") from exc


def _int_setting(value, default: int, name: str) -> int:
    if value is None:
        return default
    try:
        return int(float(value))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer, got {value!r}") from exc


def config_from_criteria(criteria: dict, tuning: dict | None = None) -> ScoringConfig:
    """
    Build a ScoringConfig from generated criteria plus optional tuning overrides.

    Merge order:
      1. dataclass defaults,
      2. numeric values in scoring-criteria.yaml,
      3. numeric overrides in scoring-tuning.yaml.
    """
    tuning = tuning or {}
    w = _deep_merge(criteria.get("weights", {}), tuning.get("weights", {}))
    t = _deep_merge(criteria.get("tolerances", {}), tuning.get("tolerances", {}))
    ladders = _deep_merge(
        criteria.get("score_ladders", {}),
        tuning.get("score_ladders", {}),
    )
    adjustments = _deep_merge(
        criteria.get("adjustments", {}),
        tuning.get("adjustments", {}),
    )

    role_fit_ladder = ladders.get("role_fit", {})
    acceptable_location_ladder = ladders.get("acceptable_location", {})
    avoid_penalty_ladder = ladders.get("avoid", {})
    tech_stack_ladder = _deep_merge(
        {"zero_match_floor": criteria.get("tech_stack", {}).get("zero_match_floor", 0.20)},
        ladders.get("tech_stack", {}),
    )
    salary_adjustments = adjustments.get("salary", {})

    return ScoringConfig(
        weights=ScoringWeights(
            role_fit=_float_setting(w.get("role_fit"), 0.40, "weights.role_fit"),
            seniority=_float_setting(w.get("seniority"), 0.20, "weights.seniority"),
            location_remote=_float_setting(
                w.get("location_remote"), 0.15, "weights.location_remote"
            ),
            tech_stack=_float_setting(w.get("tech_stack"), 0.15, "weights.tech_stack"),
            avoid_penalty=_float_setting(w.get("avoid_penalty"), 0.10, "weights.avoid_penalty"),
            semantic_fit=_float_setting(w.get("semantic_fit"), 0.0, "weights.semantic_fit"),
        ),
        tolerances=ScoringTolerances(
            min_score_threshold=_float_setting(
                t.get("min_score_threshold"), 0.35, "tolerances.min_score_threshold"
            ),
            top_n_for_llm=_int_setting(t.get("top_n_for_llm"), 100, "tolerances.top_n_for_llm"),
            salary_below_min_tolerance_pct=_float_setting(
                t.get("salary_below_min_tolerance_pct"),
                0.10,
                "tolerances.salary_below_min_tolerance_pct",
            ),
            location_override_role_fit=_float_setting(
                t.get("location_override_role_fit"),
                0.80,
                "tolerances.location_override_role_fit",
            ),
            min_title_keyword_hits=_int_setting(
                t.get("min_title_keyword_hits"), 1, "tolerances.min_title_keyword_hits"
            ),
        ),
        role_fit_ladder=RoleFitLadder(
            exact_archetype=_float_setting(
                role_fit_ladder.get("exact_archetype"),
                1.0,
                "score_ladders.role_fit.exact_archetype",
            ),
            strong_keyword_multiple=_float_setting(
                role_fit_ladder.get("strong_keyword_multiple"),
                0.75,
                "score_ladders.role_fit.strong_keyword_multiple",
            ),
            strong_keyword_single=_float_setting(
                role_fit_ladder.get("strong_keyword_single"),
                0.50,
                "score_ladders.role_fit.strong_keyword_single",
            ),
            strong_keyword_multiple_min_hits=_int_setting(
                role_fit_ladder.get("strong_keyword_multiple_min_hits"),
                2,
                "score_ladders.role_fit.strong_keyword_multiple_min_hits",
            ),
            weak_keyword=_float_setting(
                role_fit_ladder.get("weak_keyword"), 0.30, "score_ladders.role_fit.weak_keyword"
            ),
            no_match=_float_setting(
                role_fit_ladder.get("no_match"), 0.0, "score_ladders.role_fit.no_match"
            ),
        ),
        acceptable_location_ladder=AcceptableLocationLadder(
            remote=_float_setting(
                acceptable_location_ladder.get("remote"),
                1.0,
                "score_ladders.acceptable_location.remote",
            ),
            hybrid=_float_setting(
                acceptable_location_ladder.get("hybrid"),
                0.85,
                "score_ladders.acceptable_location.hybrid",
            ),
            onsite=_float_setting(
                acceptable_location_ladder.get("onsite"),
                0.75,
                "score_ladders.acceptable_location.onsite",
            ),
        ),
        avoid_penalty_ladder=AvoidPenaltyLadder(
            hard_title=_float_setting(
                avoid_penalty_ladder.get("hard_title"), 0.20, "score_ladders.avoid.hard_title"
            ),
            soft_title=_float_setting(
                avoid_penalty_ladder.get("soft_title"), 0.60, "score_ladders.avoid.soft_title"
            ),
            clean=_float_setting(
                avoid_penalty_ladder.get("clean"), 1.0, "score_ladders.avoid.clean"
            ),
        ),
        tech_stack_ladder=TechStackLadder(
            zero_match_floor=_float_setting(
                tech_stack_ladder.get("zero_match_floor"),
                0.20,
                "score_ladders.tech_stack.zero_match_floor",
            ),
        ),
        salary_adjustments=SalaryAdjustments(
            below_min_penalty=_float_setting(
                salary_adjustments.get("below_min_penalty"),
                0.15,
                "adjustments.salary.below_min_penalty",
            ),
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

def passes_title_filter(
    listing: RawListing, criteria: dict, config: ScoringConfig | None = None
) -> bool:
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

    min_hits = (
        config.tolerances.min_title_keyword_hits
        if config is not None
        else criteria.get("tolerances", {}).get("min_title_keyword_hits", 1)
    )
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
    if not passes_title_filter(listing, criteria, config):
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

def score_role_fit(
    listing: RawListing, criteria: dict, config: ScoringConfig
) -> tuple[float, str]:
    """
    0.0 – 1.0 for how closely the title matches target role archetypes.

    Ladder (title checked first, description as fallback for weak signals):
      exact_archetype in title             → score_ladders.role_fit.exact_archetype
      N strong_keywords in title           → score_ladders.role_fit.strong_keyword_multiple
      1 strong_keyword in title            → score_ladders.role_fit.strong_keyword_single
      any weak_keyword in title or desc    → score_ladders.role_fit.weak_keyword
      no match                             → score_ladders.role_fit.no_match
    """
    rf = criteria.get("role_fit", {})
    ladder = config.role_fit_ladder
    exact = rf.get("exact_archetypes", [])
    strong = rf.get("strong_keywords", [])
    weak = rf.get("weak_keywords", [])
    title = listing.title
    body = f"{title} {listing.description or ''}"

    if _any_phrase_in(exact, title):
        match = _any_phrase_in(exact, title)
        return ladder.exact_archetype, f"exact archetype match: {match!r}"

    strong_hits = [kw for kw in strong if kw and _phrase_in(kw, title)]
    if len(strong_hits) >= ladder.strong_keyword_multiple_min_hits:
        return ladder.strong_keyword_multiple, f"strong keyword matches: {strong_hits}"
    if len(strong_hits) == 1:
        return ladder.strong_keyword_single, f"strong keyword match: {strong_hits[0]!r}"

    if _any_phrase_in(weak, body):
        match = _any_phrase_in(weak, body)
        return ladder.weak_keyword, f"weak keyword match: {match!r}"

    return ladder.no_match, "no role keyword match"


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


def score_location_remote(
    listing: RawListing, criteria: dict, config: ScoringConfig
) -> tuple[float, str]:
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
    ladder = config.acceptable_location_ladder
    patterns = loc_cfg.get("patterns", [])
    fallback = float(loc_cfg.get("fallback_score", 0.50))
    acceptable = [p.lower() for p in loc_cfg.get("acceptable_onsite_locations", [])]

    if not listing.location:
        return fallback, "location not specified"

    loc = listing.location.lower()

    # Acceptable onsite location takes priority over generic pattern matching
    if any(place in loc for place in acceptable):
        if "remote" in loc:
            return ladder.remote, f"remote in acceptable location ({listing.location})"
        if "hybrid" in loc:
            return ladder.hybrid, f"hybrid in acceptable location ({listing.location})"
        return ladder.onsite, f"onsite in acceptable location ({listing.location})"

    # Generic pattern matching (checked in YAML order; first match wins)
    for pattern in patterns:
        score = float(pattern["score"])
        for match_str in (pattern.get("match") or []):
            if match_str and match_str.lower() in loc:
                return score, f"location matches '{match_str}'"

    return fallback, f"location '{listing.location}' — remote status unclear"


def score_tech_stack(
    listing: RawListing, criteria: dict, config: ScoringConfig
) -> tuple[float, str]:
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
    floor = float(config.tech_stack_ladder.zero_match_floor)

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


def score_avoid_penalty(
    listing: RawListing, criteria: dict, config: ScoringConfig
) -> tuple[float, str]:
    """
    0.0 – 1.0 where lower means more avoid-role overlap.

    Checks only the title (descriptions are noisy for this signal):
      hard_disqualify phrase in title  → 0.2 (heavy penalty; listing survived
                                              hard filter due to a positive signal)
      soft_penalise keyword in title   → 0.6 (moderate penalty)
      nothing matched                  → 1.0 (clean)
    """
    avoid = criteria.get("avoid", {})
    ladder = config.avoid_penalty_ladder
    hard: list[str] = avoid.get("hard_disqualify", [])
    soft: list[str] = avoid.get("soft_penalise", [])
    title = listing.title

    if matched := _any_phrase_in(hard, title):
        return ladder.hard_title, f"hard avoid phrase in title: {matched!r}"

    if matched := _any_phrase_in(soft, title):
        return ladder.soft_title, f"soft avoid keyword in title: {matched!r}"

    return ladder.clean, "no avoid keywords"


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
        if name == "seniority":
            raw, reason_str = fn(listing, criteria)
        else:
            raw, reason_str = fn(listing, criteria, config)
        criterion_scores[name] = CriterionScore(weight, raw, raw * weight, reason_str)

    # Semantic fit — only runs when a scorer is attached and weight > 0
    if config.semantic_scorer is not None and config.weights.semantic_fit > 0:
        weight = config.weights.semantic_fit
        raw, reason_str = config.semantic_scorer.score(listing)
        criterion_scores["semantic_fit"] = CriterionScore(weight, raw, raw * weight, reason_str)

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
            config.salary_adjustments.below_min_penalty,
        )
        if penalty:
            total *= (1.0 - penalty)

    return ScoredListing(listing, criterion_scores, round(total, 4))


def _salary_penalty(
    hint: str,
    compensation: dict,
    tolerance_pct: float,
    below_min_penalty: float,
) -> float:
    """
    Return a fractional penalty if the salary hint is detectably
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
        return below_min_penalty
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
