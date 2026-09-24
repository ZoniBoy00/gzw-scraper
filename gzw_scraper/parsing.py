"""HTML parsing: portable infobox extraction, listing-page tables, value sanitising and ballistics table parsing."""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import bs4

import logging

from gzw_scraper import network

logger = logging.getLogger("gzw-scraper")

# ─── Value sanitising ───

def sanitize_value(value: str) -> str:
    """Clean up common wiki formatting artifacts in scraped values.

    Fixes issues like duplicated units (kgkg, m/s m/s), double percent
    signs, and inconsistent whitespace.
    """
    # Collapse whitespace first
    v: str = re.sub(r"\s+", " ", value).strip()
    # Fix duplicated units: "0.01 kgkg" → "0.01 kg", "830 m/s m/s" → "830 m/s"
    v = re.sub(r"\b(kg|g|lb|oz)\1\b", r"\1", v, flags=re.IGNORECASE)
    v = re.sub(r"\b(m/s)\s*\1\b", r"\1", v, flags=re.IGNORECASE)
    # Fix double percent: "+3% %" → "+3%", "-2% %" → "-2%"
    v = re.sub(r"%(\s*%)+", "%", v)
    # Remove trailing/leading whitespace again after fixes
    return v.strip()


# ─── Infobox parsing ───

def parse_infobox(soup: Optional[bs4.BeautifulSoup]) -> Dict[str, str]:
    """Extract key-value pairs from a portable infobox, safely.

    Args:
        soup: BeautifulSoup object of a wiki page.

    Returns:
        Dictionary of infobox field -> value, plus optional '_image' key.
    """
    data: Dict[str, str] = {}
    if not soup:
        return data
    try:
        infobox = (
            soup.find("aside", class_=lambda c: c and "portable-infobox" in str(c))
            if soup else None
        )
        if not infobox:
            return data
        for data_item in infobox.find_all("div", class_="pi-data"):
            try:
                label_el = data_item.find("h3", class_="pi-data-label")
                value_el = data_item.find("div", class_="pi-data-value")
                if label_el and value_el:
                    label: str = label_el.get_text(" ", strip=True).lower().replace(" ", "_")
                    value: str = value_el.get_text(" ", strip=True)
                    value = sanitize_value(value)
                    data[label] = value
            except Exception:
                continue
        # Get image from infobox (including image collections)
        try:
            # Try direct img first (most common)
            img = infobox.find("img")
            if img and img.get("src"):
                src = img["src"]
                if "data-src" in img.attrs and ("base64" in src or not src.startswith("http")):
                    src = img["data-src"]
                data["_image"] = src
            else:
                # Try pi-image-collection (some pages use this)
                collection = infobox.find("div", class_="pi-image-collection")
                if collection:
                    img = collection.find("img")
                    if img and img.get("src"):
                        src = img["src"]
                        if "data-src" in img.attrs and ("base64" in src or not src.startswith("http")):
                            src = img["data-src"]
                        data["_image"] = src
        except Exception:
            pass
    except Exception as exc:
        logger.debug("parse_infobox error: %s", exc)
    return data


# ─── Listing-page tables ───

def extract_listing_rows(soup: bs4.BeautifulSoup) -> List[Dict[str, str]]:
    """Extract raw name/icon/field rows from every parseable listing table.

    Shared by the listing-page scraper: finds wikitable/article-table/
    sortable/fandom-table tables with a 'name' (or 'icon') column and
    yields one row dict per data row, including resolved image URLs
    (Fandom lazy-load ``data-src`` aware).
    """
    rows: List[Dict[str, str]] = []
    for table in soup.find_all("table", class_=re.compile(r"wikitable|article-table|sortable|fandom-table")):
        table_rows = table.find_all("tr")
        if len(table_rows) < 2:
            continue

        headers: List[str] = []
        for cell in table_rows[0].find_all(["th", "td"]):
            text: str = cell.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            headers.append(text.lower())

        has_name: bool = any("name" in h for h in headers)
        has_icon: bool = any("icon" in h for h in headers)
        if not has_name and not has_icon:
            continue

        for row in table_rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue

            row_data: Dict[str, str] = {}
            img_url: str = ""
            for j, cell in enumerate(cells):
                col_name: str = headers[j] if j < len(headers) else f"col_{j}"
                text = cell.get_text(" ", strip=True)
                text = sanitize_value(text)
                img_tag = cell.find("img")
                if img_tag:
                    src: str = img_tag.get("src", "")
                    if "base64" in src or not src.startswith("http"):
                        data_src: str = img_tag.get("data-src", "")
                        if data_src.startswith("http"):
                            src = data_src
                    if src.startswith("http"):
                        img_url = src
                row_data[col_name] = text

            rows.append({**row_data, "_img_url": img_url})

    return rows


# ─── Ballistics table ───

BALLISTICS_ARMOR_CLASSES: Tuple[str, ...] = (
    "I", "IIA", "IIA+", "IIIA", "IIIA+", "III", "III+", "III++",
)


def _normalise_ammo_name(value: str) -> str:
    """Normalise chart names enough to match scraped ammo records."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip().lower()


def scrape_ballistics_penetration() -> Dict[str, Optional[str]]:
    """Read the wiki Ballistics table and return ammo stopping thresholds.

    The ammo infoboxes do not consistently expose penetration data. The
    Ballistics article is the authoritative table used by the wiki itself and
    contains the 0/1/2 effectiveness values for every caliber. A row whose
    values are all zero is deliberately returned as ``None`` so consumers can
    render it as ineffective at every armor level.
    """
    soup = network.parse_page("Ballistics")
    if not soup:
        logger.warning("Could not load Ballistics page; keeping existing ammo penetration data")
        return {}

    thresholds: Dict[str, Optional[str]] = {}
    for table in soup.find_all("table"):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        header = [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)).lower() for cell in rows[0].find_all(["th", "td"])]
        if not any("effectiveness" in value for value in header):
            continue

        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            values = [re.sub(r"\s+", " ", cell.get_text(" ", strip=True)) for cell in cells]
            if len(values) < len(BALLISTICS_ARMOR_CLASSES):
                continue

            ammo_name = ""
            links = row.find_all("a")
            if len(links) >= 2:
                ammo_name = re.sub(r"\s+", " ", links[1].get_text(" ", strip=True))
            elif links:
                candidate = re.sub(r"\s+", " ", links[0].get_text(" ", strip=True))
                if (
                    len(values) >= 2
                    and values[0] == candidate
                    and re.search(r"\d+(?:\.\d+)?x\d+mm", values[0], re.IGNORECASE)
                    and values[1]
                    and not re.fullmatch(r"[+-]?[\d.]+(?:%| m/s)?", values[1])
                ):
                    caliber_match = re.search(r"\d+(?:\.\d+)?x\d+mm", values[0], re.IGNORECASE)
                    ammo_name = f"{caliber_match.group(0)} {values[1]}" if caliber_match else ""
                else:
                    ammo_name = candidate
            if not ammo_name:
                continue

            effectiveness: List[int] = []
            for value in values[-len(BALLISTICS_ARMOR_CLASSES):]:
                match = re.fullmatch(r"[012]", value)
                if not match:
                    effectiveness = []
                    break
                effectiveness.append(int(value))
            if not effectiveness:
                continue

            effective_indexes = [index for index, level in enumerate(effectiveness) if level > 0]
            if not effective_indexes:
                thresholds[_normalise_ammo_name(ammo_name)] = None
                continue

            last_index = effective_indexes[-1]
            if effectiveness[last_index] == 1:
                threshold = BALLISTICS_ARMOR_CLASSES[last_index]
            elif last_index + 1 < len(BALLISTICS_ARMOR_CLASSES):
                threshold = BALLISTICS_ARMOR_CLASSES[last_index + 1]
            else:
                # The app includes IV/IV+, while the wiki table currently ends
                # at III++. A fully effective III++ row therefore stops at IV.
                threshold = "IV"
            thresholds[_normalise_ammo_name(ammo_name)] = threshold

    # Some legacy rows use the caliber/category link as the name while the
    # actual round name is plain text in the next cell (currently 7.62x25 FMJ).
    legacy_fmj_key = _normalise_ammo_name("7.62x25mm Tokarev")
    if legacy_fmj_key in thresholds:
        thresholds[_normalise_ammo_name("7.62x25mm FMJ")] = thresholds.pop(legacy_fmj_key)

    logger.info("Ballistics table provided penetration data for %d ammo rows", len(thresholds))
    return thresholds


def apply_ballistics_penetration(items: List[Dict[str, object]], thresholds: Dict[str, Optional[str]]) -> int:
    """Apply Ballistics-table thresholds to scraped ammo items."""
    updated = 0
    for item in items:
        key = _normalise_ammo_name(str(item.get("name", "")))
        if key not in thresholds:
            continue
        threshold = thresholds[key]
        if threshold is None:
            # Keep an explicit marker instead of deleting the field. This makes
            # the source decision visible in gzw-data and survives future
            # scraper runs without relying on stale field preservation.
            item["stopped_by_armor_class"] = "NIJ 0"
        else:
            item["stopped_by_armor_class"] = f"NIJ {threshold}"
        updated += 1
    return updated
