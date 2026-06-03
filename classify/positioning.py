"""
Positioning / archetype matching for job listings and CV generation.

This module is the single reader of ``data/positioning.yaml`` (see that file's
header for the full contract). It is consumed by two callers:

  * ``classify/rules.py``  — turns ``tier`` into a small, bounded, promotion-only
    re-rank bias and lets ``match_titles`` widen the hard title gate.
  * ``commands/apply.py``  — turns the matched archetype into CV framing
    (headline, lead pillar, emphasis, proof points) plus global guardrails.

Design rules:
  * Pure functions over a loaded dict — no file IO except ``load_positioning``.
  * Never raises on missing/empty config: an absent file yields ``{}`` and every
    helper degrades to a no-op so the rest of the pipeline is unaffected.
  * No import from ``classify.rules`` (rules imports this — keep it one-way).
"""

from __future__ import annotations

import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path

import yaml

POSITIONING_PATH = Path("data/positioning.yaml")
# Legacy misspelled filename kept as a fallback for one migration cycle.
LEGACY_POSITIONING_PATH = Path("data/postioning.yaml")


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def load_positioning(path: Path | None = None) -> dict:
    """
    Load positioning config.

    Resolution order:
      1. explicit ``path`` if given,
      2. ``data/positioning.yaml``,
      3. ``data/postioning.yaml`` (legacy typo) — with a deprecation warning.

    Returns ``{}`` when no file exists, which disables every positioning feature
    without breaking the pipeline.
    """
    if path is not None:
        candidates = [Path(path)]
    else:
        candidates = [POSITIONING_PATH, LEGACY_POSITIONING_PATH]

    for candidate in candidates:
        if candidate.exists():
            if candidate == LEGACY_POSITIONING_PATH:
                warnings.warn(
                    "Loading positioning from legacy 'data/postioning.yaml'. "
                    "Rename it to 'data/positioning.yaml'.",
                    DeprecationWarning,
                    stacklevel=2,
                )
            data = yaml.safe_load(candidate.read_text()) or {}
            if not isinstance(data, dict):
                raise ValueError(f"{candidate} must contain a YAML mapping.")
            return data
    return {}


# ---------------------------------------------------------------------------
# Bias ladder — numeric knobs (loaded from scoring-tuning.yaml: archetype_bias)
# ---------------------------------------------------------------------------

@dataclass
class ArchetypeBiasLadder:
    """
    Soft, bounded re-rank bias applied to the rule score by tier.

    Defaults are PROMOTION-ONLY: tier-1/tier-2 get a small positive nudge,
    while tier-3 and no-match are neutral (0.0). This guarantees the bias can
    only ever *raise* a listing's rank, never sink something the candidate
    actually pursues. Set negatives only if you explicitly want to penalize.
    """
    enabled: bool = True
    tier_1: float = 0.15
    tier_2: float = 0.06
    tier_3: float = 0.0
    no_match: float = 0.0
    cap: float = 0.20  # max absolute bias before the final score clamp


def bias_ladder_from_tuning(tuning: dict | None) -> ArchetypeBiasLadder:
    """Build an ArchetypeBiasLadder from a scoring-tuning.yaml ``archetype_bias`` block."""
    cfg = (tuning or {}).get("archetype_bias", {}) or {}

    def _f(key: str, default: float) -> float:
        value = cfg.get(key)
        if value is None:
            return default
        try:
            return float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"archetype_bias.{key} must be numeric, got {value!r}") from exc

    enabled = cfg.get("enabled", True)
    return ArchetypeBiasLadder(
        enabled=bool(enabled),
        tier_1=_f("tier_1", 0.15),
        tier_2=_f("tier_2", 0.06),
        tier_3=_f("tier_3", 0.0),
        no_match=_f("no_match", 0.0),
        cap=_f("cap", 0.20),
    )


# ---------------------------------------------------------------------------
# Archetype match
# ---------------------------------------------------------------------------

@dataclass
class ArchetypeMatch:
    key: str                       # e.g. "forward_deployed_engineer"; "" for no-match fallback
    tier: int | None               # 1/2/3, or None when no archetype matched
    headline: str = ""
    lead_pillar: str = ""
    support_pillars: list[str] = field(default_factory=list)
    emphasis: str = ""
    matched_title: str = ""        # the match_titles phrase that fired
    is_fallback: bool = False      # True when populated from default_when_no_match


def _phrase_in(phrase: str, text: str) -> bool:
    """Case-insensitive whole-word / whole-phrase match (mirrors classify.rules)."""
    if not phrase:
        return False
    return bool(re.search(r"\b" + re.escape(phrase) + r"\b", text, re.IGNORECASE))


def match_archetype(title: str, positioning: dict) -> ArchetypeMatch | None:
    """
    Return the best archetype match for a job title, or ``None`` if none match.

    Precedence when several archetypes match: lowest ``tier`` wins; ties broken
    by the longest matched phrase (more specific). Returns ``None`` (not the
    fallback) so the caller decides whether to use ``default_when_no_match``.
    """
    overrides = (positioning or {}).get("archetype_overrides", {}) or {}
    if not title or not overrides:
        return None

    best: ArchetypeMatch | None = None
    best_rank: tuple[int, int] | None = None  # (tier, -len(matched)) — lower is better

    for key, entry in overrides.items():
        if not isinstance(entry, dict):
            continue
        matched_phrase = ""
        for phrase in entry.get("match_titles", []) or []:
            if _phrase_in(phrase, title) and len(phrase) > len(matched_phrase):
                matched_phrase = phrase
        if not matched_phrase:
            continue

        tier = entry.get("tier")
        tier_int = int(tier) if isinstance(tier, (int, float)) else 99
        rank = (tier_int, -len(matched_phrase))
        if best_rank is None or rank < best_rank:
            best_rank = rank
            best = ArchetypeMatch(
                key=key,
                tier=tier_int if tier is not None else None,
                headline=entry.get("headline", ""),
                lead_pillar=entry.get("lead_pillar", ""),
                support_pillars=list(entry.get("support_pillars", []) or []),
                emphasis=entry.get("emphasis", ""),
                matched_title=matched_phrase,
            )
    return best


def fallback_match(positioning: dict) -> ArchetypeMatch:
    """Build an ArchetypeMatch from ``default_when_no_match`` (for the apply step)."""
    dflt = (positioning or {}).get("default_when_no_match", {}) or {}
    return ArchetypeMatch(
        key="",
        tier=None,
        headline=dflt.get("headline", ""),
        lead_pillar=dflt.get("lead_pillar", ""),
        emphasis=dflt.get("note", ""),
        is_fallback=True,
    )


def title_matches_any_archetype(title: str, positioning: dict) -> bool:
    """True if the title matches any archetype's ``match_titles`` (used to widen the title gate)."""
    return match_archetype(title, positioning) is not None


# ---------------------------------------------------------------------------
# Bias computation
# ---------------------------------------------------------------------------

def archetype_bias(match: ArchetypeMatch | None, ladder: ArchetypeBiasLadder) -> tuple[float, str]:
    """
    Return ``(signed_bias, reason)`` for a match under the given ladder.

    The bias is clamped to ``±ladder.cap``. With default (promotion-only)
    settings the bias is >= 0 for tier-1/2 and 0 for tier-3/no-match.
    """
    if not ladder.enabled:
        return 0.0, "archetype bias disabled"

    if match is None or match.tier is None:
        bias = ladder.no_match
        label = "no archetype match"
    elif match.tier == 1:
        bias = ladder.tier_1
        label = f"{match.key} (tier 1, '{match.matched_title}')"
    elif match.tier == 2:
        bias = ladder.tier_2
        label = f"{match.key} (tier 2, '{match.matched_title}')"
    else:
        bias = ladder.tier_3
        label = f"{match.key} (tier {match.tier}, '{match.matched_title}')"

    cap = abs(ladder.cap)
    bias = max(-cap, min(cap, bias))
    sign = "+" if bias >= 0 else ""
    return bias, f"{label} {sign}{bias:.3f}"


def tier_display_score(match: ArchetypeMatch | None) -> float:
    """A 0–1 representation of tier strength for display in the score breakdown."""
    if match is None or match.tier is None:
        return 0.0
    return {1: 1.0, 2: 0.66, 3: 0.33}.get(match.tier, 0.0)
