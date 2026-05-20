"""
Job listing ingestion from portals.yaml sources.

Pipeline position: STAGE 1 — produces RawListing objects for classify/rules.py.

Each source is implemented as an IngestionSource subclass. resolve_ingester()
picks the right one for a given company config entry; BraveSearchIngester is the
catch-all fallback for any source without a recognised ATS URL.

  GreenhouseIngester  — company has an `api:` field → boards-api.greenhouse.io
  LeverIngester       — careers_url on jobs.lever.co → api.lever.co
  WorkableIngester    — careers_url on apply.workable.com → Workable REST API
  AshbyIngester       — careers_url on jobs.ashbyhq.com → Ashby posting API
  RybTechIngester     — careers_url on rybtech.com/open-positions → HTML scrape
  BraveSearchIngester — everything else → Brave Search API (BRAVE_SEARCH_API_KEY required)

Search queries (portals.yaml search_queries section) always use BraveSearchIngester.
"""

from __future__ import annotations

import os
import re
import warnings
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, as_completed
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import yaml

from classify.rules import RawListing

PORTALS_CONFIG_PATH = Path("data/portals.yaml")
PROFILE_CONFIG_PATH = Path("data/profile.yaml")

_FETCH_TIMEOUT = 15
_INGEST_CONCURRENCY = 10

# Used for structured API calls — no Accept-Encoding so servers send plain
# JSON rather than gzip, which httpx only auto-decompresses when the response
# includes a matching Content-Encoding header (not all APIs set it correctly).
_API_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
    "Accept-Language": "en-US,en;q=0.9",
}

# Used for HTML page fetches where compression is fine and expected.
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


def filter_portals_config(portals_config: dict, source_filters: tuple[str, ...]) -> dict:
    """Return a portals config narrowed to source entries matching any filter."""
    needles = tuple(f.strip().casefold() for f in source_filters if f and f.strip())
    if not needles:
        return portals_config

    filtered = dict(portals_config)
    filtered["tracked_companies"] = [
        company
        for company in portals_config.get("tracked_companies", [])
        if _matches_source_filter(company, needles)
    ]
    filtered["search_queries"] = [
        query
        for query in portals_config.get("search_queries", [])
        if _matches_source_filter(query, needles)
    ]
    return filtered


def source_counts(portals_config: dict) -> tuple[int, int]:
    """Return tracked-company and search-query counts for a portals config."""
    return (
        len(portals_config.get("tracked_companies", [])),
        len(portals_config.get("search_queries", [])),
    )


def _matches_source_filter(entry: dict, needles: tuple[str, ...]) -> bool:
    haystack = " ".join(
        str(entry.get(key, ""))
        for key in ("name", "scan_method", "careers_url", "api", "query", "scan_query")
    ).casefold()
    return any(needle in haystack for needle in needles)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _normalise_url(url: str) -> str:
    return url.rstrip("/").split("#")[0]


def _slug_from_url(url: str) -> str:
    return urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]


def _company_name_from_url(url: str) -> str:
    slug = _slug_from_url(url)
    if slug:
        return slug.replace("-", " ").title()
    hostname = urlparse(url).hostname or ""
    parts = hostname.split(".")
    return parts[-2].title() if len(parts) >= 2 else hostname


def _page_url(url: str, page: int) -> str:
    if page <= 1:
        return url
    parsed = urlparse(url)
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    query["page"] = str(page)
    return urlunparse(parsed._replace(query=urlencode(query)))


def _gotfriends_listing_block(tail_html: str) -> str:
    next_anchor = tail_html.lower().find("<a")
    if 0 <= next_anchor < 3000:
        return tail_html[:next_anchor]
    return tail_html[:3500]


def _looks_like_gotfriends_job(title: str, block_html: str) -> bool:
    if not title:
        return False
    title_lower = title.casefold()
    blocked_titles = (
        "דף הבית",
        "דרושים הייטק",
        "שלחו",
        "לכל משרות",
        "תחום",
        "מקצוע",
        "אזור",
    )
    if any(blocked in title_lower for blocked in blocked_titles):
        return False
    block_text = _strip_html(unescape(block_html))
    return "מס' משרה" in block_text and "תיאור המשרה" in block_text


_GOTFRIENDS_LOCATION_LABELS = {
    'ת"א והמרכז': 'Israel - Tel Aviv / Center',
    "תל אביב": "Israel - Tel Aviv",
    "השרון": "Israel - Sharon",
    "ירושלים": "Israel - Jerusalem",
    "חיפה והצפון": "Israel - Haifa / North",
    "באר שבע והדרום": "Israel - Beersheba / South",
    "שפלה": "Israel - Shfela",
    "אילת": "Israel - Eilat",
    "אחר": "Other / relocation",
}


def _extract_gotfriends_location(text: str) -> str | None:
    match = re.search(r"מיקום:\s*([^:]+?)\s+תיאור המשרה:", text)
    if match:
        return " ".join(match.group(1).split())
    match = re.search(r"מיקום:\s*([^\n\r]+?)(?:\s{2,}|תיאור המשרה:|דרישות המשרה:|מס' משרה:)", text)
    if match:
        return " ".join(match.group(1).split())
    return None


def _normalise_gotfriends_location(location: str | None) -> str | None:
    if not location:
        return "Israel"
    if location in _GOTFRIENDS_LOCATION_LABELS:
        return _GOTFRIENDS_LOCATION_LABELS[location]
    if "רילוקיישן" in location or location == "אחר":
        return "Other / relocation"
    return f"Israel - {location}"


def _extract_gotfriends_job_id(text: str) -> str:
    match = re.search(r"מס' משרה:\s*(\d+)", text)
    return match.group(1) if match else ""


def _trim_gotfriends_description(text: str) -> str:
    text = " ".join(text.split())
    stop = text.find("שלחו קורות חיים")
    if stop > 0:
        text = text[:stop]
    return text[:4000]


def _extract_rybtech_location(description: str) -> str:
    text = description.casefold()
    if "relocation" in text and "israel" not in text:
        return "Other / relocation"

    region_patterns = [
        ("Tel Aviv", ("tel aviv", "tel-aviv")),
        ("Jerusalem", ("jerusalem",)),
        ("Shfela", ("shfela", "shefela")),
        ("Sharon", ("sharon",)),
        ("Haifa / North", ("haifa", "north israel", "northern israel")),
        ("Beersheba / South", ("beersheba", "be'er sheva", "south israel", "southern israel")),
    ]
    matched = [label for label, patterns in region_patterns if any(p in text for p in patterns)]
    if matched:
        unique = list(dict.fromkeys(matched))
        suffix = " / ".join(unique[:2])
        location = f"Israel - {suffix}"
    else:
        location = "Israel"

    if "hybrid" in text:
        return f"{location} (Hybrid)"
    if "onsite" in text or "on-site" in text or "5 days/week" in text:
        return f"{location} (Onsite)"
    if "remote" in text:
        return f"{location} (Remote)"
    return location


def _format_rybtech_date(date_text: str) -> str:
    match = re.search(r"(\d{1,2})/(\d{1,2})/(\d{4})", date_text)
    if not match:
        return date_text.strip()
    month, day, year = (int(part) for part in match.groups())
    return f"{year:04d}-{month:02d}-{day:02d}"


# ---------------------------------------------------------------------------
# Base class
# ---------------------------------------------------------------------------

class IngestionSource(ABC):
    """
    Interface for a single job listing source.

    Subclasses implement fetch() for their specific ATS or search mechanism,
    and can_handle() to declare which company config entries they own.
    resolve_ingester() walks the registry in priority order and returns the
    first match; BraveSearchIngester sits last as the catch-all fallback.
    """

    strategy_name: str = ""

    @classmethod
    def can_handle(cls, _config: dict) -> bool:
        """Return True if this source can handle the given config entry."""
        return False

    @abstractmethod
    def fetch(self, config: dict) -> list[RawListing]:
        """Fetch all listings for this config entry."""
        ...


# ---------------------------------------------------------------------------
# Greenhouse
# ---------------------------------------------------------------------------

class GreenhouseIngester(IngestionSource):
    strategy_name = "greenhouse_api"

    @classmethod
    def can_handle(cls, config: dict) -> bool:
        return "api" in config

    def fetch(self, config: dict) -> list[RawListing]:
        try:
            resp = httpx.get(config["api"], headers=_API_HEADERS, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            warnings.warn(f"[greenhouse_api] {config['name']}: {exc}")
            return []

        listings = []
        for job in resp.json().get("jobs", []):
            loc = job.get("location", {})
            location = loc.get("name") if isinstance(loc, dict) else None
            listings.append(RawListing(
                title=job["title"],
                company=config["name"],
                url=job["absolute_url"],
                location=location,
                source=f"greenhouse_api:{config['name']}",
                raw=job,
            ))
        return listings


# ---------------------------------------------------------------------------
# Lever
# ---------------------------------------------------------------------------

class LeverIngester(IngestionSource):
    strategy_name = "lever_api"

    @classmethod
    def can_handle(cls, config: dict) -> bool:
        return "jobs.lever.co" in config.get("careers_url", "")

    def fetch(self, config: dict) -> list[RawListing]:
        slug = _slug_from_url(config["careers_url"])
        api_url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
        try:
            resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            warnings.warn(f"[lever_api] {config['name']}: {exc}")
            return []

        listings = []
        for job in resp.json():
            categories = job.get("categories", {})
            location = categories.get("location") or (
                categories.get("allLocations") or [None]
            )[0]
            listings.append(RawListing(
                title=job["text"],
                company=config["name"],
                url=job["hostedUrl"],
                location=location,
                source=f"lever_api:{config['name']}",
                raw=job,
            ))
        return listings


# ---------------------------------------------------------------------------
# Workable
# ---------------------------------------------------------------------------

class WorkableIngester(IngestionSource):
    strategy_name = "workable_api"

    @classmethod
    def can_handle(cls, config: dict) -> bool:
        return "apply.workable.com" in config.get("careers_url", "")

    def fetch(self, config: dict) -> list[RawListing]:
        slug = _slug_from_url(config["careers_url"])
        api_url = f"https://apply.workable.com/api/v3/accounts/{slug}/jobs"
        payload = {"query": "", "location": [], "department": [], "worktype": [], "remote": []}
        try:
            resp = httpx.post(api_url, json=payload, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            warnings.warn(f"[workable_api] {config['name']}: {exc}")
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
                company=config["name"],
                url=job_url,
                location=location,
                source=f"workable_api:{config['name']}",
                raw=job,
            ))
        return listings


# ---------------------------------------------------------------------------
# Ashby
# ---------------------------------------------------------------------------

class AshbyIngester(IngestionSource):
    strategy_name = "ashby_api"

    @classmethod
    def can_handle(cls, config: dict) -> bool:
        return "jobs.ashbyhq.com" in config.get("careers_url", "")

    def fetch(self, config: dict) -> list[RawListing]:
        slug = _slug_from_url(config["careers_url"])
        api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true"
        try:
            resp = httpx.get(api_url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT)
            resp.raise_for_status()
        except Exception as exc:
            warnings.warn(f"[ashby_api] {config['name']}: {exc}")
            return []

        _WORKPLACE_LABELS = {"Remote": "Remote", "OnSite": "Onsite", "Hybrid": "Hybrid"}
        listings = []
        for job in resp.json().get("jobs", []):
            workplace = job.get("workplaceType")
            location = job.get("location") or ""
            label = _WORKPLACE_LABELS.get(workplace or "", "")
            if label and location:
                location = f"{location} ({label})"
            elif label:
                location = label
            listings.append(RawListing(
                title=job["title"],
                company=config["name"],
                url=job["jobUrl"],
                location=location or None,
                source=f"ashby_api:{config['name']}",
                raw=job,
            ))
        return listings


# ---------------------------------------------------------------------------
# GotFriends
# ---------------------------------------------------------------------------

class GotFriendsIngester(IngestionSource):
    strategy_name = "gotfriends"

    @classmethod
    def can_handle(cls, config: dict) -> bool:
        return "gotfriends.co.il" in config.get("careers_url", "")

    def fetch(self, config: dict) -> list[RawListing]:
        base_url = config["careers_url"]
        max_pages = int(config.get("max_pages", 3))
        company_name = config.get("name", "GotFriends")
        listings: list[RawListing] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            url = _page_url(base_url, page)
            try:
                resp = httpx.get(
                    url,
                    headers=_HTTP_HEADERS,
                    timeout=_FETCH_TIMEOUT,
                    follow_redirects=True,
                )
                resp.raise_for_status()
            except Exception as exc:
                warnings.warn(f"[gotfriends] {company_name} page {page}: {exc}")
                break

            page_listings = self._extract_listings(resp.text, url, company_name)
            new_listings = []
            for listing in page_listings:
                key = _normalise_url(listing.url)
                if key not in seen:
                    seen.add(key)
                    new_listings.append(listing)
            if not new_listings:
                break
            listings.extend(new_listings)

        return listings

    @staticmethod
    def _extract_listings(html_text: str, page_url: str, company_name: str) -> list[RawListing]:
        listings: list[RawListing] = []
        for match in re.finditer(
            r"<a\s+[^>]*href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            html_text,
            re.IGNORECASE | re.DOTALL,
        ):
            href, raw_title = match.groups()
            url = urljoin(page_url, unescape(href))
            if "gotfriends.co.il" not in (urlparse(url).hostname or ""):
                continue
            if "/jobslobby/" not in urlparse(url).path:
                continue

            tail = html_text[match.end():match.end() + 3500]
            block = _gotfriends_listing_block(tail)
            title = _strip_html(unescape(raw_title))
            if not _looks_like_gotfriends_job(title, block):
                continue

            tail_text = _strip_html(unescape(block))
            original_location = _extract_gotfriends_location(tail_text)
            location = _normalise_gotfriends_location(original_location)
            job_id = _extract_gotfriends_job_id(tail_text)
            description = _trim_gotfriends_description(tail_text)
            listings.append(RawListing(
                title=title,
                company=company_name,
                url=_normalise_url(url),
                location=location,
                description=description,
                source=f"gotfriends:{company_name}",
                raw={
                    "job_id": job_id,
                    "location": location,
                    "original_location": original_location,
                    "source_page": page_url,
                },
            ))
        return listings


# ---------------------------------------------------------------------------
# RYB Technologies
# ---------------------------------------------------------------------------

class RybTechIngester(IngestionSource):
    strategy_name = "rybtech"

    @classmethod
    def can_handle(cls, config: dict) -> bool:
        return "rybtech.com" in config.get("careers_url", "")

    def fetch(self, config: dict) -> list[RawListing]:
        base_url = config["careers_url"]
        max_pages = int(config.get("max_pages", 2))
        company_name = config.get("name", "RYB Technologies")
        listings: list[RawListing] = []
        seen: set[str] = set()

        for page in range(1, max_pages + 1):
            url = base_url if page == 1 else urljoin(base_url, f"/open-positions/previous/{page}")
            try:
                resp = httpx.get(
                    url,
                    headers=_HTTP_HEADERS,
                    timeout=_FETCH_TIMEOUT,
                    follow_redirects=True,
                )
                resp.raise_for_status()
            except Exception as exc:
                warnings.warn(f"[rybtech] {company_name} page {page}: {exc}")
                break

            page_listings = self._extract_listings(resp.text, url, company_name)
            new_listings = []
            for listing in page_listings:
                key = _normalise_url(listing.url)
                if key not in seen:
                    seen.add(key)
                    new_listings.append(listing)
            if not new_listings:
                break
            listings.extend(new_listings)

        return listings

    @staticmethod
    def _extract_listings(html_text: str, page_url: str, company_name: str) -> list[RawListing]:
        listings: list[RawListing] = []
        for match in re.finditer(
            r'<div id="blog-post-[^"]+" class="blog-post">(.*?)(?=<div id="blog-post-|<div class="blog-page-nav-previous"|</div>\s*</div>\s*</td>)',
            html_text,
            re.IGNORECASE | re.DOTALL,
        ):
            block = match.group(1)
            title_match = re.search(
                r'<a[^>]*class="[^"]*blog-title-link[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
                block,
                re.IGNORECASE | re.DOTALL,
            )
            if not title_match:
                continue
            href, raw_title = title_match.groups()
            title = _strip_html(unescape(raw_title))
            if not title:
                continue

            date_match = re.search(
                r'<span class="date-text">\s*([^<]+?)\s*</span>',
                block,
                re.IGNORECASE | re.DOTALL,
            )
            content_match = re.search(
                r'<div class="blog-content">\s*(.*?)\s*<div class="blog-social',
                block,
                re.IGNORECASE | re.DOTALL,
            )
            if not content_match:
                content_match = re.search(
                    r'<div class="blog-content">\s*(.*?)\s*<div class="blog-comments-bottom"',
                    block,
                    re.IGNORECASE | re.DOTALL,
                )
            description = _strip_html(unescape(content_match.group(1))) if content_match else ""
            location = _extract_rybtech_location(description)
            listings.append(RawListing(
                title=title,
                company=company_name,
                url=_normalise_url(urljoin(page_url, unescape(href))),
                location=location,
                description=description[:4000],
                source=f"rybtech:{company_name}",
                raw={
                    "date_posted": _format_rybtech_date(date_match.group(1)) if date_match else "",
                    "source_page": page_url,
                },
            ))
        return listings


# ---------------------------------------------------------------------------
# Brave Search (catch-all fallback)
# ---------------------------------------------------------------------------

class BraveSearchIngester(IngestionSource):
    """
    Ingester backed by the Brave Search API.

    Used as the fallback for any company without a recognised structured ATS,
    and for all search_queries entries in portals.yaml.

    Free tier: 2,000 queries/month. Sign up at https://brave.com/search/api/
    Requires BRAVE_SEARCH_API_KEY in the environment.
    """

    strategy_name = "websearch"

    @classmethod
    def can_handle(cls, _config: dict) -> bool:
        return True  # catch-all; always placed last in the registry

    def fetch(self, config: dict, *, max_results: int = 10) -> list[RawListing]:
        query = (
            config.get("query")
            or config.get("scan_query")
            or self._build_query(config)
        )
        source_name = config.get("name", "search")
        company_name = config.get("name", "")

        listings = []
        for title, url in self._search(query, max_results=max_results):
            listings.append(RawListing(
                title=title,
                company=company_name or _company_name_from_url(url),
                url=url,
                source=f"websearch:{source_name}",
            ))
        return listings

    def _search(self, query: str, max_results: int = 10) -> list[tuple[str, str]]:
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

    @staticmethod
    def _build_query(config: dict) -> str:
        parsed = urlparse(config.get("careers_url", ""))
        return f"site:{parsed.netloc}{parsed.path}"


# ---------------------------------------------------------------------------
# Registry and resolution
# ---------------------------------------------------------------------------

# Ordered by specificity — BraveSearchIngester last as the catch-all.
_REGISTRY: list[type[IngestionSource]] = [
    GreenhouseIngester,
    LeverIngester,
    WorkableIngester,
    AshbyIngester,
    GotFriendsIngester,
    RybTechIngester,
    BraveSearchIngester,
]

_BY_NAME: dict[str, type[IngestionSource]] = {
    cls.strategy_name: cls
    for cls in _REGISTRY
    if cls.strategy_name
}


def resolve_ingester(config: dict) -> IngestionSource:
    """
    Return the appropriate IngestionSource for a company or query config.

    If the config has an explicit `scan_method` that matches a known
    strategy_name, that ingester is used directly. Otherwise the registry
    is walked in order and the first class whose can_handle() returns True
    is instantiated.
    """
    explicit = config.get("scan_method")
    if explicit:
        cls = _BY_NAME.get(explicit)
        if cls:
            return cls()

    for cls in _REGISTRY:
        if cls.can_handle(config):
            return cls()

    return BraveSearchIngester()


# ---------------------------------------------------------------------------
# Description fetching
# ---------------------------------------------------------------------------

def fetch_description(listing: RawListing) -> RawListing:
    """Fetch the full job description for a listing and return an updated copy."""
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


def _ashby_location_from_page(html: str, existing: str | None) -> str | None:
    """Parse an Ashby job page to extract Location Type and return an updated location string."""
    lt_match = re.search(
        r'<h2[^>]*>\s*Location\s+Type\s*</h2>\s*<p[^>]*>([^<]+)</p>',
        html, re.IGNORECASE
    )
    if not lt_match:
        return existing
    loc_type = lt_match.group(1).strip().lower()
    _LABELS = {"on-site": "Onsite", "onsite": "Onsite", "hybrid": "Hybrid", "remote": "Remote"}
    label = _LABELS.get(loc_type)
    if not label:
        return existing
    loc_match = re.search(
        r'<h2[^>]*>\s*Location\s*</h2>\s*<p[^>]*>([^<]+)</p>',
        html, re.IGNORECASE
    )
    base = loc_match.group(1).strip() if loc_match else None
    if not base:
        base = re.sub(r'\s*\((?:Remote|Onsite|Hybrid)\)\s*$', '', existing or '', flags=re.IGNORECASE).strip()
    return f"{base} ({label})" if base else label


def _fetch_ashby_description(listing: RawListing) -> RawListing:
    raw = listing.raw if isinstance(listing.raw, dict) else {}
    desc_html = raw.get("descriptionHtml") or raw.get("description")
    location = listing.location

    if not raw.get("workplaceType"):
        # workplaceType missing from API — scrape the job page to determine it
        try:
            resp = httpx.get(listing.url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT, follow_redirects=True)
            resp.raise_for_status()
            html = resp.text
            location = _ashby_location_from_page(html, listing.location)
            if not desc_html:
                return RawListing(**{
                    **listing.__dict__,
                    "description": _strip_html(html)[:4000],
                    "location": location,
                })
        except Exception as exc:
            warnings.warn(f"[ashby_description] {listing.url}: {exc}")

    if desc_html:
        return RawListing(**{**listing.__dict__, "description": _strip_html(desc_html), "location": location})
    return _fetch_generic_description(listing)


def _fetch_generic_description(listing: RawListing) -> RawListing:
    resp = httpx.get(listing.url, headers=_HTTP_HEADERS, timeout=_FETCH_TIMEOUT, follow_redirects=True)
    resp.raise_for_status()
    text = _strip_html(resp.text)
    return RawListing(**{**listing.__dict__, "description": text[:4000]})


def _strip_html(html: str) -> str:
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

    for company in portals_config.get("tracked_companies", []):
        if skip_disabled and not company.get("enabled", True):
            continue
        ingester = resolve_ingester(company)
        try:
            for listing in ingester.fetch(company):
                _add(listing)
        except Exception as exc:
            warnings.warn(f"[ingest] {company.get('name', '?')} ({ingester.strategy_name}): {exc}")

    for query in portals_config.get("search_queries", []):
        if skip_disabled and not query.get("enabled", True):
            continue
        ingester = BraveSearchIngester()
        try:
            for listing in ingester.fetch(query):
                _add(listing)
        except Exception as exc:
            warnings.warn(f"[ingest] search:{query.get('name', '?')}: {exc}")

    if fetch_descriptions:
        results = [fetch_description(r) for r in results]

    return results
