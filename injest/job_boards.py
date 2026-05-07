"""
Job listing ingestion from portals.yaml sources.

Pipeline position: STAGE 1 — produces RawListing objects for classify/rules.py.

Strategy per company (resolved by _pick_strategy):
  greenhouse_api  — company has an `api:` field → JSON from boards-api.greenhouse.io.
  lever_api       — careers_url on jobs.lever.co → JSON from api.lever.co.
  workable_api    — careers_url on apply.workable.com → Workable public REST API.
  ashby_scrape    — careers_url on jobs.ashbyhq.com → parse window.__appData JSON
                    embedded in the HTML (server-side rendered by Next.js).
  websearch       — explicit scan_method: websearch, or unknown ATS →
                    DuckDuckGo HTML search using the company's scan_query.

Search queries (portals.yaml search_queries section) always use websearch.
"""

from __future__ import annotations

import json
import re
import time
import warnings
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import httpx
import yaml

from classify.rules import RawListing

PORTALS_CONFIG_PATH = Path("data/portals.yaml")
PROFILE_CONFIG_PATH = Path("data/profile.yaml")

_DDG_RATE_LIMIT_SECS = 2.5

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# Config loading
# ---------------------------------------------------------------------------

def load_portals_config(path: Path = PORTALS_CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text())


def load_profile(path: Path = PROFILE_CONFIG_PATH) -> dict:
    return yaml.safe_load(path.read_text())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _normalise_url(url: str) -> str:
    """Strip trailing slash and fragment for deduplication."""
    return url.rstrip("/").split("#")[0]


def _slug_from_url(url: str) -> str:
    """Return the last non-empty path segment of a URL."""
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


# ---------------------------------------------------------------------------
# Strategy 1: Greenhouse API
# ---------------------------------------------------------------------------

def ingest_greenhouse_api(company: dict) -> list[RawListing]:
    """
    Fetch all jobs from the Greenhouse boards JSON API.

    Works for both job-boards.greenhouse.io and job-boards.eu.greenhouse.io;
    the slug and absolute_url in the response handle the difference transparently.
    """
    try:
        resp = httpx.get(company["api"], headers=_HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        warnings.warn(f"[greenhouse_api] {company['name']}: {exc}")
        return []

    listings = []
    for job in resp.json().get("jobs", []):
        loc = job.get("location", {})
        location = loc.get("name") if isinstance(loc, dict) else None
        listings.append(RawListing(
            title=job["title"],
            company=company["name"],
            url=job["absolute_url"],
            location=location,
            source=f"greenhouse_api:{company['name']}",
            raw=job,
        ))
    return listings


# ---------------------------------------------------------------------------
# Strategy 2: Lever API
# ---------------------------------------------------------------------------

def ingest_lever_api(company: dict) -> list[RawListing]:
    """
    Fetch all jobs from the public Lever postings API.

    Endpoint: https://api.lever.co/v0/postings/{slug}?mode=json
    Each job has: text (title), hostedUrl, categories.location.
    """
    slug = _slug_from_url(company["careers_url"])
    api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        warnings.warn(f"[lever_api] {company['name']}: {exc}")
        return []

    listings = []
    for job in resp.json():
        categories = job.get("categories", {})
        location = categories.get("location") or (
            categories.get("allLocations") or [None]
        )[0]
        listings.append(RawListing(
            title=job["text"],
            company=company["name"],
            url=job["hostedUrl"],
            location=location,
            source=f"lever_api:{company['name']}",
            raw=job,
        ))
    return listings


# ---------------------------------------------------------------------------
# Strategy 3: Workable API
# ---------------------------------------------------------------------------

def ingest_workable_api(company: dict) -> list[RawListing]:
    """
    Fetch all jobs from the Workable public jobs API.

    Endpoint: POST https://apply.workable.com/api/v3/accounts/{slug}/jobs
    The location field is a dict: {city, country, countryCode, region}.
    """
    slug = _slug_from_url(company["careers_url"])
    api_url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    payload = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
    try:
        resp = httpx.post(api_url, json=payload, headers=_HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as exc:
        warnings.warn(f"[workable_api] {company['name']}: {exc}")
        return []

    listings = []
    for job in resp.json().get("results", []):
        loc = job.get("location") or {}
        city = loc.get("city") if isinstance(loc, dict) else None
        country = loc.get("country") if isinstance(loc, dict) else None
        remote_str = "Remote" if job.get("remote") else None
        location = ", ".join(p for p in [city, country, remote_str] if p) or None
        job_url = f"https://apply.workable.com/{slug}/j/{job['shortcode']}/"
        listings.append(RawListing(
            title=job["title"],
            company=company["name"],
            url=job_url,
            location=location,
            source=f"workable_api:{company['name']}",
            raw=job,
        ))
    return listings


# ---------------------------------------------------------------------------
# Strategy 4: Ashby scrape (window.__appData)
# ---------------------------------------------------------------------------

def ingest_ashby_scrape(company: dict) -> list[RawListing]:
    """
    Fetch all jobs from an Ashby-hosted career page.

    Ashby career pages are Next.js SSR and embed all job data in a
    `window.__appData = {...}` script tag. We parse that JSON directly —
    no GraphQL, no headless browser required.

    The relevant path in the JSON is:
      window.__appData.jobBoard.jobPostings[]
        .id           → used to build the canonical job URL
        .title
        .locationName
        .workplaceType → "Remote" | "Hybrid" | "OnSite" | null
    """
    careers_url = company["careers_url"]
    try:
        resp = httpx.get(careers_url, headers=_HTTP_HEADERS, timeout=20, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:
        warnings.warn(f"[ashby_scrape] {company['name']}: {exc}")
        return []

    app_data = _extract_ashby_app_data(resp.text)
    if app_data is None:
        warnings.warn(f"[ashby_scrape] {company['name']}: could not parse window.__appData")
        return []

    slug = _slug_from_url(careers_url)
    postings = (app_data.get("jobBoard") or {}).get("jobPostings", [])

    listings = []
    for job in postings:
        workplace = job.get("workplaceType")  # "Remote" | "Hybrid" | "OnSite" | null
        location_name = job.get("locationName")
        if workplace == "Remote" and location_name:
            location = f"{location_name} (Remote)"
        elif workplace == "Remote":
            location = "Remote"
        else:
            location = location_name
        job_url = f"https://jobs.ashbyhq.com/{slug}/{job['id']}"
        listings.append(RawListing(
            title=job["title"],
            company=company["name"],
            url=job_url,
            location=location,
            source=f"ashby_scrape:{company['name']}",
            raw=job,
        ))
    return listings


def _extract_ashby_app_data(html: str) -> dict | None:
    """
    Pull the JSON value from `window.__appData = <JSON>;` in a page's script tags.
    Returns None if the pattern isn't found or the JSON is malformed.
    """
    # Find all script tag contents and look for the one with __appData
    scripts = re.findall(r"<script[^>]*>(.*?)</script>", html, re.DOTALL)
    for script in scripts:
        m = re.match(r"\s*window\.__appData\s*=\s*", script)
        if not m:
            continue
        try:
            data, _ = json.JSONDecoder().raw_decode(script[m.end():])
            return data
        except (json.JSONDecodeError, ValueError):
            continue
    return None


# ---------------------------------------------------------------------------
# Strategy 5: DuckDuckGo websearch
# ---------------------------------------------------------------------------

_ddg_last_request: float = 0.0


def _ddg_search(query: str, max_results: int = 10) -> list[tuple[str, str]]:
    """
    Search DuckDuckGo's HTML interface and return (title, url) pairs.

    Uses the POST form endpoint which is more stable than GET.
    DDG embeds result links as:
      <a class="result__a" href="//duckduckgo.com/l/?uddg=<encoded_url>&rut=...">Title</a>
    We decode the uddg parameter to get the real URL.
    """
    global _ddg_last_request
    elapsed = time.monotonic() - _ddg_last_request
    if elapsed < _DDG_RATE_LIMIT_SECS:
        time.sleep(_DDG_RATE_LIMIT_SECS - elapsed)

    try:
        resp = httpx.post(
            "https://html.duckduckgo.com/html/",
            data={"q": query},
            headers={**_HTTP_HEADERS, "Content-Type": "application/x-www-form-urlencoded"},
            timeout=20,
            follow_redirects=True,
        )
        _ddg_last_request = time.monotonic()
        resp.raise_for_status()
    except Exception as exc:
        warnings.warn(f"[ddg_search] {query!r}: {exc}")
        _ddg_last_request = time.monotonic()
        return []

    return _parse_ddg_results(resp.text, max_results)


def _parse_ddg_results(html: str, max_results: int) -> list[tuple[str, str]]:
    """
    Extract (title, url) pairs from DuckDuckGo HTML results using regex.

    DDG result links look like:
      <a class="result__a" href="//duckduckgo.com/l/?uddg=<pct-encoded-url>&rut=...">Title text</a>
    Attribute order varies (class may appear before or after href).
    """
    # Match <a> tags that carry both class="result__a" and an href, in any order.
    # Lookaheads let us capture both attributes regardless of order.
    pattern = re.compile(
        r'<a\b'
        r'(?=[^>]*\bclass=["\']result__a["\'])'   # must have class=result__a
        r'(?=[^>]*\bhref=["\']((?://duckduckgo\.com/l/\?[^"\']+)|(?:https?://[^"\']+))["\'])'  # and href
        r'[^>]*>'
        r'([^<]+)'      # title text (text-only child, no nested tags in DDG titles)
        r'</a>',
        re.IGNORECASE,
    )

    results: list[tuple[str, str]] = []
    for m in pattern.finditer(html):
        raw_href = m.group(1)
        title = re.sub(r"\s+", " ", m.group(2)).strip()
        url = _ddg_resolve_url(raw_href)
        if url and "duckduckgo.com" not in url and title:
            results.append((title, url))
        if len(results) >= max_results:
            break
    return results


def _ddg_resolve_url(href: str) -> str:
    """Decode a DDG redirect href to the real destination URL."""
    if not href:
        return ""
    if "duckduckgo.com/l/" in href:
        full = href if href.startswith("http") else "https:" + href
        params = parse_qs(urlparse(full).query)
        uddg = params.get("uddg", [""])[0]
        return unquote(uddg) if uddg else ""
    return href if href.startswith("http") else "https:" + href


def _build_websearch_query(company: dict) -> str:
    """
    Construct a site: query for a company with no explicit scan_query.
    E.g. jobs.ashbyhq.com/pinecone → 'site:jobs.ashbyhq.com/pinecone'
    """
    parsed = urlparse(company.get("careers_url", ""))
    return f"site:{parsed.netloc}{parsed.path}"


def _company_name_from_url(url: str) -> str:
    """Infer a readable company name from a job-board URL path segment."""
    slug = _slug_from_url(url)
    if slug:
        return slug.replace("-", " ").title()
    hostname = urlparse(url).hostname or ""
    parts = hostname.split(".")
    return parts[-2].title() if len(parts) >= 2 else hostname


def ingest_websearch(query_config: dict, *, max_results: int = 10) -> list[RawListing]:
    """
    Run a DuckDuckGo search for a portals.yaml entry and return RawListings.

    `query_config` may be either:
      - a search_queries item: {name, query, enabled}
      - a tracked_company item: {name, careers_url, scan_query, ...}
    """
    query = (
        query_config.get("query")
        or query_config.get("scan_query")
        or _build_websearch_query(query_config)
    )
    source_name = query_config.get("name", "search")
    company_name = query_config.get("name", "")

    listings = []
    for title, url in _ddg_search(query, max_results=max_results):
        listings.append(RawListing(
            title=title,
            company=company_name or _company_name_from_url(url),
            url=url,
            source=f"websearch:{source_name}",
        ))
    return listings


# ---------------------------------------------------------------------------
# Strategy selection
# ---------------------------------------------------------------------------

def _pick_strategy(company: dict) -> str:
    """
    Determine the ingestion strategy for a tracked company.

    Priority:
      1. `api:` field present → greenhouse_api.
      2. careers_url on jobs.lever.co → lever_api.
      3. careers_url on apply.workable.com → workable_api.
      4. careers_url on jobs.ashbyhq.com (and no explicit scan_method) → ashby_scrape.
      5. Explicit scan_method field → honour it.
      6. Default → websearch.
    """
    if "api" in company:
        return "greenhouse_api"
    careers_url = company.get("careers_url", "")
    if "jobs.lever.co" in careers_url:
        return "lever_api"
    if "apply.workable.com" in careers_url:
        return "workable_api"
    # Honour explicit scan_method before falling back to Ashby detection
    explicit = company.get("scan_method")
    if explicit:
        return explicit
    if "jobs.ashbyhq.com" in careers_url:
        return "ashby_scrape"
    return "websearch"


_STRATEGY_FNS = {
    "greenhouse_api": ingest_greenhouse_api,
    "lever_api":      ingest_lever_api,
    "workable_api":   ingest_workable_api,
    "ashby_scrape":   ingest_ashby_scrape,
    "websearch":      ingest_websearch,
    # playwright not implemented; fall through to websearch
    "playwright":     ingest_websearch,
}


# ---------------------------------------------------------------------------
# Description fetching (called on top-N survivors, not during bulk ingestion)
# ---------------------------------------------------------------------------

def fetch_description(listing: RawListing) -> RawListing:
    """
    Fetch the full job description for a listing and return an updated copy.

    Strategies by source prefix:
      greenhouse_api → boards-api.greenhouse.io/v1/boards/{slug}/jobs/{id} (content field)
      lever_api      → api.lever.co/v0/postings/{slug}/{uuid} (description + lists)
      ashby_scrape   → re-use the raw.descriptionHtml if already embedded, else GET the URL
      other          → GET the URL and strip HTML

    Returns the original listing unchanged on any error.
    """
    try:
        if listing.source.startswith("greenhouse_api:"):
            return _fetch_greenhouse_description(listing)
        if listing.source.startswith("lever_api:"):
            return _fetch_lever_description(listing)
        if listing.source.startswith("ashby_scrape:"):
            return _fetch_ashby_description(listing)
        return _fetch_generic_description(listing)
    except Exception as exc:
        warnings.warn(f"[fetch_description] {listing.url}: {exc}")
        return listing


def _fetch_greenhouse_description(listing: RawListing) -> RawListing:
    parsed = urlparse(listing.url)
    parts = parsed.path.strip("/").split("/")
    # path: {slug}/jobs/{id}
    if len(parts) >= 3 and parts[-2] == "jobs":
        slug, job_id = parts[0], parts[-1]
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
        resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
        html = resp.json().get("content", "")
        return RawListing(**{**listing.__dict__, "description": _strip_html(html)})
    return listing


def _fetch_lever_description(listing: RawListing) -> RawListing:
    parsed = urlparse(listing.url)
    parts = parsed.path.strip("/").split("/")
    # path: {slug}/{uuid}
    if len(parts) >= 2:
        slug, uuid = parts[0], parts[1]
        url = f"https://api.lever.co/v0/postings/{slug}/{uuid}"
        resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        parts_text = [_strip_html(data.get("description", ""))]
        for section in data.get("lists", []):
            parts_text.append(section.get("text", ""))
            for item in section.get("content", []):
                parts_text.append(f"- {_strip_html(item)}")
        return RawListing(**{**listing.__dict__, "description": "\n".join(p for p in parts_text if p)})
    return listing


def _fetch_ashby_description(listing: RawListing) -> RawListing:
    # If the raw payload already has the description HTML, use it
    desc_html = listing.raw.get("descriptionHtml") or listing.raw.get("description")
    if desc_html:
        return RawListing(**{**listing.__dict__, "description": _strip_html(desc_html)})
    # Otherwise GET the individual job page
    return _fetch_generic_description(listing)


def _fetch_generic_description(listing: RawListing) -> RawListing:
    resp = httpx.get(listing.url, headers=_HTTP_HEADERS, timeout=20, follow_redirects=True)
    resp.raise_for_status()
    text = _strip_html(resp.text)
    return RawListing(**{**listing.__dict__, "description": text[:4000]})


def _strip_html(html: str) -> str:
    """Remove HTML tags and collapse whitespace."""
    text = re.sub(r"<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------

def ingest_all(
    portals_config: dict,
    *,
    fetch_descriptions: bool = False,
    skip_disabled: bool = True,
) -> list[RawListing]:
    """
    Run all enabled ingestion sources and return a deduplicated list of RawListings.

    Execution order:
      1. tracked_companies — direct API or scrape per company (highest-quality data).
      2. search_queries    — broad DDG discovery (may surface unlisted companies).

    Tracked-company results win deduplication: if the same URL appears from
    both a direct API and a search query, the API version is kept.
    """
    seen_urls: set[str] = set()
    results: list[RawListing] = []

    def _add(listing: RawListing) -> None:
        key = _normalise_url(listing.url)
        if key not in seen_urls:
            seen_urls.add(key)
            results.append(listing)

    # Phase 1: tracked companies
    for company in portals_config.get("tracked_companies", []):
        if skip_disabled and not company.get("enabled", True):
            continue
        strategy = _pick_strategy(company)
        fn = _STRATEGY_FNS.get(strategy, ingest_websearch)
        try:
            for listing in fn(company):
                _add(listing)
        except Exception as exc:
            warnings.warn(f"[ingest] {company.get('name', '?')} ({strategy}): {exc}")

    # Phase 2: search queries
    for query in portals_config.get("search_queries", []):
        if skip_disabled and not query.get("enabled", True):
            continue
        try:
            for listing in ingest_websearch(query):
                _add(listing)
        except Exception as exc:
            warnings.warn(f"[ingest] search:{query.get('name', '?')}: {exc}")

    if fetch_descriptions:
        results = [fetch_description(r) for r in results]

    return results
