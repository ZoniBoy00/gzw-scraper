"""Publish item datasets read from the game files next to the wiki datasets.

Files ``custom_data/game_items/gamedata_<category>.json`` hold complete, game-id keyed item lists (weights, grids, prices, vendors, stats).
They are added to the scrape output as separate datasets and never replace a wiki dataset. A missing folder or an invalid file is
non-fatal and simply skipped.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("gzw-scraper")

ENABLED: bool = True
DIR: Path = Path("custom_data") / "game_items"
PREFIX: str = "gamedata_"


def load_datasets(directory: Optional[Path] = None) -> Dict[str, List[Dict[str, Any]]]:
    """Return ``{filename: items}`` for every valid ``gamedata_*.json`` file in the folder."""
    target = Path(directory) if directory is not None else DIR
    datasets: Dict[str, List[Dict[str, Any]]] = {}
    for path in sorted(target.glob(f"{PREFIX}*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Game dataset %s unreadable (%s); skipping it", path.name, exc)
            continue
        items = [r for r in payload if isinstance(r, dict) and isinstance(r.get("id"), str) and isinstance(r.get("name"), str)] if isinstance(payload, list) else []
        if not items:
            logger.warning("Game dataset %s has no valid items; skipping it", path.name)
            continue
        datasets[path.name] = items
    return datasets


def apply_datasets(merged: Dict[str, List[Dict[str, Any]]], datasets: Optional[Dict[str, List[Dict[str, Any]]]] = None) -> int:
    """Add the game datasets to ``merged`` in place without touching existing datasets. Returns how many were added."""
    if datasets is None:
        datasets = load_datasets() if ENABLED else {}
    added = 0
    for filename, items in datasets.items():
        if filename in merged:
            logger.warning("Dataset %s already exists; game dataset not added", filename)
            continue
        merged[filename] = [dict(item) for item in items]
        added += 1
    return added
