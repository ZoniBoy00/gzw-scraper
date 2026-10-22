"""Pipeline orchestration: category scraping, listing pages, the full scrape run and single-category runs."""

from __future__ import annotations

import json
import logging
import re
import threading
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
LISTING_PAGE_FIELDS: Dict[str, Set[str]] = {}
LISTING_PAGE_ONLY_DATASETS: Set[str] = set()
CATEGORY_INDEX_PAGE_TITLES: Dict[str, str] = {}
SKIP_PAGES: Set[str] = set()
SUPPLEMENTAL_PAGES: Dict[str, List[str]] = {}
CONTENT_SECTIONS: Dict[str, Dict[str, str]] = {}
PAGE_DELAY: float = 0.8
MAX_WORKERS: int = 3
MAX_SAFE_DEVIATION: float = 0.7

# Set to True via --force to bypass the >70% item-drop guard in safe_save.
FORCE_SAVE: bool = False
DATASET_SOURCE_REVISIONS: Dict[str, Dict[str, int]] = {}
SOURCE_REVISION_LOCK = threading.Lock()


def _record_source_page(dataset_filename: str, page_title: str) -> None:
    """Record the MediaWiki revision that contributed to a dataset."""
    revision_id = network.PAGE_REVISIONS.get(page_title)
    if not isinstance(revision_id, int):
        return
    dataset_name = Path(dataset_filename).stem
    with SOURCE_REVISION_LOCK:
        DATASET_SOURCE_REVISIONS.setdefault(dataset_name, {})[page_title] = revision_id


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
            dataset_name = Path(get_output_filename(title)).stem
            item = scrape_page_record(page_title, dataset_name)
            if item is None:
                skipped += 1
                continue

            # Skip the category's own index page (e.g. the "Weapon Parts" page
            # inside the Weapon Parts category) — it is not a real item.
            index_title = CATEGORY_INDEX_PAGE_TITLES.get(title, title)
            if page_title.casefold() == index_title.casefold():
                skipped += 1
                continue

            items.append(item)
            _record_source_page(get_output_filename(title), page_title)
            split_name = title.casefold().replace(" ", "_")
            if split_name in {"main_task", "side_task"}:
                _record_source_page(f"{split_name}.json", page_title)
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


def scrape_page_record(page_title: str, dataset_name: str) -> Optional[Dict[str, Any]]:
    """Scrape one wiki article using its infobox and configured text sections."""
    soup = network.parse_page(page_title)
    if soup is None:
        return None
    info = parsing.parse_infobox(soup)
    item: Dict[str, Any] = {
        "name": page_title,
        "id": page_title.lower().replace(" ", "-").replace("'", "").replace("(", "").replace(")", ""),
    }
    for wiki_key, value in info.items():
        if wiki_key == "_image":
            item["image"] = value
        elif wiki_key not in ("id", "name"):
            item[wiki_key] = value
    item.update(parsing.extract_page_sections(soup, CONTENT_SECTIONS.get(dataset_name, {})))
    if "image" not in item:
        image = info.get("_image") or network.get_page_image(page_title, soup)
        if image:
            item["image"] = image
    return item


def scrape_supplemental_pages() -> Dict[str, List[Dict[str, Any]]]:
    """Scrape standalone article pages into their configured datasets."""
    datasets: Dict[str, List[Dict[str, Any]]] = {}
    for dataset_name, page_titles in SUPPLEMENTAL_PAGES.items():
        filename = f"{dataset_name}.json"
        for page_title in page_titles:
            try:
                item = scrape_page_record(page_title, dataset_name)
                if item is None:
                    logger.warning("Could not parse supplemental page '%s'", page_title)
                    continue
                datasets.setdefault(filename, []).append(item)
                _record_source_page(filename, page_title)
            except Exception as exc:
                logger.warning("Failed supplemental page '%s': %s", page_title, exc)
            time.sleep(PAGE_DELAY)
    return datasets


def scrape_listing_page(
    key: str,
    page_title: str,
    existing_names: Optional[Set[str]] = None,
    *,
    existing_items: Optional[List[Dict[str, Any]]] = None,
    fields: Optional[Set[str]] = None,
) -> List[Dict[str, Any]]:
    """Scrape items from a listing page (wikitable-based).

    Some categories (Loot, Apparel) have items ONLY in tables on their listing
    pages. Existing item records can be supplied so display aliases resolve to
    their canonical wiki page names rather than creating duplicate records.

    Args:
        key: Output key for the dataset (e.g. 'loot_items').
        page_title: Wiki page title containing the listing tables.
        existing_names: Optional legacy set of existing names for alias matching.
        existing_items: Existing records, including canonical IDs for merges.
        fields: Optional allowlist of output fields for this page.

    Returns:
        List of scraped item dictionaries.
    """
    logger.info("Listing page: %s -> %s...", page_title, key)
    soup: Optional[bs4.BeautifulSoup] = network.parse_page(page_title)
    if not soup:
        logger.warning("  Could not parse '%s'", page_title)
        return []

    existing_by_name: Dict[str, Dict[str, Any]] = {}
    for existing_name in existing_names or set():
        existing_by_name[existing_name.casefold()] = {"name": existing_name}
    for existing_item in existing_items or []:
        existing_name = existing_item.get("name")
        if isinstance(existing_name, str) and existing_name:
            existing_by_name[existing_name.casefold()] = existing_item

    items: List[Dict[str, Any]] = []
    seen_names: Set[str] = set()

    for row in parsing.extract_listing_rows(soup):
        row_data: Dict[str, str] = {
            col: val for col, val in row.items() if not col.startswith("_")
        }
        img_url: str = row.get("_img_url", "")
        page_name: str = row.get("_page_title", "").casefold()

        headers: List[str] = list(row_data.keys())
        display_name: str = ""
        for col_name in [h for h in headers if "name" in h or "type" in h]:
            display_name = row_data.get(col_name, "")
            if display_name:
                break
        if not display_name:
            first_val: str = row_data.get(headers[0], "") if headers else ""
            if first_val and len(first_val) > 1 and first_val.lower() not in ("icon", "image", ""):
                display_name = first_val

        existing_item = (
            existing_by_name.get(page_name)
            or existing_by_name.get(display_name.casefold())
        )
        name = existing_item.get("name", display_name) if existing_item else display_name
        if not name or len(name) <= 1 or name.casefold() in seen_names:
            continue
        seen_names.add(name.casefold())

        item_id = existing_item.get("id") if existing_item else None
        item: Dict[str, Any] = {
            "name": name,
            "id": item_id or name.lower().replace(" ", "-").replace("'", ""),
        }
        meaningful_keys: Tuple[str, ...] = (
            "type", "category", "class", "rarity", "source",
            "location", "weight", "value", "price", "grid",
            "slots", "description", "caliber", "material",
        )
        for header, value in row_data.items():
            field = header.lower().strip()
            if field == "nij":
                parts = value.split("/", 1)
                if len(parts) == 2:
                    rating = re.fullmatch(r"\s*([PR]\d+)\s*", parts[0], flags=re.IGNORECASE)
                    legacy_class = parts[1].strip()
                    if rating and (fields is None or "protection_rating" in fields):
                        item["protection_rating"] = rating.group(1).upper()
                    if legacy_class and (fields is None or "nij_class" in fields):
                        item["nij_class"] = legacy_class
                elif value and (fields is None or "nij_class" in fields):
                    if fields is None or existing_item is None or "nij_class" not in existing_item:
                        item["nij_class"] = value
                continue
            output_field = {
                "plates": "armor_locations",
                "grid": "grid_size",
                "source": "sold_by",
            }.get(field, field)
            if field in meaningful_keys or field == "plates":
                if fields is None or output_field in fields:
                    if fields is None or existing_item is None or output_field not in existing_item:
                        item[output_field] = value
        if img_url and (fields is None or "image" in fields):
            if fields is None or existing_item is None or "image" not in existing_item:
                item["image"] = img_url
        items.append(item)

    logger.info("  Found %d items in '%s'", len(items), page_title)
    if items:
        _record_source_page(key, page_title)
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
        except Exception as exc:
            logger.debug("Could not count items in %s: %s", f.name, exc)
    return counts


def write_scrape_metadata(
    updated_datasets: Optional[Set[str]] = None,
    *,
    full_scrape: bool = False,
) -> None:
    """Write dataset freshness, MediaWiki revision provenance, and schemas."""
    timestamp = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    write_metadata(
        storage.OUTPUT_DIR,
        last_scraped_at=timestamp,
        scraper_version=SCRAPER_VERSION,
        parser_revision=PARSER_REVISION,
        updated_datasets=updated_datasets or set(),
        source_revisions=DATASET_SOURCE_REVISIONS,
        full_scrape=full_scrape,
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


def get_filtered_categories(
    categories: List[Dict[str, Any]],
    included_categories: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    """Return non-empty wiki categories omitted by the active game filter."""
    included = {
        str(category.get("title", "")).strip().casefold()
        for category in included_categories
    }
    filtered: List[Dict[str, Any]] = []
    for category in categories:
        title = str(category.get("*", "")).replace("_", " ").strip()
        try:
            page_count = int(category.get("size", 0))
        except (TypeError, ValueError):
            page_count = 0
        if title and page_count > 0 and title.casefold() not in included:
            filtered.append({"title": title, "pages": page_count})
    return sorted(filtered, key=lambda category: category["title"].casefold())


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

    # Skip only datasets configured to be generated exclusively from listing
    # pages; other datasets may need both category pages and listing enrichment.
    base_name: str = filename.replace(".json", "")
    if base_name in LISTING_PAGE_ONLY_DATASETS:
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
    logger.info("GZW Wiki Scraper v%s — Configurable & Bulletproof", SCRAPER_VERSION)
    logger.info("=" * 60)

    DATASET_SOURCE_REVISIONS.clear()
    network.PAGE_REVISIONS.clear()

    # Get previous counts for change detection
    previous_counts: Dict[str, int] = get_previous_counts()

    # ── Phase 1: Discover categories ──
    logger.info("\n📡 Phase 1: Discovering wiki categories...")
    all_cats: List[Dict[str, Any]] = network.get_all_categories()
    game_cats: List[Dict[str, Any]] = network.filter_game_categories(all_cats)
    logger.info("Found %d total categories, %d game-relevant", len(all_cats), len(game_cats))
    filtered_categories = get_filtered_categories(all_cats, game_cats)
    logger.info("Filtered out %d non-empty wiki categories", len(filtered_categories))
    for category in filtered_categories:
        logger.info("  Category not scraped: %s (%d pages)", category["title"], category["pages"])

    # Sort by number of pages (smallest first for quick wins)
    game_cats.sort(key=lambda c: c["pages"])

    # ── Phase 2: Scrape page-based categories in parallel ──
    logger.info("\n🔍 Phase 2: Scraping page-based categories (max %d workers)...", MAX_WORKERS)

    # Collect all scraped items per filename (supports merging)
    merged: Dict[str, List[Dict[str, Any]]] = {}
    # Per-category task files (main_task.json, side_task.json) kept separate for
    # frontend compatibility — gzw-tools MissionFinder fetches them individually.
    task_split: Dict[str, List[Dict[str, Any]]] = {}
    auto_discovered_categories: Set[str] = set()

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
                        auto_discovered_categories.add(cat["title"])
            except Exception as exc:
                logger.error("  Failed to scrape '%s': %s", cat["title"], exc)

    # ── Phase 2b: Add game articles not assigned to a wiki category ──
    logger.info("\n📄 Phase 2b: Scraping configured standalone pages...")
    for filename, items in scrape_supplemental_pages().items():
        merged[filename] = storage.merge_items(merged.get(filename, []), items)

    # ── Phase 3: Scrape listing-page-only categories ──
    logger.info("\n📋 Phase 3: Scraping listing-page-only categories...")

    for filename_key, page_title in LISTING_PAGES.items():
        full_filename: str = f"{filename_key}.json" if not filename_key.endswith(".json") else filename_key
        existing_items = merged.get(full_filename, [])
        if not existing_items:
            existing_path = storage.OUTPUT_DIR / full_filename
            if existing_path.exists():
                try:
                    with open(existing_path, "r", encoding="utf-8") as fh:
                        loaded = json.load(fh)
                    if isinstance(loaded, list):
                        existing_items = loaded
                except (OSError, json.JSONDecodeError):
                    logger.warning("Could not load previous %s for listing-page alias matching", full_filename)
        items = scrape_listing_page(
            full_filename,
            page_title,
            existing_items=existing_items,
            fields=LISTING_PAGE_FIELDS.get(filename_key),
        )
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

    # ── Phase 4: Save all merged and per-category task files ──
    total_files = len(merged) + len(task_split)
    logger.info("\n💾 Phase 4: Saving %d output files...", total_files)

    saved_count: int = 0
    updated_datasets: Set[str] = set()
    for filename, items in merged.items():
        prev_count: Optional[int] = previous_counts.get(filename)
        if storage.safe_save(filename, items, prev_count, force=FORCE_SAVE):
            saved_count += 1
            updated_datasets.add(Path(filename).stem)

    # Save per-category task files (main_task.json, side_task.json)
    for filename, items in task_split.items():
        full_name: str = f"{filename}.json"
        prev_count = previous_counts.get(full_name)
        if storage.safe_save(full_name, items, prev_count, force=FORCE_SAVE):
            saved_count += 1
            updated_datasets.add(Path(full_name).stem)

    # ── Summary ──
    logger.info("\n" + "=" * 60)
    logger.info("📊 Scrape Complete!")
    logger.info("  Files saved: %d/%d", saved_count, total_files)
    if auto_discovered_categories:
        logger.info(
            "  🆕 New categories discovered (%d): %s",
            len(auto_discovered_categories),
            ", ".join(sorted(auto_discovered_categories, key=str.casefold)),
        )
    write_scrape_metadata(updated_datasets, full_scrape=True)
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
    DATASET_SOURCE_REVISIONS.clear()
    network.PAGE_REVISIONS.clear()
    items: Optional[List[Dict[str, Any]]] = scrape_category(category_name, category_name)
    if not items:
        logger.error("No items scraped for '%s' (and previous data preserved)", category_name)
        return False
    filename: str = get_output_filename(category_name)
    saved = storage.safe_save(filename, items, force=FORCE_SAVE)
    if saved:
        write_scrape_metadata({Path(filename).stem})
    logger.info("Done: %d items in %s", len(items), filename)
    return True
