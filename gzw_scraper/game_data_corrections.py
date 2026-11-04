"""Apply reviewed corrections taken from the game files to wiki-scraped values.

The wiki is no longer updated, so some values (vendor ranks, buy prices, ammo stats) are stale. The corrections file lists, per record and
field, the stale wiki value that was observed (``from``) and the value read from the game files (``to``). A correction is applied only while
the record still holds the observed stale value; if the wiki changed in the meantime, the wiki value wins and the correction is skipped.
Every applied value is marked with ``<field>_source``. A missing or invalid file is non-fatal.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("gzw-scraper")

ENABLED: bool = True
PATH: Path = Path("custom_data") / "game_data_corrections.json"
SOURCE: str = "game-files"


def load_corrections(path: Optional[Path] = None) -> List[Dict[str, Any]]:
    """Return the correction entries, or an empty list when the file is missing or invalid."""
    target = Path(path) if path is not None else PATH
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        logger.warning("Game data corrections unavailable (%s); continuing without them", exc)
        return []
    entries = payload.get("corrections") if isinstance(payload, dict) else None
    if not isinstance(entries, list):
        logger.warning("Game data corrections file has no 'corrections' list; ignoring it")
        return []
    required = ("dataset", "id", "field", "from", "to")
    return [e for e in entries if isinstance(e, dict) and all(isinstance(e.get(k), str) for k in required)]


def apply_corrections(
    datasets: Dict[str, List[Dict[str, Any]]], corrections: Optional[List[Dict[str, Any]]] = None
) -> Tuple[int, int, int]:
    """Apply corrections in place. Returns ``(applied, skipped_changed, not_found)``."""
    if corrections is None:
        corrections = load_corrections() if ENABLED else []
    applied = skipped = missing = 0
    records: Dict[Tuple[str, str], Dict[str, Any]] = {}
    for filename, items in datasets.items():
        for record in items:
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                records.setdefault((filename, record["id"]), record)

    for entry in corrections:
        record = records.get((f"{entry['dataset']}.json", entry["id"]))
        field = entry["field"]
        if record is None or field not in record:
            missing += 1
            continue
        current = record[field]
        if isinstance(current, str) and current.strip() == entry["to"].strip():
            continue  # already correct (earlier run, or the wiki now agrees)
        if not isinstance(current, str) or current.strip() != entry["from"].strip():
            skipped += 1
            logger.info("Skipped game-data correction for %s.%s: wiki value changed to %r", entry["id"], field, current)
            continue
        record[field] = entry["to"]
        record[f"{field}_source"] = SOURCE
        numeric = record.get("numeric_values")
        if isinstance(numeric, dict):
            numeric.pop(field, None)  # recomputed from the corrected text on save
        applied += 1
    return applied, skipped, missing
