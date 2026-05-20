import unittest

from classify.location_filter import (
    ListingLocationFilter,
    filter_listings_by_location,
    is_remote_listing,
)
from classify.rules import RawListing, ScoringConfig


def listing(title: str, location: str | None = None, raw: dict | None = None) -> RawListing:
    return RawListing(
        title=title,
        company="Example",
        url=f"https://example.com/{title.replace(' ', '-').lower()}",
        source="test",
        location=location,
        raw=raw or {},
    )


class LocationFilterTests(unittest.TestCase):
    def test_phrase_or_remote_filter_keeps_israel_and_remote(self) -> None:
        policy = ListingLocationFilter(
            include_locations=("Israel", "Tel Aviv"),
            include_remote=True,
        )
        listings = [
            listing("Platform Engineer", "Tel Aviv, Israel"),
            listing("AI Engineer", "Remote"),
            listing("Backend Engineer", "Dublin, Ireland"),
        ]

        kept, stats = filter_listings_by_location(listings, policy)

        self.assertEqual([item.location for item in kept], ["Tel Aviv, Israel", "Remote"])
        self.assertEqual(stats.kept_by_location, 1)
        self.assertEqual(stats.kept_by_remote, 1)
        self.assertEqual(stats.dropped, 1)

    def test_remote_filter_excludes_hybrid_or_onsite(self) -> None:
        self.assertTrue(is_remote_listing(listing("AI Engineer", "Remote")))
        self.assertTrue(is_remote_listing(listing("AI Engineer", raw={"remote": True})))
        self.assertFalse(is_remote_listing(listing("AI Engineer", "Hybrid - London")))
        self.assertFalse(is_remote_listing(listing("AI Engineer", "Remote / Hybrid - London")))
        self.assertFalse(is_remote_listing(listing("AI Engineer", "On-site - New York")))

    def test_min_location_score_reuses_criteria_location_rules(self) -> None:
        criteria = {
            "location_remote": {
                "patterns": [
                    {"score": 1.0, "match": ["remote", "worldwide"]},
                    {"score": 0.1, "match": ["onsite", "on-site"]},
                ],
                "fallback_score": 0.5,
                "acceptable_onsite_locations": ["Israel", "Tel Aviv"],
            }
        }
        policy = ListingLocationFilter(min_location_score=0.75)
        listings = [
            listing("Platform Engineer", "Tel Aviv"),
            listing("AI Engineer", "Remote"),
            listing("Backend Engineer", "Dublin, Ireland"),
        ]

        kept, stats = filter_listings_by_location(
            listings,
            policy,
            criteria=criteria,
            config=ScoringConfig(),
        )

        self.assertEqual([item.location for item in kept], ["Tel Aviv", "Remote"])
        self.assertEqual(stats.kept_by_score, 2)
        self.assertEqual(stats.dropped, 1)

    def test_keep_unknown_only_applies_when_filter_enabled(self) -> None:
        policy = ListingLocationFilter(include_remote=True, keep_unknown=True)
        kept, stats = filter_listings_by_location(
            [listing("Unknown Location", None)],
            policy,
        )

        self.assertEqual(len(kept), 1)
        self.assertEqual(stats.kept_unknown, 1)


if __name__ == "__main__":
    unittest.main()
