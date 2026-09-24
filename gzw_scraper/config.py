"""Configuration loading: TOML config, defaults and deep-merge, plus runtime settings distribution to the other modules."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

try:
    import tomllib
except ImportError:  # pragma: no cover — Python <3.11 fallback
    tomllib = None  # type: ignore[assignment]

import logging

logger = logging.getLogger("gzw-scraper")

# Path to the TOML configuration file (repo root).
CONFIG_PATH: Path = Path(__file__).resolve().parent.parent / "config.toml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "wiki": {
        "api_url": "https://gray-zone-warfare.fandom.com/api.php",
        "user_agent": "GZW-Scraper/4.3.0 (community tool; github.com/ZoniBoy00/gzw-scraper)",
    },
    "scraper": {
        "max_retries": 5,
        "base_delay": 1.0,
        "page_delay": 0.8,
        "max_safe_deviation": 0.7,
        "category_page_limit": 500,
        "max_workers": 3,
        "request_interval": 0.5,
        "field_preservation_min_coverage": 0.5,
        "max_stale_field_runs": 2,
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
    "skip_pages": {
        "titles": [],
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

# ─── Runtime settings (populated by apply_settings) ───

CONFIG: Dict[str, Any] = dict(DEFAULT_CONFIG)
OUTPUT_DIR: Path = CONFIG_PATH.parent / "data"
BACKUP_DIR: Path = CONFIG_PATH.parent / "data_backup"
PAGE_DELAY: float = 0.8


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge two dictionaries. Override values take precedence."""
    result = base.copy()
    for key, val in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(val, dict):
            result[key] = _deep_merge(result[key], val)
        else:
            result[key] = val
    return result


def load_config(config_path: Path = CONFIG_PATH) -> Dict[str, Any]:
    """Load configuration from TOML file, with fallback defaults.

    Args:
        config_path: Path to the TOML configuration file.

    Returns:
        Dictionary with all config values merged from file and defaults.
    """
    if not config_path.exists():
        logger.warning("config.toml not found at %s — using defaults", config_path)
        return _deep_merge(DEFAULT_CONFIG, {})

    if tomllib is None:  # pragma: no cover
        logger.warning("tomllib unavailable — using defaults")
        return _deep_merge(DEFAULT_CONFIG, {})

    try:
        with open(config_path, "rb") as fh:
            user_config = tomllib.load(fh)
    except Exception as exc:
        logger.warning("Failed to load config.toml: %s — using defaults", exc)
        return _deep_merge(DEFAULT_CONFIG, {})

    # Deep-merge user config into defaults
    return _deep_merge(DEFAULT_CONFIG, user_config)


def apply_settings(config_path: Path = CONFIG_PATH) -> None:
    """Load config and distribute its values to the modules that consume them.

    Called at import time for the default config and again whenever the CLI
    is given a custom ``--config`` path.
    """
    global CONFIG, OUTPUT_DIR, BACKUP_DIR, PAGE_DELAY

    # Imported lazily to avoid circular imports (network/pipeline/storage
    # read their settings from this module).
    from gzw_scraper import network, pipeline, storage  # noqa: PLC0415

    cfg = load_config(config_path)
    CONFIG = cfg

    wiki_config = cfg["wiki"]
    scraper_config = cfg["scraper"]
    output_config = cfg["output"]

    root = CONFIG_PATH.parent

    network.API_URL = wiki_config["api_url"]
    network.HEADERS = {"User-Agent": wiki_config["user_agent"]}
    network.MAX_RETRIES = scraper_config["max_retries"]
    network.BASE_DELAY = scraper_config["base_delay"]
    network.REQUEST_INTERVAL = scraper_config.get("request_interval", 0.5)
    network.CATEGORY_PAGE_LIMIT = scraper_config["category_page_limit"]
    network.SKIP_CATEGORIES = set(cfg["skip_categories"]["infrastructure"])

    pipeline.CATEGORY_TO_FILENAME = dict(cfg["category_to_filename"])
    pipeline.LISTING_PAGES = dict(cfg["listing_pages"])
    pipeline.SKIP_PAGES = set(cfg.get("skip_pages", {}).get("titles", []))
    pipeline.PAGE_DELAY = scraper_config["page_delay"]
    pipeline.MAX_WORKERS = scraper_config.get("max_workers", 3)
    pipeline.MAX_SAFE_DEVIATION = scraper_config["max_safe_deviation"]

    storage.OUTPUT_DIR = root / output_config["directory"]
    storage.BACKUP_DIR = root / output_config["backup_directory"]
    storage.MAX_SAFE_DEVIATION = scraper_config["max_safe_deviation"]
    storage.FIELD_PRESERVATION_MIN_COVERAGE = scraper_config.get(
        "field_preservation_min_coverage", 0.5
    )
    storage.MAX_STALE_FIELD_RUNS = scraper_config.get("max_stale_field_runs", 2)

    PAGE_DELAY = pipeline.PAGE_DELAY
    OUTPUT_DIR = storage.OUTPUT_DIR
    BACKUP_DIR = storage.BACKUP_DIR

    storage.OUTPUT_DIR.mkdir(exist_ok=True)
