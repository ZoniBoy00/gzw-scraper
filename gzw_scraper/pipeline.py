"""Pipeline orchestration: category scraping, listing pages, the full scrape run and single-category runs."""

from __future__ import annotations

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import bs4

try:
    from tqdm import tqdm
except ImportError:  # pragma: no cover
    tqdm = None  # type: ignore[assignment]

from gzw_scraper import network, parsing, storage
from scripts.generate_metadata import PARSER_REVISION, SCRAPER_VERSION, write_metadata

logger = logging.getLogger("gzw-scraper")

# ─── Runtime settings (populated by gzw_scraper.config.apply_settings) ───

CATEGORY_TO_FILENAME: Dict[str, str] = {}
LISTING_PAGES: Dict[str, str] = {}
SKIP_PAGES: Set[str] = set()
PAGE_DELAY: float = 0.8
MAX_WORKERS: int = 3
MAX_SAFE_DEVIATION: float = 0.7

# Set to True via --force to bypass the >70% item-drop guard in safe_save.
FORCE_SAVE: bool = False


# ─── Universal scraper ───

def scrape_category(name: str, title: str) -> Optional[List[Dict[str, Any]]]:
    """Scrape ANY game category with a universal parser.

    Args:
        name: Category name on the wiki (e.g. 'Weapons').
        title: Human-readable name for logging.

    Returns:
        List of scraped item dictionaries. Returns None if the category member
        fetch failed entirely (API down / rate limited) — callers must NOT
        write an empty dataset in that case, otherwise existing data is lost.
    """
    logger.info("Scraping: %s...", title)
    try:
        pages: Optional[List[Dict[str, Any]]] = network.get_category_members(name, limit=network.CATEGORY_PAGE_LIMIT)
    except Exception as exc:
        logger.warning("  Failed to get members for '%s': %s", name, exc)
        return None

    if pages is None:
        logger.error("  ❌ API failed for '%s' — category NOT scraped, preserving previous data", title)
        return None

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

        # Skip explicitly-listed pages (redirects, index pages, lore articles
        # that sit in a game category but are not items/missions)
        if page_title in SKIP_PAGES:
            skipped += 1
            continue

        try:
            soup: Optional[bs4.BeautifulSoup] = network.parse_page(page_title)
            info: Dict[str, str] = parsing.parse_infobox(soup)

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
                img: Optional[str] = info.get("_image") or network.get_page_image(page_title, soup)
                if img:
                    item["image"] = img

            # Skip the category's own index page (e.g. the "Weapon Parts" page
            # inside the Weapon Parts category) — it is not a real item.
            if page_title == title:
                skipped += 1
                continue

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
    soup: Optional[bs4.BeautifulSoup] = network.parse_page(page_title)
    if not soup:
        logger.warning("  Could not parse '%s'", page_title)
        return []

    items: List[Dict[str, Any]] = []
    seen_names: Set[str] = set(existing_names) if existing_names else set()

    for row in parsing.extract_listing_rows(soup):
        row_data: Dict[str, str] = {
            col: val for col, val in row.items() if not col.startswith("_")
        }
        img_url: str = row.get("_img_url", "")

        headers: List[str] = list(row_data.keys())

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


# ─── Orchestrator helpers ───

def get_previous_counts() -> Dict[str, int]:
    """Get item counts from previous scrape for anomaly detection.

    Returns:
        Dictionary mapping filename -> item count.
    """
    counts: Dict[str, int] = {}
    for f in storage.OUTPUT_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fh:
                data: Any = json.load(fh)
                counts[f.name] = len(data) if isinstance(data, list) else 0
        except Exception:
            pass
    return counts


def write_scrape_metadata() -> None:
    """Write scrape timestamp and deterministic dataset schema metadata."""
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    write_metadata(
        storage.OUTPUT_DIR,
        last_scraped_at=timestamp,
        scraper_version=SCRAPER_VERSION,
        parser_revision=PARSER_REVISION,
    )


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

    items: Optional[List[Dict[str, Any]]] = scrape_category(name, title)
    if not items:
        # None = API failure (preserve previous data), [] = genuinely empty
        return None
    return (filename, items)


def run_full_scrape() -> bool:
    """Run the complete bulletproof scrape.

    Scraped items are merged across categories that map to the same
    output filename (e.g. 'Helmet' + 'Headwear' → helmets.json,
    '.222 Remington ammunition' + 'Ammo' → ammo.json).

    Returns:
        True if scrape completed (even with some failures).
    """
    logger.info("=" * 60)
    logger.info("GZW Wiki Scraper v4.3.0 — Configurable & Bulletproof")
    logger.info("=" * 60)

    # Get previous counts for change detection
    previous_counts: Dict[str, int] = get_previous_counts()

    # ── Phase 1: Discover categories ──
    logger.info("\n📡 Phase 1: Discovering wiki categories...")
    all_cats: List[Dict[str, Any]] = network.get_all_categories()
    game_cats: List[Dict[str, Any]] = network.filter_game_categories(all_cats)
    logger.info("Found %d total categories, %d game-relevant", len(all_cats), len(game_cats))

    # Sort by number of pages (smallest first for quick wins)
    game_cats.sort(key=lambda c: c["pages"])

    # ── Phase 2: Scrape page-based categories in parallel ──
    logger.info("\n🔍 Phase 2: Scraping page-based categories (max %d workers)...", MAX_WORKERS)

    # Collect all scraped items per filename (supports merging)
    merged: Dict[str, List[Dict[str, Any]]] = {}
    # Per-category task files (main_task.json, side_task.json) kept separate for
    # frontend compatibility — gzw-tools MissionFinder fetches them individually.
    task_split: Dict[str, List[Dict[str, Any]]] = {}
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
                    merged[filename] = storage.merge_items(merged[filename], items)
                    # Keep per-category task files (main_task, side_task) so the
                    # frontend can still categorize missions by type.
                    if cat["title"] in ("Main task", "Side task"):
                        split_name: str = cat["title"].lower().replace(" ", "_")
                        if split_name not in task_split:
                            task_split[split_name] = []
                        task_split[split_name] = storage.merge_items(task_split[split_name], items)
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
            merged[full_filename] = storage.merge_items(merged[full_filename], items)

    # The ammo infoboxes often omit penetration data. Rebuild the complete
    # threshold map from the wiki Ballistics table before saving ammo.json.
    if "ammo.json" in merged:
        ballistics = parsing.scrape_ballistics_penetration()
        if ballistics:
            updated = parsing.apply_ballistics_penetration(merged["ammo.json"], ballistics)
            logger.info("Applied Ballistics penetration data to %d ammo rows", updated)

    # ── Phase 4: Save all merged files ──
    logger.info("\n💾 Phase 4: Saving %d merged files...", len(merged))

    saved_count: int = 0
    for filename, items in merged.items():
        prev_count: Optional[int] = previous_counts.get(filename)
        if storage.safe_save(filename, items, prev_count, force=FORCE_SAVE):
            saved_count += 1

    # Save per-category task files (main_task.json, side_task.json)
    for filename, items in task_split.items():
        full_name: str = f"{filename}.json"
        prev_count = previous_counts.get(full_name)
        if storage.safe_save(full_name, items, prev_count, force=FORCE_SAVE):
            saved_count += 1

    # ── Summary ──
    logger.info("\n" + "=" * 60)
    logger.info("📊 Scrape Complete!")
    logger.info("  Files saved: %d/%d", saved_count, len(merged))
    if auto_discovered:
        logger.info("  🆕 New categories discovered: %d", auto_discovered)
    write_scrape_metadata()
    logger.info("  Metadata updated: %s", storage.METADATA_FILENAME)
    logger.info("=" * 60)
    return True


def run_single_category(category_name: str) -> bool:
    """Scrape a single category by exact wiki name (for testing).

    Args:
        category_name: Wiki category name to scrape.

    Returns:
        True if successful.
    """
    items: Optional[List[Dict[str, Any]]] = scrape_category(category_name, category_name)
    if not items:
        logger.error("No items scraped for '%s' (and previous data preserved)", category_name)
        return False
    filename: str = get_output_filename(category_name)
    storage.safe_save(filename, items, force=FORCE_SAVE)
    logger.info("Done: %d items in %s", len(items), filename)
    return True
