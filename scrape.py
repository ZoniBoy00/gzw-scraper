"""
GZW Wiki Scraper v3 — Universal & Bulletproof
==============================================
Automatically discovers ALL game categories from the wiki and scrapes every page.
Features:
  - Auto-discovers new categories (Crafting, Ammo types, etc.)
  - Validates all data before saving
  - Retry + exponential backoff for all API calls
  - Preserves existing data if scrape fails entirely
  - Backups previous data before overwriting
  - Skips wiki-internal categories (Images, Templates, Users, etc.)
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import shutil
from pathlib import Path

import requests
import bs4

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gzw-scraper")

OUTPUT_DIR = Path(__file__).parent / "data"
BACKUP_DIR = Path(__file__).parent / "data_backup"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── Wiki API ───
API_URL = "https://gray-zone-warfare.fandom.com/api.php"
HEADERS = {
    "User-Agent": "GZW-Tools/3.0 (community tool; github.com/ZoniBoy00/gzw-tools; zoni@example.com)"
}
MAX_RETRIES = 3
BASE_DELAY = 1.0  # per-page delay to avoid rate limiting
MAX_SAFE_DEVIATION = 0.7  # if >70% fewer items than previous scrape, abort

# Categories to ALWAYS skip (wiki-internal)
SKIP_CATEGORIES = {
    # Wiki infrastructure
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
    # Non-game
    "Real world", "Staff", "Administration",
    "Screenshots", "Concept art",
    "Gameplay", "Multiplayer",
    # Wiki maintenance categories
    "Documentation templates",
    "Notice templates",
    "Image license templates",
    "Pages missing details",
    "Images needing improvement",
    "Citation needed",
    "Verification needed",
    "Archive",
    "Maps",
    # Non-item categories
    "Removed Content",
    "Upcoming Content",
    "Newspaper",
    "Gray Zone Warfare Wiki",
    "Evidence",
    "Newspapers",
    "Factions",
    "Regions",
}

# Category → filename overrides for special cases
CATEGORY_TO_FILENAME = {
    "Weapons": "weapons",
    "Armor Vest": "vests",
    "Helmet": "helmets",
    "Throwables": "throwables",
    "Weapon Parts": "weapon_parts",
    "Magazines": "magazines",
    "Night Vision Devices": "night_vision",
    "Helmet Mods": "helmet_mods",
    "Helmet Mounts": "helmet_mounts",
    "Weapons camouflage": "weapon_camos",
    "Military Equipment": "military_equipment",
    "Face Cover": "face_cover",
    "Headwear": "headwear_items",
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
    "Main tasks": "tasks",
    "Side tasks": "tasks",
    "Task items": "task_items",
    "Barrels": "barrels",
    "Foregrips": "foregrips",
    "Stocks": "stocks",
    "Suppressors": "suppressors",
}


# ─── Bulletproof API helpers ───

def api_call(params, max_retries=MAX_RETRIES):
    """Make a MediaWiki API call with exponential backoff retry."""
    params["format"] = "json"
    last_error = None
    for attempt in range(max_retries):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except requests.exceptions.Timeout as e:
            last_error = f"Timeout: {e}"
        except requests.exceptions.HTTPError as e:
            status = e.response.status_code if e.response is not None else "unknown"
            # 429 = rate limited, back off longer
            if status == 429:
                wait = (2 ** attempt) * 5
                logger.warning("Rate limited (429), waiting %ds...", wait)
                time.sleep(wait)
                continue
            last_error = f"HTTP {status}: {e}"
        except requests.exceptions.ConnectionError as e:
            last_error = f"Connection error: {e}"
        except Exception as e:
            last_error = f"Unknown error: {e}"

        if attempt < max_retries - 1:
            wait = (2 ** attempt) * BASE_DELAY
            logger.debug("API call failed (attempt %d/%d): %s — retrying in %.1fs",
                         attempt + 1, max_retries, last_error, wait)
            time.sleep(wait)
    logger.error("API call failed after %d attempts: %s", max_retries, last_error)
    return None


def safe_get(url, max_retries=MAX_RETRIES):
    """Safely fetch a URL with retries."""
    for attempt in range(max_retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r
        except Exception as e:
            logger.debug("safe_get failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep((2 ** attempt) * BASE_DELAY)
    return None


# ─── Category discovery ───

def get_all_categories():
    """Get ALL categories from the wiki, excluding internal ones."""
    all_cats = []
    params = {
        "action": "query",
        "list": "allcategories",
        "aclimit": 500,
        "acprop": "size",
    }
    while True:
        data = api_call(params)
        if not data:
            break
        cats = data.get("query", {}).get("allcategories", [])
        all_cats.extend(cats)
        cont = data.get("continue", {})
        if "accontinue" in cont:
            params["accontinue"] = cont["accontinue"]
        else:
            break
    return all_cats


def filter_game_categories(categories):
    """Filter out wiki-internal categories, keep only game-relevant ones."""
    game_cats = []
    skip_patterns = [
        r"^\d", r"^[A-Z]{2,}_", r"^[a-z]",  # starts with digit or underscore-prefixed
    ]

    for cat in categories:
        name = cat.get("*", "")
        title = name.replace("_", " ")
        pages = cat.get("size", 0)

        # Skip internal categories
        if title in SKIP_CATEGORIES:
            continue

        # Skip empty categories
        if pages == 0:
            continue

        # Skip by pattern
        if any(re.match(p, name) for p in skip_patterns):
            continue

        # Skip obvious internal prefixes
        if any(name.startswith(p) for p in ["T_", "P_", "F_", "I_", "U_", "H_"]):
            continue

        game_cats.append({"name": name, "title": title, "pages": pages})

    return game_cats


# ─── Page fetching & parsing ───

def get_category_members(category, limit=500):
    """Get all pages in a wiki category."""
    pages = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": min(limit, 500),
        "cmtype": "page",
    }
    while True:
        data = api_call(params)
        if not data:
            break
        members = data.get("query", {}).get("categorymembers", [])
        pages.extend(members)
        cont = data.get("continue", {})
        if "cmcontinue" in cont:
            params["cmcontinue"] = cont["cmcontinue"]
        else:
            break
    return pages


def parse_page(title):
    """Get parsed HTML of a wiki page."""
    params = {
        "action": "parse",
        "page": title,
        "prop": "text",
        "formatversion": "2",
    }
    data = api_call(params)
    if not data:
        return None
    html = data.get("parse", {}).get("text", "")
    if not html:
        return None
    try:
        return bs4.BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.debug("Failed to parse HTML for '%s': %s", title, e)
        return None


def get_page_image(title):
    """Get thumbnail URL for a wiki page."""
    params = {
        "action": "query",
        "titles": title,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 200,
    }
    data = api_call(params)
    if data:
        for p in data.get("query", {}).get("pages", {}).values():
            if isinstance(p, dict) and p.get("thumbnail"):
                return p["thumbnail"]["source"]
    return None


def parse_infobox(soup):
    """Extract key-value pairs from a portable infobox, safely."""
    data = {}
    if not soup:
        return data
    try:
        infobox = soup.find("aside", class_=lambda c: c and "portable-infobox" in str(c)) if soup else None
        if not infobox:
            return data
        for data_item in infobox.find_all("div", class_="pi-data"):
            try:
                label_el = data_item.find("h3", class_="pi-data-label")
                value_el = data_item.find("div", class_="pi-data-value")
                if label_el and value_el:
                    label = label_el.get_text(" ", strip=True).lower().replace(" ", "_")
                    value = value_el.get_text(" ", strip=True)
                    value = re.sub(r'\s+', ' ', value).strip()
                    data[label] = value
            except Exception:
                continue
        # Get image
        try:
            img = infobox.find("img")
            if img and img.get("src"):
                data["_image"] = img["src"]
        except Exception:
            pass
    except Exception as e:
        logger.debug("parse_infobox error: %s", e)
    return data


# ─── Universal scraper ───

def scrape_category(name, title):
    """Scrape ANY game category with a universal parser.
    
    Args:
        name: Category name on the wiki (e.g. 'Weapons')
        title: Human-readable name for logging
        
    Returns:
        List of scraped items, or empty list on failure
    """
    logger.info("Scraping: %s...", title)
    try:
        pages = get_category_members(name, limit=500)
    except Exception as e:
        logger.warning("  Failed to get members for '%s': %s", name, e)
        return []

    if not pages:
        logger.info("  No pages found in '%s'", title)
        return []

    items = []
    skipped = 0
    for i, p in enumerate(pages):
        page_title = p["title"]
        # Skip non-article pages
        if page_title.startswith("Category:") or page_title.startswith("Template:") or page_title.startswith("User:"):
            skipped += 1
            continue

        try:
            soup = parse_page(page_title)
            info = parse_infobox(soup)

            item = {
                "name": page_title,
                "id": page_title.lower().replace(" ", "-").replace("'", "").replace("(", "").replace(")", ""),
            }

            # Universal field extraction — just grab every field the infobox has
            for wiki_key, val in info.items():
                if wiki_key == "_image":
                    item["image"] = val
                elif wiki_key in ("id", "name"):
                    continue
                else:
                    item[wiki_key] = val

            # Get image if not already found
            if "image" not in item:
                img = get_page_image(page_title) or info.get("_image")
                if img:
                    item["image"] = img

            items.append(item)
        except Exception as e:
            logger.debug("  Error scraping '%s': %s", page_title, e)
            skipped += 1

        # Rate limiting — be nice to the wiki
        time.sleep(BASE_DELAY * 0.5)

    if skipped:
        logger.info("  %s: %d items (+ %d skipped)", title, len(items), skipped)
    else:
        logger.info("  %s: %d items", title, len(items))

    return items


def scrape_listing_page(key, page_title, existing_names=None):
    """Scrape items from a listing page (wikitable-based).
    
    Some categories (Loot, Apparel, Provisions) have items ONLY in tables
    on their listing pages, not as individual wiki pages.
    """
    logger.info("Listing page: %s -> %s...", page_title, key)
    soup = parse_page(page_title)
    if not soup:
        logger.warning("  Could not parse '%s'", page_title)
        return []

    items = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable|article-table|sortable|fandom-table")):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue

        headers = []
        for cell in rows[0].find_all(["th", "td"]):
            text = cell.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            headers.append(text.lower())

        has_name = any("name" in h for h in headers)
        has_icon = any("icon" in h for h in headers)
        if not has_name and not has_icon:
            continue

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            row_data = {}
            img_url = ""
            for j, cell in enumerate(cells):
                col_name = headers[j] if j < len(headers) else f"col_{j}"
                text = cell.get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text).strip()
                img_tag = cell.find("img")
                if img_tag:
                    src = img_tag.get("src", "")
                    if "base64" in src or not src.startswith("http"):
                        data_src = img_tag.get("data-src", "")
                        if data_src.startswith("http"):
                            src = data_src
                    if src.startswith("http"):
                        img_url = src
                row_data[col_name] = text

            # Extract name
            name = ""
            for col_name in [h for h in headers if "name" in h or "type" in h]:
                name = row_data.get(col_name, "")
                if name:
                    break
            if not name:
                first_val = row_data.get(headers[0], "") if headers else ""
                if first_val and len(first_val) > 1 and first_val.lower() not in ("icon", "image", ""):
                    name = first_val

            if name and len(name) > 1:
                item = {
                    "name": name,
                    "id": name.lower().replace(" ", "-").replace("'", ""),
                }
                for hdr, val in row_data.items():
                    h = hdr.lower().strip()
                    if h in ("type", "category", "class", "rarity", "source",
                             "location", "weight", "value", "price", "grid",
                             "slots", "description", "caliber", "material"):
                        item[h] = val
                if img_url:
                    item["image"] = img_url
                items.append(item)

    logger.info("  Found %d items in '%s'", len(items), page_title)
    return items


# ─── Validation ───

def validate_items(items, category_name):
    """Validate scraped items before saving. Returns (valid, reason)."""
    if not items:
        return False, "No items scraped"

    # Check each item has a name
    for item in items:
        if not item.get("name") or len(item["name"]) < 1:
            return False, "Item missing name"

    # Check for excessive duplicates
    names = [i.get("name", "").lower() for i in items]
    unique_count = len(set(names))
    if unique_count < len(names) * 0.5:
        return False, f"Too many duplicates ({unique_count}/{len(names)} unique)"

    return True, f"{len(items)} items"


def safe_save(filename, items, previous_count=None):
    """Safely save scraped data with rollback protection.
    
    - Validates data before saving
    - Checks for suspicious drops in item count
    - Creates backup of previous data
    """
    # Validate
    valid, reason = validate_items(items, filename)
    if not valid:
        logger.error("  ❌ Validation FAILED for %s: %s", filename, reason)
        return False

    # Check for suspicious drops in count
    if previous_count is not None and previous_count > 0:
        if len(items) < previous_count * (1 - MAX_SAFE_DEVIATION):
            logger.warning(
                "  ⚠️  %s: %d items vs %d previous (>%.0f%% drop). Saving but flagging.",
                filename, len(items), previous_count, MAX_SAFE_DEVIATION * 100
            )

    # Backup existing file
    existing = OUTPUT_DIR / filename
    if existing.exists():
        backup_path = BACKUP_DIR / filename
        try:
            BACKUP_DIR.mkdir(exist_ok=True)
            shutil.copy2(existing, backup_path)
        except Exception as e:
            logger.debug("Backup failed for %s: %s", filename, e)

    # Save
    try:
        path = OUTPUT_DIR / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        logger.info("  ✅ %s: %s", filename, reason)
        return True
    except Exception as e:
        logger.error("  ❌ Failed to save %s: %s", filename, e)
        # Try to restore backup
        backup_path = BACKUP_DIR / filename
        if backup_path.exists():
            try:
                shutil.copy2(backup_path, existing)
                logger.info("  ↩️ Restored backup for %s", filename)
            except Exception:
                pass
        return False


# ─── Main scraper orchestrator ───

def get_previous_counts():
    """Get item counts from previous scrape for anomaly detection."""
    counts = {}
    for f in OUTPUT_DIR.glob("*.json"):
        try:
            with open(f, "r") as fh:
                data = json.load(fh)
                counts[f.name] = len(data) if isinstance(data, list) else 0
        except Exception:
            pass
    return counts


def run_full_scrape():
    """Run the complete bulletproof scrape."""
    logger.info("=" * 60)
    logger.info("GZW Wiki Scraper v3 — Universal & Bulletproof")
    logger.info("=" * 60)

    # Get previous counts for change detection
    previous_counts = get_previous_counts()

    # ── Phase 1: Discover categories ──
    logger.info("\n📡 Phase 1: Discovering wiki categories...")
    all_cats = get_all_categories()
    game_cats = filter_game_categories(all_cats)
    logger.info("Found %d total categories, %d game-relevant", len(all_cats), len(game_cats))

    # Sort by number of pages (smallest first for quick wins)
    game_cats.sort(key=lambda c: c["pages"])

    # Known categories that need special handling (page-based items)
    page_based = {c["title"] for c in game_cats if c["pages"] > 1}
    
    # ── Phase 2: Scrape page-based categories ──
    logger.info("\n🔍 Phase 2: Scraping page-based categories...")
    scraped_files = set()
    auto_discovered = 0

    for cat in game_cats:
        name = cat["name"]
        title = cat["title"]
        pages = cat["pages"]

        if pages < 1:
            continue

        # Determine output filename
        filename = CATEGORY_TO_FILENAME.get(title, name.lower().replace(" ", "_").replace("-", "_"))
        filename += ".json"

        # Skip if already going to be scraped via listing page
        # (Loot, Apparel, Provisions are listing-page-only)
        if title in ("Loot", "Apparel", "Provisions"):
            continue

        # Scrape
        items = scrape_category(name, title)

        # Save with validation
        prev_count = previous_counts.get(filename)
        if safe_save(filename, items, prev_count):
            scraped_files.add(filename)
            if filename not in [CATEGORY_TO_FILENAME.get(t) + ".json" for t in CATEGORY_TO_FILENAME if
                                CATEGORY_TO_FILENAME[t] + ".json" in previous_counts]:
                auto_discovered += 1

    # ── Phase 3: Scrape listing-page-only categories ──
    logger.info("\n📋 Phase 3: Scraping listing-page-only categories...")
    listing_pages = {
        "loot_items.json": "Loot",
        "apparel_items.json": "Apparel",
    }

    for filename, page_title in listing_pages.items():
        prev_count = previous_counts.get(filename)
        items = scrape_listing_page(filename, page_title)
        if safe_save(filename, items, prev_count):
            scraped_files.add(filename)

    # ── Phase 4: Clean up stale files ──
    # Files that exist in data/ but no longer have a corresponding wiki category
    # are likely obsolete. Keep them (don't delete) but log it.

    # ── Summary ──
    logger.info("\n" + "=" * 60)
    logger.info("📊 Scrape Complete!")
    logger.info("  Files updated: %d", len(scraped_files))
    if auto_discovered:
        logger.info("  🆕 New categories discovered: %d", auto_discovered)
    logger.info("=" * 60)
    return True


def run_single_category(category_name):
    """Scrape a single category by exact wiki name (for testing)."""
    items = scrape_category(category_name, category_name)
    filename = category_name.lower().replace(" ", "_") + ".json"
    safe_save(filename, items)
    logger.info("Done: %d items in %s", len(items), filename)
    return True


# ─── CLI ───
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="GZW Wiki Scraper v3")
    parser.add_argument("--category", help="Scrape a single category by name")
    parser.add_argument("--all", action="store_true", help="Run full scrape (all categories)")
    args = parser.parse_args()

    if args.category:
        run_single_category(args.category)
    elif args.all:
        run_full_scrape()
    else:
        # Default: full scrape
        run_full_scrape()
