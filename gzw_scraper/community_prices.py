"""Fill missing sell prices from the community-maintained GZW Items API (https://gzwitems.com).

GZW Items keeps prices current through player submissions, while the wiki is no longer updated. With the
maintainers' permission, their public ``/api/items`` data is used only to fill gaps: a price is added to a record
that has no usable ``sell_price``, never replacing one that is already there, and every added value is marked with
``sell_price_source``. Failures are non-fatal; the scrape simply continues without community prices.
"""

from __future__ import annotations

import logging
import math
import re
from typing import Any, Dict, List, Optional

import requests

from gzw_scraper import network

logger = logging.getLogger("gzw-scraper")

ENABLED: bool = True
API_URL: str = "https://gzwitems.com/api/items"
SOURCE: str = "gzwitems.com"

_HAS_DIGIT = re.compile(r"\d")
_NON_ALNUM = re.compile(r"[^a-z0-9]")


def fetch_prices() -> List[Dict[str, Any]]:
    """Return ``[{"id", "name", "price"}]`` from GZW Items, or an empty list when unavailable."""
    headers = {**network.HEADERS, "X-Client": "gzw-scraper", "Accept": "application/json"}
    try:
        response = requests.get(API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        items = response.json().get("items", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.warning("Community prices unavailable (%s); continuing without them", exc)
        return []

    entries: List[Dict[str, Any]] = []
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue
        price = item.get("sellPrice")
        if isinstance(price, (int, float)) and not isinstance(price, bool) and math.isfinite(price) and price > 0:
            entries.append({"id": item["id"], "name": item.get("name") if isinstance(item.get("name"), str) else "", "price": price})
    return entries


def _key(value: Any) -> str:
    """Compare ids and names ignoring case and punctuation (".45-acp-ap" and "45-acp-ap" are the same item)."""
    return _NON_ALNUM.sub("", str(value).lower()) if isinstance(value, str) else ""


def _index(entries: List[Dict[str, Any]]) -> Dict[str, Dict[str, float]]:
    """Index prices by normalized id and name; keys shared by different prices are dropped as ambiguous."""
    index: Dict[str, Dict[str, float]] = {"id": {}, "name": {}}
    for kind in ("id", "name"):
        seen: Dict[str, float] = {}
        ambiguous = set()
        for entry in entries:
            key = _key(entry[kind])
            if not key:
                continue
            if key in seen and seen[key] != entry["price"]:
                ambiguous.add(key)
            seen[key] = entry["price"]
        index[kind] = {key: price for key, price in seen.items() if key not in ambiguous}
    return index


def format_price(price: float) -> str:
    """Format like the wiki does, for example ``$5,451``."""
    return f"${int(price):,}" if float(price).is_integer() else f"${price:,}"


def _has_price(record: Dict[str, Any]) -> bool:
    value = record.get("sell_price")
    return isinstance(value, str) and bool(_HAS_DIGIT.search(value))


def apply_prices(datasets: Dict[str, List[Dict[str, Any]]], entries: Optional[List[Dict[str, Any]]] = None) -> int:
    """Add missing sell prices in place, matching by id first and then by name. Returns how many were filled."""
    if entries is None:
        entries = fetch_prices() if ENABLED else []
    if not entries:
        return 0
    index = _index(entries)

    filled = 0
    for items in datasets.values():
        for record in items:
            if not isinstance(record, dict) or _has_price(record):
                continue
            price = index["id"].get(_key(record.get("id")))
            if price is None:
                price = index["name"].get(_key(record.get("name")))
            if price is None:
                continue
            record["sell_price"] = format_price(price)
            record["sell_price_source"] = SOURCE
            filled += 1
    return filled
