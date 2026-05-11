"""
Job listing ingestion from portals.yaml sources.

Pipeline position: STAGE 1 — produces RawListing objects for classify/rules.py.

Strategy per company (resolved by _pick_strategy):
  greenhouse_api  — company has an `api:` field → JSON from boards-api.greenhouse.io.
  lever_api       — careers_url on jobs.lever.co → JSON from api.lever.co.
  workable_api    — careers_url on apply.workable.com → Workable public REST API.
  ashby_api       — careers_url on jobs.ashbyhq.com → Ashby public posting API.
  websearch       — explicit scan_method: websearch, or unknown ATS →
                    Brave Search API (BRAVE_SEARCH_API_KEY required).

Search queries (portals.yaml search_queries section) always use websearch.
"""

import os
import re
import warnings
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from classify.rules import RawListing

PORTALS_CONFIG_PATH = Path("data/portals.yaml")
PROFILE_CONFIG_PATH = Path("data/profile.yaml")

_FETCH_TIMEOUT = 15   # seconds for all API calls — fail fast, don't wait

_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
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
    """Fetch all jobs from the Greenhouse boards JSON API."""
    try:
        resp = httpx.get(company["api"], headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
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
    """
    slug = _slug_from_url(company["careers_url"])
    api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
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
    """
    slug = _slug_from_url(company["careers_url"])
    api_url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
    payload = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
    try:
        resp = httpx.post(api_url, json=payload, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
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
# Strategy 4: Ashby public API
# ---------------------------------------------------------------------------

def ingest_ashby_api(company: dict) -> list[RawListing]:
    """
    Fetch all jobs from Ashby's public posting API.

    Endpoint: https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true
    Returns JSON with a top-level `jobs` array. No auth, no scraping.
    """
    slug = _slug_from_url(company["careers_url"])
    api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
    try:
        resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        warnings.warn(f"[ashby_api] {company['name']}: {exc}")
        return []

    listings = []
    for job in resp.json().get("jobs", []):
        workplace = job.get("workplaceType")   # "Remote" | "Hybrid" | "OnSite" | null
        location = job.get("location") or ""
        if workplace == "Remote" and location:
            location = f"{location} (Remote)"
        elif workplace == "Remote":
            location = "Remote"
        listings.append(RawListing(
            title=job["title"],
            company=company["name"],
            url=job["jobUrl"],
            location=location or None,
            source=f"ashby_api:{company['name']}",
            raw=job,
        ))
    return listings


# ---------------------------------------------------------------------------
# Strategy 5: Brave Search API
# ---------------------------------------------------------------------------

def _brave_search(query: str, max_results: int = 10) -> list[tuple[str, str]]:
    """
    Search via the Brave Search API and return (title, url) pairs.

    Requires BRAVE_SEARCH_API_KEY in the environment. Returns [] immediately
    on any error — no retries, no sleeps.

    Free tier: 2,000 queries/month. Sign up at https://brave.com/search/api/
    """
    api_key = os.environ.get("BRAVE_SEARCH_API_KEY", "")
    if not api_key:
        warnings.warn(
            "[brave_search] BRAVE_SEARCH_API_KEY not set — websearch disabled. "
            "Get a free key at https://brave.com/search/api/"
        )
        return []

    try:
        resp = httpx.get(
            "https://api.search.brave.com/res/v1/web/search",
            params={"q": query, "count": max_results},
            headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
            timeout=_FETCH_TIMEOUT,
        )
        resp.raise_for_status()
    except Exception as exc:
        warnings.warn(f"[brave_search] {query!r}: {exc}")
        return []

    results = []
    for item in resp.json().get("web", {}).get("results", []):
        title = item.get("title", "")
        url = item.get("url", "")
        if title and url:
            results.append((title, url))
    return results


def _build_websearch_query(company: dict) -> str:
    """Construct a site: query for a company with no explicit scan_query."""
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
    Run a Brave Search query for a portals.yaml entry and return RawListings.

    `query_config` may be a search_queries item {name, query, enabled}
    or a tracked_company item {name, careers_url, scan_query, ...}.
    """
    query = (
        query_config.get("query")
        or query_config.get("scan_query")
        or _build_websearch_query(query_config)
    )
    source_name = query_config.get("name", "search")
    company_name = query_config.get("name", "")

    listings = []
    for title, url in _brave_search(query, max_results=max_results):
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
      4. careers_url on jobs.ashbyhq.com → ashby_api.
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
    if "jobs.ashbyhq.com" in careers_url:
        return "ashby_api"
    explicit = company.get("scan_method")
    if explicit:
        return explicit
    return "websearch"


_STRATEGY_FNS = {
    "greenhouse_api": ingest_greenhouse_api,
    "lever_api":      ingest_lever_api,
    "workable_api":   ingest_workable_api,
    "ashby_api":      ingest_ashby_api,
    "websearch":      ingest_websearch,
    "playwright":     ingest_websearch,   # not implemented; fall through
}


# ---------------------------------------------------------------------------
# Description fetching (called on top-N survivors, not during bulk ingestion)
# ---------------------------------------------------------------------------

def fetch_description(listing: RawListing) -> RawListing:
    """
    Fetch the full job description for a listing and return an updated copy.
    Returns the original listing unchanged on any error.
    """
    try:
        if listing.source.startswith("greenhouse_api:") or _greenhouse_job_parts(listing.url):
            return _fetch_greenhouse_description(listing)
        if listing.source.startswith("lever_api:"):
            return _fetch_lever_description(listing)
        if listing.source.startswith("ashby_api:"):
            return _fetch_ashby_description(listing)
        return _fetch_generic_description(listing)
    except Exception as exc:
        warnings.warn(f"[fetch_description] {listing.url}: {exc}")
        return listing


def _greenhouse_job_parts(url: str) -> tuple[str, str] | None:
    parsed = urlparse(url)
    if "greenhouse.io" not in (parsed.hostname or ""):
        return None
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 3 and parts[-2] == "jobs":
        return parts[0], parts[-1]
    return None


def _fetch_greenhouse_description(listing: RawListing) -> RawListing:
    job_parts = _greenhouse_job_parts(listing.url)
    if job_parts:
        slug, job_id = job_parts
        url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{job_id}"
        resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        loc = data.get("location") or {}
        location = loc.get("name") if isinstance(loc, dict) else None
        html = data.get("content", "")
        raw = {**listing.raw, **data} if isinstance(listing.raw, dict) else data
        return RawListing(**{
            **listing.__dict__,
            "description": _strip_html(html),
            "location": location or listing.location,
            "raw": raw,
        })
    return listing


def _fetch_lever_description(listing: RawListing) -> RawListing:
    parsed = urlparse(listing.url)
    parts = parsed.path.strip("/").split("/")
    if len(parts) >= 2:
        slug, uuid = parts[0], parts[1]
        url = f"https://api.lever.co/v0/postings/{slug}/{uuid}"
        resp = httpx.get(url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
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
    # Ashby API includes descriptionHtml in the job payload
    desc_html = listing.raw.get("descriptionHtml") or listing.raw.get("description")
    if desc_html:
        return RawListing(**{**listing.__dict__, "description": _strip_html(desc_html)})
    return _fetch_generic_description(listing)


def _fetch_generic_description(listing: RawListing) -> RawListing:
    resp = httpx.get(listing.url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT, follow_redirects=True)
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
      1. tracked_companies — direct API per company (highest-quality data).
      2. search_queries    — Brave Search discovery (surfaces unlisted companies).

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
