"""Apply item values from the community-maintained GZW Items API (https://gzwitems.com).

GZW Items keeps prices and item values current through player submissions, while the wiki lags behind. Its values win: for every
wiki record that matches a GZW Items entry (by id, then by name) the sell price is set, and weight, grid size and price per grid cell
replace the wiki value where the record carries that field. Entries that match no wiki record are added as new records only for the
categories listed in ``ADD_CATEGORIES`` (category -> dataset); everything else is logged and skipped so renamed items do not become
duplicates. Every value that changes is marked with ``<field>_source``. Failures are non-fatal; the scrape continues without them.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional, Tuple

import requests

from gzw_scraper import network

logger = logging.getLogger("gzw-scraper")

ENABLED: bool = True
API_URL: str = "https://gzwitems.com/api/items"
SOURCE: str = "gzwitems.com"
ADD_CATEGORIES: Dict[str, str] = {"Intel": "intels"}

_NUMBER = re.compile(r"[-+]?\d[\d,]*(?:\.\d+)?")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def _number(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def fetch_items() -> List[Dict[str, Any]]:
    """Return the GZW Items entries that have an id and a name, or an empty list when unavailable."""
    headers = {**network.HEADERS, "X-Client": "gzw-scraper", "Accept": "application/json"}
    try:
        response = requests.get(API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        items = response.json().get("items", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.warning("GZW Items data unavailable (%s); continuing without it", exc)
        return []
    return [
        item for item in (items if isinstance(items, list) else [])
        if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(item.get("name"), str)
    ]


def _key(value: Any) -> str:
    """Compare ids and names ignoring case and punctuation (".45-acp-ap" and "45-acp-ap" are the same item)."""
    return _NON_ALNUM.sub("", str(value).lower()) if isinstance(value, str) else ""


def _index(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """Index entries by normalized id and name; keys shared by different entries are dropped as ambiguous."""
    index: Dict[str, Dict[str, Dict[str, Any]]] = {"id": {}, "name": {}}
    for kind in ("id", "name"):
        seen: Dict[str, Dict[str, Any]] = {}
        ambiguous = set()
        for entry in entries:
            key = _key(entry[kind])
            if not key:
                continue
            if key in seen and seen[key] is not entry:
                ambiguous.add(key)
            seen[key] = entry
        index[kind] = {key: entry for key, entry in seen.items() if key not in ambiguous}
    return index


def format_price(price: float) -> str:
    """Format like the wiki does, for example ``$5,451``; per-round ammo prices keep their decimals (``$0.4``)."""
    return f"${int(price):,}" if float(price).is_integer() else f"${round(price, 2):,}"


def format_weight(weight: float) -> str:
    return f"{weight:g} kg"


def _values(entry: Dict[str, Any]) -> Dict[str, str]:
    """Published field values for one GZW Items entry."""
    values: Dict[str, str] = {}
    price, per_slot, weight = _number(entry.get("sellPrice")), _number(entry.get("sellPricePerSlot")), _number(entry.get("weight"))
    if entry.get("sellable", True) and price and price > 0:
        values["sell_price"] = format_price(price)
        if per_slot and per_slot > 0:
            values["price_per_grid_cell"] = format_price(per_slot)
    if weight is not None and weight >= 0:
        values["weight"] = format_weight(weight)
    grid = entry.get("gridSize")
    if isinstance(grid, str) and re.fullmatch(r"\d+x\d+", grid):
        values["grid_size"] = grid
    return values


def _same(current: Any, new: str) -> bool:
    """True when the record already holds the same number (``0.35kg`` equals ``0.35 kg``, ``$1,250`` equals ``$1,250``)."""
    if not isinstance(current, str):
        return False
    old, value = _NUMBER.findall(current), _NUMBER.findall(new)
    if old and value and len(old) == len(value):
        return all(float(a.replace(",", "")) == float(b.replace(",", "")) for a, b in zip(old, value, strict=True))
    return current.strip() == new


def _set(record: Dict[str, Any], field: str, value: str) -> bool:
    if _same(record.get(field), value):
        return False
    record[field] = value
    record[f"{field}_source"] = SOURCE
    numeric = record.get("numeric_values")
    if isinstance(numeric, dict):
        numeric.pop(field, None)  # recomputed from the new text on save
    return True


def apply_items(
    datasets: Dict[str, List[Dict[str, Any]]], entries: Optional[List[Dict[str, Any]]] = None
) -> Tuple[int, int, int]:
    """Apply GZW Items values in place. Returns ``(values_changed, records_added, entries_skipped)``."""
    if entries is None:
        entries = fetch_items() if ENABLED else []
    if not entries:
        return 0, 0, 0
    index = _index(entries)

    changed = 0
    matched = set()
    for filename, items in datasets.items():
        if filename.startswith("_"):
            continue
        for record in items:
            if not isinstance(record, dict):
                continue
            entry = index["id"].get(_key(record.get("id"))) or index["name"].get(_key(record.get("name")))
            if entry is None:
                continue
            matched.add(id(entry))
            for field, value in _values(entry).items():
                # Sell prices are always published; other fields only replace values the record already carries.
                if field == "sell_price" or field in record:
                    changed += _set(record, field, value)

    added = skipped = 0
    for entry in entries:
        if id(entry) in matched:
            continue
        dataset = ADD_CATEGORIES.get(str(entry.get("category", "")))
        name = re.sub(r"\s+", " ", entry["name"]).strip()
        if not dataset or not name or "�" in name:
            skipped += 1
            logger.info("  GZW Items entry not added: %s (%s/%s)", name, entry.get("category"), entry.get("subcategory"))
            continue
        record: Dict[str, Any] = {"name": name, "id": entry["id"], "type": str(entry.get("category"))}
        record.update(_values(entry))
        record["source"] = SOURCE
        datasets.setdefault(f"{dataset}.json", []).append(record)
        added += 1
    return changed, added, skipped
