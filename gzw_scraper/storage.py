"""Persistence: validation, safe_save with backup/drop-guard/field preservation, and item merging."""

from __future__ import annotations

import json
import logging
import shutil
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger("gzw-scraper")

# ─── Runtime settings (populated by gzw_scraper.config.apply_settings) ───

OUTPUT_DIR: Path = Path("data")
BACKUP_DIR: Path = Path("data_backup")
METADATA_FILENAME = "_metadata.json"
FIELD_PRESERVATION_FILENAME = "_field_preservation.json"
MAX_SAFE_DEVIATION: float = 0.7
FIELD_PRESERVATION_MIN_COVERAGE: float = 0.5
MAX_STALE_FIELD_RUNS: int = 2


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


def safe_save(filename: str, items: List[Dict[str, Any]], previous_count: Optional[int] = None,
              force: bool = False) -> bool:
    """Safely save scraped data with rollback protection.

    - Validates data before saving
    - Checks for suspicious drops in item count and ABORTS the save when a
      category collapsed by more than (1 - max_safe_deviation) — a >70% drop
      is almost always a scrape failure (rate limit, wiki hiccup), not the
      wiki actually losing content. Writing it would destroy good data.
    - Creates backup of previous data
    - Preserves old fields that new scrape doesn't have

    Args:
        filename: Output filename (e.g. 'weapons.json').
        items: List of item dictionaries to save.
        previous_count: Item count from previous scrape (for anomaly detection).
        force: Skip the drop-guard (used when the wiki genuinely shrank).

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

    # Check for suspicious drops in count — abort BEFORE touching the file
    if previous_count is not None and previous_count > 0 and not force:
        drop_ratio: float = len(items) / previous_count if previous_count > 0 else 1.0
        if drop_ratio < (1 - MAX_SAFE_DEVIATION):
            logger.error(
                "  ❌ %s: %d items vs %d previous (>%.0f%% drop). "
                "ABORTING save to prevent data loss. Use --force to override.",
                filename, len(items), previous_count, MAX_SAFE_DEVIATION * 100,
            )
            return False

    # Backup existing file
    existing: Path = OUTPUT_DIR / filename
    if existing.exists():
        try:
            BACKUP_DIR.mkdir(exist_ok=True)
            shutil.copy2(existing, BACKUP_DIR / filename)
        except Exception as exc:
            logger.debug("Backup failed for %s: %s", filename, exc)

    # Load old items for schema-aware field preservation. A parser hiccup can
    # omit fields from otherwise healthy items, but fields must not survive
    # indefinitely when the omission repeats across successful runs.
    old_items: List[Dict[str, Any]] = []
    if existing.exists():
        try:
            with open(existing, "r", encoding="utf-8") as fh:
                old_items = json.load(fh)
        except Exception:
            pass

    preservation_state: Dict[str, Dict[str, Dict[str, int]]] = {}
    state_path = OUTPUT_DIR / FIELD_PRESERVATION_FILENAME
    if state_path.exists():
        try:
            loaded_state = json.loads(state_path.read_text(encoding="utf-8"))
            if isinstance(loaded_state, dict):
                preservation_state = loaded_state
        except Exception:
            logger.warning("Could not read %s; resetting preservation state", state_path)

    updated_state = json.loads(json.dumps(preservation_state))
    file_state = updated_state.setdefault(filename, {})

    if old_items and isinstance(old_items, list):
        old_map: Dict[str, Dict[str, Any]] = {
            oi.get("name", ""): oi
            for oi in old_items
            if isinstance(oi, dict) and oi.get("name")
        }
        old_field_coverage: Dict[str, float] = {}
        for old_item in old_map.values():
            for key in old_item:
                old_field_coverage[key] = old_field_coverage.get(key, 0.0) + 1.0
        if old_map:
            old_field_coverage = {
                key: count / len(old_map) for key, count in old_field_coverage.items()
            }

        for item in items:
            name: str = item.get("name", "")
            if name in old_map:
                for key, val in old_map[name].items():
                    if key in item or val is None or key == "name":
                        item_state = file_state.get(name, {})
                        item_state.pop(key, None)
                        continue
                    if old_field_coverage.get(key, 0.0) < FIELD_PRESERVATION_MIN_COVERAGE:
                        continue
                    item_state = file_state.setdefault(name, {})
                    missing_runs = item_state.get(key, 0) + 1
                    if missing_runs < MAX_STALE_FIELD_RUNS:
                        item[key] = val
                        item_state[key] = missing_runs
                    else:
                        item_state.pop(key, None)
                        logger.warning(
                            "  %s: dropping stale field '%s' for '%s' after %d runs",
                            filename, key, name, missing_runs,
                        )

    # Sort items alphabetically by name for consistent ordering
    items.sort(key=lambda x: (x.get("name") or "").lower())

    # Save
    try:
        path: Path = OUTPUT_DIR / filename
        with open(path, "w", encoding="utf-8") as f:
            json.dump(items, f, indent=2, ensure_ascii=False)
        state_path.write_text(
            json.dumps(updated_state, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
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


# ─── Merging ───

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
