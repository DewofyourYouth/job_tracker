"""Command-level location filtering for listing discovery results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from classify.rules import RawListing, ScoringConfig, score_location_remote


_REMOTE_TERMS = (
    "remote",
    "fully remote",
    "100% remote",
    "work from home",
    "worldwide",
    "distributed",
)
_NON_REMOTE_TERMS = (
    "hybrid",
    "onsite",
    "on-site",
    "in-office",
    "in office",
    "office-based",
)


@dataclass(frozen=True)
class ListingLocationFilter:
    """Opt-in hard filter for acceptable listing locations."""

    include_locations: tuple[str, ...] = ()
    include_remote: bool = False
    min_location_score: float | None = None
    keep_unknown: bool = False

    @property
    def enabled(self) -> bool:
        return bool(
            self.include_locations
            or self.include_remote
            or self.min_location_score is not None
        )


@dataclass(frozen=True)
class LocationFilterStats:
    before: int
    kept: int
    dropped: int
    kept_by_location: int = 0
    kept_by_remote: int = 0
    kept_by_score: int = 0
    kept_unknown: int = 0


def build_location_filter(
    *,
    include_locations: tuple[str, ...] | list[str] = (),
    include_remote: bool = False,
    min_location_score: float | None = None,
    keep_unknown: bool = False,
) -> ListingLocationFilter:
    """Build a normalized location filter from CLI option values."""
    cleaned = tuple(p.strip() for p in include_locations if p and p.strip())
    return ListingLocationFilter(
        include_locations=cleaned,
        include_remote=include_remote,
        min_location_score=min_location_score,
        keep_unknown=keep_unknown,
    )


def listing_location_text(listing: RawListing) -> str:
    """Return searchable location/workplace text from normalized and raw ATS fields."""
    raw = listing.raw if isinstance(listing.raw, dict) else {}
    values = [
        listing.location,
        raw.get("location"),
        raw.get("locations"),
        raw.get("allLocations"),
        raw.get("workplaceType"),
        raw.get("workplace_type"),
        raw.get("workplace"),
        raw.get("workType"),
        raw.get("worktype"),
        raw.get("remotePolicy"),
        raw.get("remote_policy"),
        raw.get("locationType"),
        raw.get("categories"),
        raw.get("office"),
        raw.get("offices"),
    ]
    return " ".join(_flatten_text(values))


def is_remote_listing(listing: RawListing) -> bool:
    """Return True for listings marked remote, excluding hybrid/onsite markers."""
    raw = listing.raw if isinstance(listing.raw, dict) else {}
    if raw.get("remote") is True:
        return True

    text = listing_location_text(listing).casefold()
    if not text:
        return False
    if any(term in text for term in _NON_REMOTE_TERMS):
        return False
    return any(term in text for term in _REMOTE_TERMS)


def filter_listings_by_location(
    listings: list[RawListing],
    policy: ListingLocationFilter,
    *,
    criteria: dict | None = None,
    config: ScoringConfig | None = None,
) -> tuple[list[RawListing], LocationFilterStats]:
    """Apply an opt-in location policy and return kept listings plus counters."""
    if not policy.enabled:
        return listings, LocationFilterStats(
            before=len(listings),
            kept=len(listings),
            dropped=0,
        )

    kept: list[RawListing] = []
    counters = {
        "location": 0,
        "remote": 0,
        "score": 0,
        "unknown": 0,
    }
    for listing in listings:
        reason = _match_reason(listing, policy, criteria=criteria, config=config)
        if reason:
            kept.append(listing)
            counters[reason] += 1

    return kept, LocationFilterStats(
        before=len(listings),
        kept=len(kept),
        dropped=len(listings) - len(kept),
        kept_by_location=counters["location"],
        kept_by_remote=counters["remote"],
        kept_by_score=counters["score"],
        kept_unknown=counters["unknown"],
    )


def format_location_filter_stats(stats: LocationFilterStats) -> str:
    """Return a compact human-readable summary for CLI output."""
    parts = []
    if stats.kept_by_location:
        parts.append(f"{stats.kept_by_location} location phrase")
    if stats.kept_by_remote:
        parts.append(f"{stats.kept_by_remote} remote")
    if stats.kept_by_score:
        parts.append(f"{stats.kept_by_score} location score")
    if stats.kept_unknown:
        parts.append(f"{stats.kept_unknown} unknown")

    detail = f"; kept by {', '.join(parts)}" if parts else ""
    return (
        f"Location filter kept {stats.kept}/{stats.before} listings "
        f"and dropped {stats.dropped}{detail}."
    )


def _match_reason(
    listing: RawListing,
    policy: ListingLocationFilter,
    *,
    criteria: dict | None,
    config: ScoringConfig | None,
) -> str | None:
    text = listing_location_text(listing)
    folded = text.casefold()

    if policy.include_locations and any(
        phrase.casefold() in folded for phrase in policy.include_locations
    ):
        return "location"

    if policy.include_remote and is_remote_listing(listing):
        return "remote"

    if policy.min_location_score is not None:
        if criteria is None or config is None:
            raise ValueError("criteria and config are required for min_location_score")
        score, _reason = score_location_remote(listing, criteria, config)
        if score >= policy.min_location_score:
            return "score"

    if not text and policy.keep_unknown:
        return "unknown"

    return None


def _flatten_text(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = " ".join(value.split())
        return [text] if text else []
    if isinstance(value, bool):
        return []
    if isinstance(value, (int, float)):
        return [str(value)]
    if isinstance(value, dict):
        parts: list[str] = []
        for item in value.values():
            parts.extend(_flatten_text(item))
        return parts
    if isinstance(value, (list, tuple, set)):
        parts: list[str] = []
        for item in value:
            parts.extend(_flatten_text(item))
        return parts
    return [str(value)]
