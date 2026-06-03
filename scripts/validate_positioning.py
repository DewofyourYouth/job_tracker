#!/usr/bin/env python3
"""
Validate the positioning archetype model against real application history.

Run from the repo root:

    python scripts/validate_positioning.py
    python scripts/validate_positioning.py --applications data/applications.csv
    python scripts/validate_positioning.py --listings data/listings.csv  # optional

What it proves
--------------
For every job title you have actually applied to (data/applications.csv), it
shows which archetype + tier it maps to and what re-rank bias it would receive
under the current ladder (data/scoring-tuning.yaml → archetype_bias). It then
asserts the central safety property the design promises:

    With promotion-only settings, NO title receives a negative bias.

i.e. the positioning model can only ever *raise* a listing's rank, never sink
something you pursued. This is the guard against "encoding aspiration as a hard
down-weight empties the funnel." If you deliberately set negative tier values,
the assertion is skipped and the negatives are reported instead.
"""
from __future__ import annotations

import argparse
import csv
import sys
from collections import Counter
from pathlib import Path

# Make the repo root importable when run as `python scripts/validate_positioning.py`.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402

from classify.positioning import (  # noqa: E402
    archetype_bias,
    bias_ladder_from_tuning,
    load_positioning,
    match_archetype,
)

TUNING_PATHS = [Path("data/scoring-tuning.yaml"), Path("data/scoring-tuning.example.yaml")]


def _load_tuning() -> dict:
    for p in TUNING_PATHS:
        if p.exists():
            print(f"(bias ladder from {p})")
            return yaml.safe_load(p.read_text()) or {}
    print("(bias ladder: built-in defaults)")
    return {}


def _titles_from_csv(path: Path, column: str) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    with path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            title = (row.get(column) or "").strip()
            if title and title.lower() != "unknown":
                rows.append((row.get("Company", "").strip(), title))
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--applications", default="data/applications.csv")
    ap.add_argument("--listings", default=None, help="Optional listings.csv to also score by title.")
    args = ap.parse_args()

    positioning = load_positioning()
    if not positioning:
        print("No positioning config found (data/positioning.yaml). Nothing to validate.")
        return 1
    ladder = bias_ladder_from_tuning(_load_tuning())
    print(
        f"ladder: enabled={ladder.enabled} tier_1={ladder.tier_1} tier_2={ladder.tier_2} "
        f"tier_3={ladder.tier_3} no_match={ladder.no_match} cap={ladder.cap}\n"
    )

    sources = [("applications", Path(args.applications), "Job Title")]
    if args.listings:
        sources.append(("listings", Path(args.listings), "Job Title"))

    all_biases: list[float] = []
    for label, path, column in sources:
        if not path.exists():
            print(f"!! {path} not found — skipping {label}")
            continue
        rows = _titles_from_csv(path, column)
        print(f"=== {label}: {len(rows)} titles ({path}) ===")
        tier_counts: Counter = Counter()
        print(f"{'TIER':<6}{'BIAS':>7}  {'ARCHETYPE':<28}{'COMPANY — TITLE'}")
        print("-" * 100)
        for company, title in rows:
            m = match_archetype(title, positioning)
            bias, _reason = archetype_bias(m, ladder)
            all_biases.append(bias)
            tier = f"T{m.tier}" if (m and m.tier) else "—"
            key = m.key if m else "(no match)"
            tier_counts[tier] += 1
            print(f"{tier:<6}{bias:>+7.3f}  {key:<28}{company} — {title}")
        print()
        print("  tier distribution:", dict(sorted(tier_counts.items())))
        print()

    if not all_biases:
        print("No titles evaluated.")
        return 1

    lo, hi = min(all_biases), max(all_biases)
    print(f"bias range across all titles: [{lo:+.3f}, {hi:+.3f}]")
    negatives = [b for b in all_biases if b < 0]
    promotion_only = all(
        v >= 0 for v in (ladder.tier_1, ladder.tier_2, ladder.tier_3, ladder.no_match)
    )
    if promotion_only:
        assert not negatives, (
            f"FUNNEL-SAFETY VIOLATION: {len(negatives)} titles received a negative bias "
            "under promotion-only settings."
        )
        print(
            "OK — promotion-only: no applied-to title is penalized. The positioning "
            "model can only raise rank, never suppress what you pursued."
        )
    else:
        print(
            f"NOTE — ladder has negative tiers; {len(negatives)} titles receive a "
            "penalty by your explicit choice."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
