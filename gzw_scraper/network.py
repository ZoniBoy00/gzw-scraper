"""Wiki/network access layer: MediaWiki API calls, throttling, retries, page fetching and category discovery."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Set

import bs4
import requests
import time
import threading
import random
import re
from requests import Response

import logging

logger = logging.getLogger("gzw-scraper")

# ─── Runtime settings (populated by gzw_scraper.config.apply_settings) ───

API_URL: str = ""
HEADERS: Dict[str, str] = {}
MAX_RETRIES: int = 5
BASE_DELAY: float = 1.0
REQUEST_INTERVAL: float = 0.5
CATEGORY_PAGE_LIMIT: int = 500
SKIP_CATEGORIES: Set[str] = set()

# ─── Global rate limiting ───
# Fandom rate-limits aggressively (HTTP 429) when several worker threads hit
# the API at once. All HTTP calls go through a single shared throttle so the
# whole scraper never exceeds ~1/request_interval requests per second.

_request_lock: threading.Lock = threading.Lock()
_last_request_time: float = 0.0


def throttle_request() -> None:
    """Block until at least request_interval seconds since the last HTTP call."""
    global _last_request_time
    with _request_lock:
        now: float = time.monotonic()
        elapsed: float = now - _last_request_time
        if elapsed < REQUEST_INTERVAL:
            time.sleep(REQUEST_INTERVAL - elapsed)
        _last_request_time = time.monotonic()


# ─── Bulletproof API helpers ───

def api_call(params: Dict[str, Any], max_retries: Optional[int] = None) -> Optional[Dict[str, Any]]:
    """Make a MediaWiki API call with exponential backoff retry.

    Args:
        params: Query parameters for the API call.
        max_retries: Maximum number of retries on failure. Defaults to the
            configured MAX_RETRIES when not given.

    Returns:
        Parsed JSON response, or None if all retries failed.
    """
    if max_retries is None:
        max_retries = MAX_RETRIES
    params["format"] = "json"
    last_error: str = ""
    for attempt in range(max_retries):
        try:
            throttle_request()
            r: Response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as exc:
            last_error = f"Timeout: {exc}"
        except requests.exceptions.HTTPError as exc:
            status: int = exc.response.status_code if exc.response is not None else 0
            if status == 429:
                # Long, jittered backoff — the wiki is telling us to slow down
                wait: float = (2 ** attempt) * 10 + random.uniform(0, 2)
                logger.warning("Rate limited (429), waiting %ds...", int(wait))
                time.sleep(wait)
                continue
            last_error = f"HTTP {status}: {exc}"
        except requests.exceptions.ConnectionError as exc:
            last_error = f"Connection error: {exc}"
        except Exception as exc:
            last_error = f"Unknown error: {exc}"

        if attempt < max_retries - 1:
            wait = (2 ** attempt) * BASE_DELAY
            logger.debug(
                "API call failed (attempt %d/%d): %s — retrying in %.1fs",
                attempt + 1, max_retries, last_error, wait,
            )
            time.sleep(wait)

    logger.error("API call failed after %d attempts: %s", max_retries, last_error)
    return None


def safe_get(url: str, max_retries: Optional[int] = None) -> Optional[Response]:
    """Safely fetch a URL with retries.

    Args:
        url: The URL to fetch.
        max_retries: Maximum number of retries. Defaults to the configured
            MAX_RETRIES when not given.

    Returns:
        Response object, or None on failure.
    """
    if max_retries is None:
        max_retries = MAX_RETRIES
    for attempt in range(max_retries):
        try:
            throttle_request()
            r: Response = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r
        except Exception as exc:
            logger.debug("safe_get failed (attempt %d/%d): %s", attempt + 1, max_retries, exc)
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) * BASE_DELAY)
    return None


# ─── Category discovery ───

def get_all_categories() -> List[Dict[str, Any]]:
    """Get ALL categories from the wiki, excluding internal ones.

    Iterates through the wiki's category list using the MediaWiki API.

    Returns:
        List of category objects with 'name', 'title', and 'pages' fields.
    """
    all_cats: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {
        "action": "query",
        "list": "allcategories",
        "aclimit": 500,
        "acprop": "size",
    }
    while True:
        data: Optional[Dict[str, Any]] = api_call(params)
        if not data:
            break
        cats: List[Dict[str, Any]] = data.get("query", {}).get("allcategories", [])
        all_cats.extend(cats)
        cont: Dict[str, Any] = data.get("continue", {})
        if "accontinue" in cont:
            params["accontinue"] = cont["accontinue"]
        else:
            break
    return all_cats


def filter_game_categories(categories: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter out wiki-internal categories, keep only game-relevant ones.

    Args:
        categories: Raw category list from get_all_categories().

    Returns:
        Filtered list of game-relevant categories.
    """
    game_cats: List[Dict[str, Any]] = []
    skip_patterns: List[str] = [
        r"^[A-Z]{2,}_", r"^[a-z]",
    ]
    # NOTE: r"^\d" was removed — all digit-starting categories on this
    # wiki are valid game data (ammo calibers: 5.45x39mm, 7.62x39mm, etc.)
    # Titles containing these words (case-insensitive) are likely wiki infrastructure
    skip_words: List[str] = [
        "template", "maintenance", "formatting", "noindexed", "skin image",
    ]
    # Categories starting with these are non-game
    skip_prefixes: List[str] = [
        "pages using", "pages with", "front page",
    ]

    for cat in categories:
        name: str = cat.get("*", "")
        title: str = name.replace("_", " ")
        title_lower: str = title.lower()
        pages: int = cat.get("size", 0)

        # Skip by exact title match
        if title in SKIP_CATEGORIES:
            continue

        # Skip empty categories
        if pages == 0:
            continue

        # Skip by prefix
        if any(title_lower.startswith(p) for p in skip_prefixes):
            continue

        # Skip by word match in title (catches "X templates", "Y formatting", etc.)
        if any(word in title_lower for word in skip_words):
            continue

        # Skip by regex pattern on wiki name
        if any(re.match(p, name) for p in skip_patterns):
            continue

        # Skip obvious internal prefixes
        if any(name.startswith(p) for p in ["T_", "P_", "F_", "I_", "U_", "H_"]):
            continue

        game_cats.append({"name": name, "title": title, "pages": pages})

    return game_cats


# ─── Page fetching ───

def get_category_members(category: str, limit: Optional[int] = None) -> Optional[List[Dict[str, Any]]]:
    """Get all pages in a wiki category.

    Args:
        category: The wiki category name (without Category: prefix).
        limit: Max pages to fetch per API call. Defaults to the configured
            CATEGORY_PAGE_LIMIT.

    Returns:
        List of page objects with 'title', 'pageid', etc. Returns None if the
        API failed completely (rate limit, wiki down) so callers can tell a
        real empty category apart from a failed fetch — a failed fetch must
        NOT be written as an empty dataset.
    """
    if limit is None:
        limit = CATEGORY_PAGE_LIMIT
    pages: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": min(limit, 500),
        "cmtype": "page",
    }
    api_failed: bool = False
    while True:
        data: Optional[Dict[str, Any]] = api_call(params)
        if not data:
            api_failed = True
            break
        members: List[Dict[str, Any]] = data.get("query", {}).get("categorymembers", [])
        pages.extend(members)
        cont: Dict[str, Any] = data.get("continue", {})
        if "cmcontinue" in cont:
            params["cmcontinue"] = cont["cmcontinue"]
        else:
            break
    if api_failed and not pages:
        return None
    return pages


def parse_page(title: str) -> Optional[bs4.BeautifulSoup]:
    """Get parsed HTML of a wiki page.

    Args:
        title: The wiki page title.

    Returns:
        BeautifulSoup object of the page HTML, or None on failure.
    """
    params: Dict[str, Any] = {
        "action": "parse",
        "page": title,
        "prop": "text",
        "formatversion": "2",
    }
    data: Optional[Dict[str, Any]] = api_call(params)
    if not data:
        return None
    html: str = data.get("parse", {}).get("text", "")
    if not html:
        return None
    try:
        return bs4.BeautifulSoup(html, "lxml")
    except Exception as exc:
        logger.debug("Failed to parse HTML for '%s': %s", title, exc)
        return None


def get_page_image(title: str, soup: Optional[bs4.BeautifulSoup] = None) -> Optional[str]:
    """Get thumbnail URL for a wiki page.

    Uses the MediaWiki pageimages API first, then falls back to
    searching the page HTML for any wiki-hosted image.

    Args:
        title: The wiki page title (used for API lookup).
        soup: Already-parsed page HTML (avoids a second API call).

    Returns:
        Image URL string, or None if not found.
    """
    # Method 1: pageimages API (fast, works for most pages)
    params: Dict[str, Any] = {
        "action": "query",
        "titles": title,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 200,
    }
    data: Optional[Dict[str, Any]] = api_call(params)
    if data:
        for page in data.get("query", {}).get("pages", {}).values():
            if isinstance(page, dict) and page.get("thumbnail"):
                return page["thumbnail"]["source"]

    # Method 2: search parsed page HTML for any wiki-hosted image
    if soup is None:
        soup = parse_page(title)
    if soup:
        for img in soup.find_all("img"):
            src: str = img.get("src", "")
            if "inspect" in src and src.startswith("https://static.wikia.nocookie.net"):
                return src
        for img in soup.find_all("img"):
            src = img.get("src", "")
            if src.startswith("https://static.wikia.nocookie.net") and "icon" in src:
                return src
            if "data-src" in img.attrs:
                data_src: str = img["data-src"]
                if data_src.startswith("https://static.wikia.nocookie.net"):
                    return data_src
    return None
