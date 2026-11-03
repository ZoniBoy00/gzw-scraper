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


def fetch_prices() -> Dict[str, float]:
    """Return ``{item id: sell price}`` from GZW Items, or an empty dict when unavailable."""
    headers = {**network.HEADERS, "X-Client": "gzw-scraper", "Accept": "application/json"}
    try:
        response = requests.get(API_URL, headers=headers, timeout=30)
        response.raise_for_status()
        items = response.json().get("items", [])
    except (requests.RequestException, ValueError, AttributeError) as exc:
        logger.warning("Community prices unavailable (%s); continuing without them", exc)
        return {}

    prices: Dict[str, float] = {}
    for item in items if isinstance(items, list) else []:
        price = item.get("sellPrice") if isinstance(item, dict) else None
        if isinstance(item, dict) and isinstance(item.get("id"), str) and isinstance(price, (int, float)) \
                and not isinstance(price, bool) and math.isfinite(price) and price > 0:
            prices[item["id"]] = price
    return prices


def format_price(price: float) -> str:
    """Format like the wiki does, for example ``$5,451``."""
    return f"${int(price):,}" if float(price).is_integer() else f"${price:,}"


def _has_price(record: Dict[str, Any]) -> bool:
    value = record.get("sell_price")
    return isinstance(value, str) and bool(_HAS_DIGIT.search(value))


def apply_prices(datasets: Dict[str, List[Dict[str, Any]]], prices: Optional[Dict[str, float]] = None) -> int:
    """Add missing sell prices in place. Returns how many records were filled."""
    if prices is None:
        prices = fetch_prices() if ENABLED else {}
    if not prices:
        return 0

    filled = 0
    for items in datasets.values():
        for record in items:
            if not isinstance(record, dict):
                continue
            price = prices.get(record.get("id")) if isinstance(record.get("id"), str) else None
            if price is None or _has_price(record):
                continue
            record["sell_price"] = format_price(price)
            record["sell_price_source"] = SOURCE
            filled += 1
    return filled
