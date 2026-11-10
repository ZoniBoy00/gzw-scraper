"""Keep published wiki datasets and derived inventories consistent."""
from __future__ import annotations

import json
import logging
import re
from decimal import Decimal
from pathlib import Path

logger = logging.getLogger("gzw-scraper")

# Reviewed renames from scrape-report-52. Do not infer identity with fuzzy matching.
REVIEWED_RENAMES = {
    "Civilian Trekstars 70L Backpack": "Civilian Trekstars 70L",
    "Scorpion OCP Convoy Pack": "Scorpion OCP Convoy Pack (Coyote)",
    "Lamang Army Forces": "Lamang Armed Forces",
    "Rebels / Bandits (Tier 1 areas)": "Lamang Liberation Army / Bandits (Tier 1 areas)",
    "6B23-1(Digital)": "6B23-1 (Digital)",
    "6B23-1(Flora)": "6B23-1 (Flora)",
    "SK-S Ballistic Vest": "SK-S Vest",
    "Mich TC-2000 (Ranger Green)": "MICH TC-2000 (Green)",
}


def canonical_name(name: str) -> str:
    return REVIEWED_RENAMES.get(name, name).casefold()


def load_published_wiki_data(directory: Path) -> dict[str, list[dict]]:
    datasets = {}
    for path in sorted(directory.glob("*.json")):
        if path.name.startswith(("_", "gamedata_")):
            continue
        try:
            records = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            logger.warning("Could not read published dataset %s: %s", path.name, exc)
            continue
        if isinstance(records, list) and all(isinstance(row, dict) for row in records):
            datasets[path.name] = records
    return datasets


def preserve_record_ids(items: list[dict], previous: list[dict]) -> None:
    """Keep existing IDs for exact names and explicitly reviewed renamed records."""
    index: dict[str, set[str]] = {}
    for record in previous:
        name, record_id = record.get("name"), record.get("id")
        if isinstance(name, str) and isinstance(record_id, str):
            index.setdefault(canonical_name(name), set()).add(record_id)
    incoming_ids = {record.get("id") for record in items}
    for record in items:
        name = record.get("name")
        candidates = index.get(canonical_name(name), set()) if isinstance(name, str) else set()
        if len(candidates) == 1:
            record_id = next(iter(candidates))
            if record_id == record.get("id") or record_id not in incoming_ids:
                incoming_ids.discard(record.get("id"))
                record["id"] = record_id
                incoming_ids.add(record_id)


def fill_shared_sell_prices(datasets: dict[str, list[dict]]) -> None:
    """Fill gaps only from an unambiguous current price for the same wiki record."""
    index: dict[str, dict[str, dict]] = {}
    for filename, records in datasets.items():
        if filename.startswith("gamedata_"):
            continue
        for record in records:
            price, name = record.get("sell_price"), record.get("name")
            if isinstance(name, str) and isinstance(price, str) and price.startswith("$"):
                index.setdefault(canonical_name(name), {})[price] = record
    for filename, records in datasets.items():
        if filename.startswith("gamedata_"):
            continue
        for record in records:
            if record.get("sell_price") or not isinstance(record.get("name"), str):
                continue
            prices = index.get(canonical_name(record["name"]), {})
            if len(prices) == 1:
                price, source = next(iter(prices.items()))
                record["sell_price"] = price
                if source.get("sell_price_source"):
                    record["sell_price_source"] = source["sell_price_source"]


def fill_provision_totals(datasets: dict[str, list[dict]]) -> None:
    """Derive per-cell totals only from current nutrition, charges and grid dimensions."""
    consumables: dict[str, list[dict]] = {}
    for filename in ("food.json", "drink.json"):
        for row in datasets.get(filename, []):
            if isinstance(row.get("name"), str):
                consumables.setdefault(canonical_name(row["name"]), []).append(row)
    for record in datasets.get("provisions.json", []):
        matches = consumables.get(canonical_name(record.get("name", "")), [])
        if len(matches) != 1:
            continue
        source = matches[0]
        charges = str(source.get("charges", ""))
        grid = re.fullmatch(r"(\d+)\s*x\s*(\d+)", str(source.get("grid_size", "")))
        if not re.fullmatch(r"\d+", charges) or not grid:
            continue
        cells = int(grid[1]) * int(grid[2])
        if cells <= 0 or int(charges) <= 0:
            continue
        for nutrient in ("energy", "hydration"):
            field = f"total_{nutrient}_per_grid"
            value = str(record.get(nutrient, ""))
            if field in record or not re.fullmatch(r"[+-]?\d+(?:\.\d+)?", value):
                continue
            total = Decimal(value) * int(charges) / cells
            text = format(total, "f")
            if "." in text:
                text = text.rstrip("0").rstrip(".")
            record[field] = ("+" if total > 0 else "") + text
            record[f"{field}_source"] = "computed-from-current-nutrition"
