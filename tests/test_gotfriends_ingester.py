import unittest

from injest.job_boards import GotFriendsIngester, filter_portals_config, source_counts


class GotFriendsIngesterTests(unittest.TestCase):
    def test_extracts_listings_and_normalizes_israel_locations(self) -> None:
        html = """
        <a href="/jobslobby/software/ai-engineer/">AI Engineer</a>
        <a href="/jobslobby/software/backend-developer/senior-staff-backend-engineer/">Senior/Staff Backend Engineer</a>
        משרה חמה
        מיקום: ת"א והמרכז
        תיאור המשרה:
        החברה מפתחת פלטפורמת בינה מלאכותית וממוקמת בתל אביב.
        דרישות המשרה:
        - 10 שנות ניסיון בפיתוח
        מס' משרה: 153903
        שלחו קורות חיים עכשיו
        <a href="/jobslobby/software/data-engineer/data-engineer/">Data Engineer בחברה בטחונית</a>
        מיקום: השרון
        תיאור המשרה:
        החברה מקימה קבוצת מחקר לפתרונות AI לגבולות המדינה.
        דרישות המשרה:
        - 5 שנות ניסיון כ-Data Engineer
        מס' משרה: 153909
        """

        listings = GotFriendsIngester._extract_listings(
            html,
            "https://www.gotfriends.co.il/jobslobby/software/",
            "GotFriends - Software Israel",
        )

        self.assertEqual(len(listings), 2)
        self.assertEqual(listings[0].title, "Senior/Staff Backend Engineer")
        self.assertEqual(listings[0].location, "Israel - Tel Aviv / Center")
        self.assertEqual(listings[0].raw["job_id"], "153903")
        self.assertEqual(listings[1].location, "Israel - Sharon")

    def test_relocation_region_does_not_look_like_israel_location(self) -> None:
        html = """
        <a href="/jobslobby/software/backend-developer/backend-relocation/">Backend משרת רילוקיישן</a>
        מיקום: אחר
        תיאור המשרה:
        הזדמנות רילוקיישן למערב אירופה.
        דרישות המשרה:
        - 6 שנות ניסיון
        מס' משרה: 142949
        """

        listings = GotFriendsIngester._extract_listings(
            html,
            "https://www.gotfriends.co.il/jobslobby/software/",
            "GotFriends - Software Israel",
        )

        self.assertEqual(len(listings), 1)
        self.assertEqual(listings[0].location, "Other / relocation")

    def test_filter_portals_config_matches_gotfriends_source(self) -> None:
        config = {
            "tracked_companies": [
                {
                    "name": "GotFriends - Software Israel",
                    "careers_url": "https://www.gotfriends.co.il/jobslobby/software/",
                    "scan_method": "gotfriends",
                },
                {
                    "name": "Anthropic",
                    "careers_url": "https://job-boards.greenhouse.io/anthropic",
                    "api": "https://boards-api.greenhouse.io/v1/boards/anthropic/jobs",
                },
            ],
            "search_queries": [
                {"name": "Remote AI", "query": 'site:example.com "AI" remote'},
            ],
        }

        filtered = filter_portals_config(config, ("gotfriends",))

        self.assertEqual(source_counts(filtered), (1, 0))
        self.assertEqual(filtered["tracked_companies"][0]["name"], "GotFriends - Software Israel")


if __name__ == "__main__":
    unittest.main()
