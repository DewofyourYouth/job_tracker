import unittest

from injest.job_boards import RybTechIngester, filter_portals_config, source_counts


class RybTechIngesterTests(unittest.TestCase):
    def test_extracts_weebly_blog_jobs_with_location_and_date(self) -> None:
        html = """
        <div id="blog-post-868454787137050801" class="blog-post">
          <div class="blog-header">
            <h2 class="blog-title">
              <a class="blog-title-link blog-link" href="//www.rybtech.com/open-positions/product-manager-779760">Product Manager 779760</a>
            </h2>
            <p class="blog-date"><span class="date-text">5/11/2026</span></p>
          </div>
          <div class="blog-content">
            <div class="paragraph">
              The Role We are seeking a Product Manager for a B2B SaaS marketplace.
              Job location: Tel Aviv (hybrid)
              Please send CV to jobs@example.com
            </div>
            <div class="blog-social"></div>
          </div>
        </div>
        <div id="blog-post-133729638544782885" class="blog-post">
          <div class="blog-header">
            <h2 class="blog-title">
              <a class="blog-title-link blog-link" href="/open-positions/enterprise-systems-support-team-lead-779265">Enterprise Systems Support Team Lead 779265</a>
            </h2>
            <p class="blog-date"><span class="date-text">5/10/2026</span></p>
          </div>
          <div class="blog-content">
            <div class="paragraph">
              Join a global fintech company building a Jerusalem R&D center.
              Location: Jerusalem office — onsite, 5 days/week.
            </div>
            <div class="blog-social"></div>
          </div>
        </div>
        <div class="blog-page-nav-previous">
          <a href="/open-positions/previous/2" class="blog-link">&lt;&lt;Previous</a>
        </div>
        """

        listings = RybTechIngester._extract_listings(
            html,
            "https://www.rybtech.com/open-positions",
            "RYB Technologies",
        )

        self.assertEqual(len(listings), 2)
        self.assertEqual(listings[0].title, "Product Manager 779760")
        self.assertEqual(listings[0].location, "Israel - Tel Aviv (Hybrid)")
        self.assertEqual(listings[0].raw["date_posted"], "2026-05-11")
        self.assertEqual(
            listings[1].url,
            "https://www.rybtech.com/open-positions/enterprise-systems-support-team-lead-779265",
        )
        self.assertEqual(listings[1].location, "Israel - Jerusalem (Onsite)")

    def test_filter_portals_config_matches_rybtech_source(self) -> None:
        config = {
            "tracked_companies": [
                {
                    "name": "RYB Technologies - Open Positions Israel",
                    "careers_url": "https://www.rybtech.com/open-positions",
                    "scan_method": "rybtech",
                },
                {
                    "name": "GotFriends - Software Israel",
                    "careers_url": "https://www.gotfriends.co.il/jobslobby/software/",
                    "scan_method": "gotfriends",
                },
            ],
            "search_queries": [],
        }

        filtered = filter_portals_config(config, ("rybtech",))

        self.assertEqual(source_counts(filtered), (1, 0))
        self.assertEqual(filtered["tracked_companies"][0]["name"], "RYB Technologies - Open Positions Israel")


if __name__ == "__main__":
    unittest.main()
