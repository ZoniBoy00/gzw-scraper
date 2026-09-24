"""
GZW Wiki Scraper v4.3.0 — Configurable & Bulletproof
==================================================
Automatically discovers supported game-data categories from the wiki and scrapes their pages.

This module is a thin compatibility facade over the ``gzw_scraper``
package, where the implementation now lives:

  - gzw_scraper.config   — config.toml loading, defaults, settings distribution
  - gzw_scraper.network  — MediaWiki API access (throttling, retries, page fetch)
  - gzw_scraper.parsing  — infobox / listing-table / ballistics parsing
  - gzw_scraper.storage  — validation, safe_save, field preservation, merging
  - gzw_scraper.pipeline — scraping orchestration (full & single-category runs)
  - gzw_scraper.cli      — command-line entry point

Public names that existed on this module before package extraction are
re-exported for import compatibility. Functions use settings from their
owning package module, so tests and extensions that monkeypatch internals
should patch ``gzw_scraper.network`` (etc.) rather than this facade.

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

import logging
import sys
from pathlib import Path

import bs4  # noqa: F401  (re-exported for backward compatibility)
from requests import Response  # noqa: F401

from gzw_scraper import cli as _cli
from gzw_scraper import config as _config
from gzw_scraper import network as _network
from gzw_scraper import parsing as _parsing
from gzw_scraper import pipeline as _pipeline
from gzw_scraper import storage as _storage
from gzw_scraper.config import (
    CONFIG,
    CONFIG_PATH,
    DEFAULT_CONFIG,
    _deep_merge,
    apply_settings,
    load_config,
)
from gzw_scraper.network import (
    API_URL,
    CATEGORY_PAGE_LIMIT,
    HEADERS,
    MAX_RETRIES,
    REQUEST_INTERVAL,
    SKIP_CATEGORIES,
    api_call,
    filter_game_categories,
    get_all_categories,
    get_category_members,
    get_page_image,
    parse_page,
    safe_get,
    throttle_request,
)
from gzw_scraper.parsing import (
    BALLISTICS_ARMOR_CLASSES,
    _normalise_ammo_name,
    apply_ballistics_penetration,
    extract_listing_rows,
    parse_infobox,
    sanitize_value,
    scrape_ballistics_penetration,
)
from gzw_scraper.pipeline import (
    CATEGORY_TO_FILENAME,
    FORCE_SAVE,
    LISTING_PAGES,
    MAX_WORKERS,
    PAGE_DELAY,
    SKIP_PAGES,
    get_output_filename,
    get_previous_counts,
    run_full_scrape,
    run_single_category,
    scrape_category,
    scrape_listing_page,
    scrape_single_category_task,
    write_scrape_metadata,
)
from gzw_scraper.storage import (
    BACKUP_DIR,
    FIELD_PRESERVATION_FILENAME,
    FIELD_PRESERVATION_MIN_COVERAGE,
    MAX_SAFE_DEVIATION,
    MAX_STALE_FIELD_RUNS,
    METADATA_FILENAME,
    OUTPUT_DIR,
    merge_items,
    safe_save,
    validate_items,
)

sys.path.insert(0, str(Path(__file__).parent))
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gzw-scraper")

# ─── CLI ───
if __name__ == "__main__":
    _cli.main()
