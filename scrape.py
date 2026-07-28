"""
GZW Wiki Scraper v4 — Configurable & Bulletproof
==================================================
Automatically discovers ALL game categories from the wiki and scrapes every page.

Features:
  - Auto-discovers new categories (Crafting, Ammo types, etc.)
  - Validates all data before saving
  - Retry + exponential backoff for all API calls
  - Preserves existing data if scrape fails entirely
  - Backups previous data before overwriting
  - Skips wiki-internal categories (Images, Templates, Users, etc.)
  - Config-driven via config.toml
  - Parallel scraping for better performance
  - Progress bar with tqdm
  - Type hints throughout
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import bs4
import requests
from requests import Response

# Try to load config; fall back to defaults if config.toml is missing
try:
    import tomllib
except ImportError:
    # Python <3.11 fallback — use toml if available
    try:
        import tomllib as toml  # type: ignore[no-redef]
    except ImportError:
        tomllib = None  # type: ignore[assignment]

try:
    from tqdm import tqdm
except ImportError:
    tqdm = None  # type: ignore[assignment]

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gzw-scraper")

# ─── Config loading ───

CONFIG_PATH = Path(__file__).parent / "config.toml"

def load_config(config_path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Load configuration from TOML file, with fallback defaults.

    Args:
        config_path: Path to the TOML configuration file.

    Returns:
        Dictionary with all config values merged from file and defaults.
    """
    defaults: Dict[str, Any] = {
        "wiki": {
            "api_url": "https://gray-zone-warfare.fandom.com/api.php",
            "user_agent": "GZW-Tools/4.0 (community tool; github.com/ZoniBoy00/gzw-tools)",
        },
        "scraper": {
            "max_retries": 3,
            "base_delay": 1.0,
            "page_delay": 0.5,
            "max_safe_deviation": 0.7,
            "category_page_limit": 500,
            "max_workers": 4,
        },
        "output": {
            "directory": "data",
            "backup_directory": "data_backup",
        },
        "skip_categories": {
            "infrastructure": [
                "Images", "Image", "Videos", "Video", "Audio", "Audio files",
                "Templates", "Template", "Template documentation",
                "Users", "User", "User blog", "User blog comment",
                "Blog posts", "Blog listing", "Blog feed",
                "Files", "File",
                "Pages", "Pages with",
                "Articles", "Stubs", "Disambiguation",
                "Candidates for deletion", "Protected pages",
                "Infobox templates", "Navigation templates",
                "Featured articles", "Good articles",
                "Pages with broken file links",
                "Categories", "Category",
                "Need images", "Pages with missing images",
                "Pages with unavailable images",
                "Redlinks", "Broken redirects",
                "Community", "Help",
                "Real world", "Staff", "Administration",
                "Screenshots", "Concept art",
                "Gameplay", "Multiplayer",
                "Documentation templates",
                "Notice templates",
                "Image license templates",
                "Pages missing details",
                "Images needing improvement",
                "Citation needed",
                "Verification needed",
                "Archive",
                "Maps",
                "Removed Content",
                "Upcoming Content",
                "Newspaper",
                "Gray Zone Warfare Wiki",
                "Evidence",
                "Newspapers",
                "Factions",
                "Regions",
                # Wiki infrastructure leaks
                "Front page", "Basics", "Characters", "Locations", "Media",
                "Maintenance", "Your locker",
                # Template families
                "Navbox templates", "Section formatting templates",
                "Formatting templates", "General wiki templates",
                "Auxiliary templates", "Design template", "Quote templates",
                "Link Template",
                # Wiki technical
                "Noindexed pages", "Wiki skin images", "Wiki maintenance",
                "Front page sections",
                "Pages using duplicate arguments in template calls",
                "Image and media templates",
                "Pages missing details",
                "Candidates for deletion",
            ],
        },
        "category_to_filename": {
            "Weapons": "weapons",
            "Armor Vest": "vests",
            "Helmet": "helmets",
            "Headwear": "helmets",
            "Throwables": "throwables",
            "Weapon Parts": "weapon_parts",
            "Magazines": "magazines",
            "Night Vision Devices": "night_vision",
            "Helmet Mods": "helmet_mods",
            "Helmet Mounts": "helmet_mounts",
            "Weapons camouflage": "weapon_camos",
            "Military Equipment": "military_equipment",
            "Face Cover": "face_cover",
            "Tactical Rigs": "rigs",
            "Loot Containers": "loot_containers",
            "Task Item": "task_items",
            "Repair Kits": "repair_kits",
            "Medical Item": "medical",
            "Tool": "tools",
            "Muzzle Devices": "muzzle_devices",
            "Stock Adapters": "stock_adapters",
            "Pistol Grips": "pistol_grips",
            "Night vision": "night_vision",
            "Main task": "tasks",
            "Side task": "tasks",
            "Barrels": "barrels",
            "Foregrips": "foregrips",
            "Stocks": "stocks",
            "Suppressors": "suppressors",
            # Ammo: master category + all caliber subcategories → merged into ammo.json
            "Ammunition": "ammo",
            ".222 Remington ammunition": "ammo",
            ".300 AAC Blackout ammunition": "ammo",
            ".45 ACP Ammunition": "ammo",
            ".SX 4.6x30 ammunition": "ammo",
            "12-Gauge ammunition": "ammo",
            "5.45x39mm ammunition": "ammo",
            "5.56x45mm ammunition": "ammo",
            "7.62x25mm ammunition": "ammo",
            "7.62x39mm ammunition": "ammo",
            "7.62x51mm ammunition": "ammo",
            "7.62x54mmR Ammunition": "ammo",
            "7.62x54mm R Ammunition": "ammo",
            "7.65 Browning ammunition": "ammo",
            "7.65mm Browning ammunition": "ammo",
            "9x19mm ammunition": "ammo",
            "4.6x30mm": "ammo",
        },
        "listing_pages": {
            "loot_items": "Loot",
            "apparel_items": "Apparel",
            "provisions": "Provisions",
        },
    }

    if not config_path.exists():
        logger.warning("config.toml not found at %s — using defaults", config_path)
        return defaults

    try:
        with open(config_path, "rb") as fh:
            user_config = tomllib.load(fh)
    except Exception as exc:
        logger.warning("Failed to load config.toml: %s — using defaults", exc)
        return defaults

    # Deep-merge user config into defaults
    return _deep_merge(defaults, user_config)


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge two dictionaries. Override values take precedence."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


# ─── Load config on module import ───
CONFIG = load_config()

# Extract top-level config values for easy access
WIKI_CONFIG = CONFIG["wiki"]
SCRAPER_CONFIG = CONFIG["scraper"]
OUTPUT_CONFIG = CONFIG["output"]
SKIP_CATEGORIES: Set[str] = set(CONFIG["skip_categories"]["infrastructure"])
CATEGORY_TO_FILENAME: Dict[str, str] = CONFIG["category_to_filename"]
LISTING_PAGES: Dict[str, str] = CONFIG["listing_pages"]

API_URL: str = WIKI_CONFIG["api_url"]
HEADERS: Dict[str, str] = {
    "User-Agent": WIKI_CONFIG["user_agent"],
}
MAX_RETRIES: int = SCRAPER_CONFIG["max_retries"]
BASE_DELAY: float = SCRAPER_CONFIG["base_delay"]
PAGE_DELAY: float = SCRAPER_CONFIG["page_delay"]
MAX_SAFE_DEVIATION: float = SCRAPER_CONFIG["max_safe_deviation"]
CATEGORY_PAGE_LIMIT: int = SCRAPER_CONFIG["category_page_limit"]
MAX_WORKERS: int = SCRAPER_CONFIG.get("max_workers", 4)

OUTPUT_DIR: Path = Path(__file__).parent / OUTPUT_CONFIG["directory"]
BACKUP_DIR: Path = Path(__file__).parent / OUTPUT_CONFIG["backup_directory"]
OUTPUT_DIR.mkdir(exist_ok=True)


# ─── Bulletproof API helpers ───

def api_call(params: Dict[str, Any], max_retries: int = MAX_RETRIES) -> Optional[Dict[str, Any]]:
    """Make a MediaWiki API call with exponential backoff retry.

    Args:
        params: Query parameters for the API call.
        max_retries: Maximum number of retries on failure.

    Returns:
        Parsed JSON response, or None if all retries failed.
    """
    params["format"] = "json"
    last_error: str = ""
    for attempt in range(max_retries):
        try:
            r: Response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as exc:
            last_error = f"Timeout: {exc}"
        except requests.exceptions.HTTPError as exc:
            status: int = exc.response.status_code if exc.response is not None else 0
            if status == 429:
                wait: float = (2 ** attempt) * 5
                logger.warning("Rate limited (429), waiting %ds...", wait)
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


def safe_get(url: str, max_retries: int = MAX_RETRIES) -> Optional[Response]:
    """Safely fetch a URL with retries.

    Args:
        url: The URL to fetch.
        max_retries: Maximum number of retries.

    Returns:
        Response object, or None on failure.
    """
    for attempt in range(max_retries):
        try:
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


# ─── Page fetching & parsing ───

def get_category_members(category: str, limit: int = CATEGORY_PAGE_LIMIT) -> List[Dict[str, Any]]:
    """Get all pages in a wiki category.

    Args:
        category: The wiki category name (without Category: prefix).
        limit: Max pages to fetch per API call.

    Returns:
        List of page objects with 'title', 'pageid', etc.
    """
    pages: List[Dict[str, Any]] = []
    params: Dict[str, Any] = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": min(limit, 500),
        "cmtype": "page",
    }
    while True:
        data: Optional[Dict[str, Any]] = api_call(params)
        if not data:
            break
        members: List[Dict[str, Any]] = data.get("query", {}).get("categorymembers", [])
        pages.extend(members)
        cont: Dict[str, Any] = data.get("continue", {})
        if "cmcontinue" in cont:
            params["cmcontinue"] = cont["cmcontinue"]
        else:
            break
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


def sanitize_value(value: str) -> str:
    """Clean up common wiki formatting artifacts in scraped values.

    Fixes issues like duplicated units (kgkg, m/s m/s), double percent
    signs, and inconsistent whitespace.
    """
    # Collapse whitespace first
    v: str = re.sub(r'\s+', ' ', value).strip()
    # Fix duplicated units: "0.01 kgkg" → "0.01 kg", "830 m/s m/s" → "830 m/s"
    v = re.sub(r'\b(kg|g|lb|oz)\1\b', r'\1', v, flags=re.IGNORECASE)
    v = re.sub(r'\b(m/s)\s*\1\b', r'\1', v, flags=re.IGNORECASE)
    # Fix double percent: "+3% %" → "+3%", "-2% %" → "-2%"
    v = re.sub(r'%(\s*%)+', '%', v)
    # Remove trailing/leading whitespace again after fixes
    return v.strip()


def parse_infobox(soup: Optional[bs4.BeautifulSoup]) -> Dict[str, str]:
    """Extract key-value pairs from a portable infobox, safely.

    Args:
        soup: BeautifulSoup object of a wiki page.

    Returns:
        Dictionary of infobox field -> value, plus optional '_image' key.
    """
    data: Dict[str, str] = {}
    if not soup:
        return data
    try:
        infobox = (
            soup.find("aside", class_=lambda c: c and "portable-infobox" in str(c))
            if soup else None
        )
        if not infobox:
            return data
        for data_item in infobox.find_all("div", class_="pi-data"):
            try:
                label_el = data_item.find("h3", class_="pi-data-label")
                value_el = data_item.find("div", class_="pi-data-value")
                if label_el and value_el:
                    label: str = label_el.get_text(" ", strip=True).lower().replace(" ", "_")
                    value: str = value_el.get_text(" ", strip=True)
                    value = sanitize_value(value)
                    data[label] = value
            except Exception:
                continue
        # Get image from infobox (including image collections)
        try:
            # Try direct img first (most common)
            img = infobox.find("img")
            if img and img.get("src"):
                src = img["src"]
                if "data-src" in img.attrs and ("base64" in src or not src.startswith("http")):
                    src = img["data-src"]
                data["_image"] = src
            else:
                # Try pi-image-collection (some pages use this)
                collection = infobox.find("div", class_="pi-image-collection")
                if collection:
                    img = collection.find("img")
                    if img and img.get("src"):
                        src = img["src"]
                        if "data-src" in img.attrs and ("base64" in src or not src.startswith("http")):
                            src = img["data-src"]
                        data["_image"] = src
        except Exception:
            pass
    except Exception as exc:
        logger.debug("parse_infobox error: %s", exc)
    return data


# ─── Universal scraper ───

def scrape_category(name: str, title: str) -> List[Dict[str, Any]]:
    """Scrape ANY game category with a universal parser.

    Args:
        name: Category name on the wiki (e.g. 'Weapons').
        title: Human-readable name for logging.

    Returns:
        List of scraped item dictionaries.
    """
    logger.info("Scraping: %s...", title)
    try:
        pages: List[Dict[str, Any]] = get_category_members(name, limit=CATEGORY_PAGE_LIMIT)
    except Exception as exc:
        logger.warning("  Failed to get members for '%s': %s", name, exc)
        return []

    if not pages:
        logger.info("  No pages found in '%s'", title)
        return []

    items: List[Dict[str, Any]] = []
    skipped: int = 0

    # Use tqdm for progress if available
    page_iter = pages
    if tqdm:
        page_iter = tqdm(pages, desc=f"  {title}", leave=False, unit="page")

    for page in page_iter:
        page_title: str = page["title"]

        # Skip non-article pages
        if page_title.startswith("Category:") or page_title.startswith("Template:") or page_title.startswith("User:"):
            skipped += 1
            continue

        try:
            soup: Optional[bs4.BeautifulSoup] = parse_page(page_title)
            info: Dict[str, str] = parse_infobox(soup)

            item: Dict[str, Any] = {
                "name": page_title,
                "id": page_title.lower().replace(" ", "-").replace("'", "").replace("(", "").replace(")", ""),
            }

            # Universal field extraction — grab every field the infobox has
            for wiki_key, val in info.items():
                if wiki_key == "_image":
                    item["image"] = val
                elif wiki_key in ("id", "name"):
                    continue
                else:
                    item[wiki_key] = val

            # Get image if not already found (use parsed soup to avoid extra API call)
            if "image" not in item:
                img: Optional[str] = info.get("_image") or get_page_image(page_title, soup)
                if img:
                    item["image"] = img

            items.append(item)
        except Exception as exc:
            logger.debug("  Error scraping '%s': %s", page_title, exc)
            skipped += 1

        # Rate limiting — be nice to the wiki
        time.sleep(PAGE_DELAY)

    if skipped:
        logger.info("  %s: %d items (+ %d skipped)", title, len(items), skipped)
    else:
        logger.info("  %s: %d items", title, len(items))

    return items


def scrape_listing_page(key: str, page_title: str, existing_names: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
    """Scrape items from a listing page (wikitable-based).

    Some categories (Loot, Apparel) have items ONLY in tables
    on their listing pages, not as individual wiki pages.

    Args:
        key: Output key for the dataset (e.g. 'loot_items').
        page_title: Wiki page title containing the listing tables.
        existing_names: Optional set of already-known item names to avoid duplicates.

    Returns:
        List of scraped item dictionaries.
    """
    logger.info("Listing page: %s -> %s...", page_title, key)
    soup: Optional[bs4.BeautifulSoup] = parse_page(page_title)
    if not soup:
        logger.warning("  Could not parse '%s'", page_title)
        return []

    items: List[Dict[str, Any]] = []
    seen_names: Set[str] = set(existing_names) if existing_names else set()

    for table in soup.find_all("table", class_=re.compile(r"wikitable|article-table|sortable|fandom-table")):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers: List[str] = []
        for cell in rows[0].find_all(["th", "td"]):
            text: str = cell.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            headers.append(text.lower())

        has_name: bool = any("name" in h for h in headers)
        has_icon: bool = any("icon" in h for h in headers)
        if not has_name and not has_icon:
            continue

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            row_data: Dict[str, str] = {}
            img_url: str = ""
            for j, cell in enumerate(cells):
                col_name: str = headers[j] if j < len(headers) else f"col_{j}"
                text = cell.get_text(" ", strip=True)
                text = sanitize_value(text)
                img_tag = cell.find("img")
                if img_tag:
                    src: str = img_tag.get("src", "")
                    if "base64" in src or not src.startswith("http"):
                        data_src: str = img_tag.get("data-src", "")
                        if data_src.startswith("http"):
                            src = data_src
                    if src.startswith("http"):
                        img_url = src
                row_data[col_name] = text

            # Extract name
            name: str = ""
            for col_name in [h for h in headers if "name" in h or "type" in h]:
                name = row_data.get(col_name, "")
                if name:
                    break
            if not name:
                first_val: str = row_data.get(headers[0], "") if headers else ""
                if first_val and len(first_val) > 1 and first_val.lower() not in ("icon", "image", ""):
                    name = first_val

            if name and len(name) > 1 and name.lower() not in seen_names:
                seen_names.add(name.lower())
                item: Dict[str, Any] = {
                    "name": name,
                    "id": name.lower().replace(" ", "-").replace("'", ""),
                }
                meaningful_keys: Tuple[str, ...] = (
                    "type", "category", "class", "rarity", "source",
                    "location", "weight", "value", "price", "grid",
                    "slots", "description", "caliber", "material",
                )
                for hdr, val in row_data.items():
                    h: str = hdr.lower().strip()
                    if h in meaningful_keys:
                        item[h] = val
                if img_url:
                    item["image"] = img_url
                items.append(item)

    logger.info("  Found %d items in '%s'", len(items), page_title)
    return items


# ─── Validation ───

def validate_items(items: List[Dict[str, Any]], category_name: str) -> Tuple[bool, str]:
    """Validate scraped items before saving.

    Args:
        items: List of scraped item dictionaries.
        category_name: Name of the category (for logging).

    Returns:
        Tuple of (is_valid, reason_string).
    """
    if not items:
        return False, "No items scraped"

    # Check each item has a name
    for item in items:
        if not item.get("name") or len(item["name"]) < 1:
            return False, "Item missing name"

    # Check for excessive duplicates
    names: List[str] = [i.get("name", "").lower() for i in items if i.get("name")]
    unique_count: int = len(set(names))
    if unique_count < len(names) * 0.5 and len(names) > 1:
        return False, f"Too many duplicates ({unique_count}/{len(names)} unique)"

    return True, f"{len(items)} items"


def safe_save(filename: str, items: List[Dict[str, Any]], previous_count: Optional[int] = None) -> bool:
    """Safely save scraped data with rollback protection.

    - Validates data before saving
    - Checks for suspicious drops in item count
    - Creates backup of previous data
    - Preserves old fields that new scrape doesn't have

    Args:
        filename: Output filename (e.g. 'weapons.json').
        items: List of item dictionaries to save.
        previous_count: Item count from previous scrape (for anomaly detection).

    Returns:
        True if save succeeded, False on failure.
    """
    # Validate
    valid: bool
    reason: str
    valid, reason = validate_items(items, filename)
    if not valid:
        logger.error("  ❌ Validation FAILED for %s: %s", filename, reason)
        return False

    # Check for suspicious drops in count
    if previous_count is not None and previous_count > 0:
        drop_ratio: float = len(items) / previous_count if previous_count > 0 else 1.0
        if drop_ratio < (1 - MAX_SAFE_DEVIATION):
            logger.warning(
                "  ⚠️  %s: %d items vs %d previous (>%.0f%% drop). Saving but flagging.",
                filename, len(items), previous_count, MAX_SAFE_DEVIATION * 100,
            )

    # Backup existing file
    existing: Path = OUTPUT_DIR / filename
    if existing.exists():
        try:
            BACKUP_DIR.mkdir(exist_ok=True)
            shutil.copy2(existing, BACKUP_DIR / filename)
        except Exception as exc:
            logger.debug("Backup failed for %s: %s", filename, exc)

    # Load old items for field preservation
    old_items: List[Dict[str, Any]] = []
    if existing.exists():
        try:
            with open(existing, "r", encoding="utf-8") as fh:
                old_items = json.load(fh)
        except Exception:
            pass

    if old_items and isinstance(old_items, list):
        old_map: Dict[str, Dict[str, Any]] = {
            oi.get("name", ""): oi
            for oi in old_items
            if oi.get("name")
        }
        for item in items:
            name: str = item.get("name", "")
            if name in old_map:
                for key, val in old_map[name].items():
                    if key not in item and val is not None:
                        item[key] = val

    # Sort items alphabetically by name for consistent ordering
    items.sort(key=lambda x: (x.get("name") or "").lower())

    # Save
    try:
        path: Path = OUTPUT_DIR / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        logger.info("  ✅ %s: %s", filename, reason)
        return True
    except Exception as exc:
        logger.error("  ❌ Failed to save %s: %s", filename, exc)
        # Try to restore backup
        backup_path: Path = BACKUP_DIR / filename
        if backup_path.exists():
            try:
                shutil.copy2(backup_path, existing)
                logger.info("  ↩️ Restored backup for %s", filename)
            except Exception:
                pass
        return False


# ─── Main scraper orchestrator ───

def get_previous_counts() -> Dict[str, int]:
    """Get item counts from previous scrape for anomaly detection.

    Returns:
        Dictionary mapping filename -> item count.
    """
    counts: Dict[str, int] = {}
    for f in OUTPUT_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data: Any = json.load(fh)
                counts[f.name] = len(data) if isinstance(data, list) else 0
        except Exception:
            pass
    return counts


def get_output_filename(category_title: str) -> str:
    """Determine the output filename for a wiki category.

    Args:
        category_title: Display name of the wiki category.

    Returns:
        Filename with .json extension.
    """
    base: str = CATEGORY_TO_FILENAME.get(
        category_title,
        category_title.lower().replace(" ", "_").replace("-", "_"),
    )
    return base + ".json"


def scrape_single_category_task(cat: Dict[str, Any], previous_counts: Dict[str, int]) -> Optional[Tuple[str, List[Dict[str, Any]]]]:
    """Scrape a single category. Used by ThreadPoolExecutor.

    Args:
        cat: Category dict with 'name', 'title', 'pages' keys.
        previous_counts: Previous item counts for anomaly detection.

    Returns:
        Tuple of (filename, items) if items found, None otherwise.
    """
    name: str = cat["name"]
    title: str = cat["title"]
    filename: str = get_output_filename(title)

    # Skip listing-page-only categories (compare basename without .json)
    base_name: str = filename.replace(".json", "")
    if base_name in LISTING_PAGES:
        return None

    items: List[Dict[str, Any]] = scrape_category(name, title)
    if not items:
        return None
    return (filename, items)


def merge_items(existing: List[Dict[str, Any]], incoming: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge incoming items into existing list, deduplicating by name.

    When two items have the same name, fields from the incoming item
    overwrite existing fields, but existing fields not present in the
    incoming item are preserved.

    Args:
        existing: Current list of items.
        incoming: New items to merge in.

    Returns:
        Merged list of items.
    """
    item_map: Dict[str, Dict[str, Any]] = {
        item.get("name", ""): dict(item)
        for item in existing
        if item.get("name")
    }
    for item in incoming:
        name: str = item.get("name", "")
        if not name:
            continue
        if name in item_map:
            # Merge: incoming overwrites, but preserve existing fields not in incoming
            for key, val in item.items():
                item_map[name][key] = val
        else:
            item_map[name] = dict(item)
    return list(item_map.values())


def run_full_scrape() -> bool:
    """Run the complete bulletproof scrape.

    Scraped items are merged across categories that map to the same
    output filename (e.g. 'Helmet' + 'Headwear' → helmets.json,
    '.222 Remington ammunition' + 'Ammo' → ammo.json).

    Returns:
        True if scrape completed (even with some failures).
    """
    logger.info("=" * 60)
    logger.info("GZW Wiki Scraper v4 — Configurable & Bulletproof")
    logger.info("=" * 60)

    # Get previous counts for change detection
    previous_counts: Dict[str, int] = get_previous_counts()

    # ── Phase 1: Discover categories ──
    logger.info("\n📡 Phase 1: Discovering wiki categories...")
    all_cats: List[Dict[str, Any]] = get_all_categories()
    game_cats: List[Dict[str, Any]] = filter_game_categories(all_cats)
    logger.info("Found %d total categories, %d game-relevant", len(all_cats), len(game_cats))

    # Sort by number of pages (smallest first for quick wins)
    game_cats.sort(key=lambda c: c["pages"])

    # ── Phase 2: Scrape page-based categories in parallel ──
    logger.info("\n🔍 Phase 2: Scraping page-based categories (max %d workers)...", MAX_WORKERS)

    # Collect all scraped items per filename (supports merging)
    merged: Dict[str, List[Dict[str, Any]]] = {}
    auto_discovered: int = 0

    # Use ThreadPoolExecutor for parallel scraping
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        futures = {
            executor.submit(scrape_single_category_task, cat, previous_counts): cat
            for cat in game_cats
        }

        if tqdm:
            future_iter = tqdm(as_completed(futures), total=len(futures), desc="Categories", unit="cat")
        else:
            future_iter = as_completed(futures)

        for future in future_iter:
            cat = futures[future]
            try:
                result: Optional[Tuple[str, List[Dict[str, Any]]]] = future.result()
                if result:
                    filename: str
                    items: List[Dict[str, Any]]
                    filename, items = result
                    if filename not in merged:
                        merged[filename] = []
                    merged[filename] = merge_items(merged[filename], items)
                    # Check if this is a newly discovered category
                    expected_filename: str = get_output_filename(cat["title"])
                    if expected_filename not in previous_counts:
                        auto_discovered += 1
            except Exception as exc:
                logger.error("  Failed to scrape '%s': %s", cat["title"], exc)

    # ── Phase 3: Scrape listing-page-only categories ──
    logger.info("\n📋 Phase 3: Scraping listing-page-only categories...")

    for filename_key, page_title in LISTING_PAGES.items():
        full_filename: str = f"{filename_key}.json" if not filename_key.endswith(".json") else filename_key
        items = scrape_listing_page(full_filename, page_title)
        if items:
            if full_filename not in merged:
                merged[full_filename] = []
            merged[full_filename] = merge_items(merged[full_filename], items)

    # ── Phase 4: Save all merged files ──
    logger.info("\n💾 Phase 4: Saving %d merged files...", len(merged))

    saved_count: int = 0
    for filename, items in merged.items():
        prev_count: Optional[int] = previous_counts.get(filename)
        if safe_save(filename, items, prev_count):
            saved_count += 1

    # ── Summary ──
    logger.info("\n" + "=" * 60)
    logger.info("📊 Scrape Complete!")
    logger.info("  Files saved: %d/%d", saved_count, len(merged))
    if auto_discovered:
        logger.info("  🆕 New categories discovered: %d", auto_discovered)
    logger.info("=" * 60)
    return True


def run_single_category(category_name: str) -> bool:
    """Scrape a single category by exact wiki name (for testing).

    Args:
        category_name: Wiki category name to scrape.

    Returns:
        True if successful.
    """
    items: List[Dict[str, Any]] = scrape_category(category_name, category_name)
    filename: str = get_output_filename(category_name)
    safe_save(filename, items)
    logger.info("Done: %d items in %s", len(items), filename)
    return True


# ─── CLI ───
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GZW Wiki Scraper v4")
    parser.add_argument("--category", help="Scrape a single category by name")
    parser.add_argument("--all", action="store_true", help="Run full scrape (all categories)")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to config.toml")
    args = parser.parse_args()

    if args.config != str(CONFIG_PATH):
        # Reload config from custom path
        CONFIG = load_config(Path(args.config))

    if args.category:
        run_single_category(args.category)
    elif args.all:
        run_full_scrape()
    else:
        run_full_scrape()
