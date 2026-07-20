"""
GZW Wiki Scraper v2 — Comprehensive data extractor
Scrapes ALL wiki content from the Gray Zone Warfare Fandom wiki:
- Weapons, Armor, Helmets, Attachments, Ammo, Throwables, Tasks, Keys
- All Gear & Items categories (glasses, face cover, headsets, headwear, belts, apparel)
- Medical, Provisions, Food, Drinks, Containers, Loot, Tools
- Weapon parts (barrels, stocks, grips, magazines, suppressors, etc.)
- Factions, Info pages, images
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from pathlib import Path

import requests
import bs4

sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("gzw-scraper")

OUTPUT_DIR = Path(__file__).parent / "data"
OUTPUT_DIR.mkdir(exist_ok=True)

# ─── Wiki API setup ───

API_URL = "https://gray-zone-warfare.fandom.com/api.php"
HEADERS = {
    "User-Agent": "GZW-Tools/1.0 (community tool; github.com/ZoniBoy00/gzw-tools)",
}

def api_call(params, max_retries=3):
    """Make a MediaWiki API call with retries."""
    params["format"] = "json"
    for attempt in range(max_retries):
        try:
            r = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.warning("API call failed (attempt %d/%d): %s", attempt + 1, max_retries, e)
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
    return None

def get_category_members(category, limit=500):
    """Get all pages in a category via categorymembers API."""
    pages = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": min(limit, 500),
        "cmtype": "page",
    }
    while True:
        data = api_call(params)
        if not data:
            break
        members = data.get("query", {}).get("categorymembers", [])
        pages.extend(members)
        cont = data.get("continue", {})
        if "cmcontinue" in cont:
            params["cmcontinue"] = cont["cmcontinue"]
        else:
            break
    return pages

def get_category_subcategories(category, limit=500):
    """Get all subcategories in a category."""
    pages = []
    params = {
        "action": "query",
        "list": "categorymembers",
        "cmtitle": f"Category:{category}",
        "cmlimit": min(limit, 500),
        "cmtype": "subcat",
    }
    while True:
        data = api_call(params)
        if not data:
            break
        members = data.get("query", {}).get("categorymembers", [])
        pages.extend(members)
        cont = data.get("continue", {})
        if "cmcontinue" in cont:
            params["cmcontinue"] = cont["cmcontinue"]
        else:
            break
    return pages

def get_page_image(title):
    """Get thumbnail URL for a wiki page."""
    params = {
        "action": "query",
        "titles": title,
        "prop": "pageimages",
        "piprop": "thumbnail",
        "pithumbsize": 200,
        "format": "json",
    }
    data = api_call(params)
    if data:
        for p in data.get("query", {}).get("pages", {}).values():
            if isinstance(p, dict) and p.get("thumbnail"):
                return p["thumbnail"]["source"]
    return None

def parse_infobox(soup):
    """Extract key-value pairs from a portable infobox."""
    import bs4
    data = {}
    if not soup:
        return data
    infobox = soup.find("aside", class_=lambda c: c and "portable-infobox" in str(c)) if soup else None
    if not infobox:
        return data
    
    for data_item in infobox.find_all("div", class_="pi-data"):
        label_el = data_item.find("h3", class_="pi-data-label")
        value_el = data_item.find("div", class_="pi-data-value")
        if label_el and value_el:
            label = label_el.get_text(" ", strip=True).lower().replace(" ", "_")
            value = value_el.get_text(" ", strip=True)
            value = re.sub(r'\s+', ' ', value).strip()
            data[label] = value
    
    # Get image
    img = infobox.find("img")
    if img and img.get("src"):
        data["_image"] = img["src"]
    
    return data

def parse_page(title):
    """Get parsed HTML of a wiki page."""
    params = {
        "action": "parse",
        "page": title,
        "prop": "text",
        "formatversion": "2",
    }
    data = api_call(params)
    if not data:
        return None
    html = data.get("parse", {}).get("text", "")
    if not html:
        return None
    import bs4
    return bs4.BeautifulSoup(html, "lxml")

def extract_objectives(soup):
    """Extract objectives section from page text."""
    if not soup:
        return []
    text = soup.get_text(" ", strip=True)
    m = re.search(r'==\s*Objectives\s*==\s*(.+?)(?=\n==|\Z)', text, re.DOTALL)
    if m:
        objectives = []
        for line in m.group(1).split("\n"):
            line = line.strip().strip("•-*")
            if line and len(line) > 5:
                objectives.append(line)
        return objectives[:8]
    return []

def extract_listing_table(soup):
    """Extract all item names from a listing page table (wikitable/fandom-table).
    Returns a list of dicts with 'name', 'image_url', and any other columns found."""
    items = []
    if not soup:
        return items
    
    for table in soup.find_all("table", class_=re.compile(r"wikitable|article-table|sortable|fandom-table")):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        
        # Get headers
        headers = []
        for cell in rows[0].find_all(["th", "td"]):
            text = cell.get_text(" ", strip=True)
            text = re.sub(r"\s+", " ", text).strip()
            headers.append(text.lower())
        
        # Check if this looks like an item listing table (has a name column)
        has_name = any("name" in h for h in headers)
        has_icon = any("icon" in h for h in headers)
        if not has_name and not has_icon:
            continue
        
        for row in rows[1:]:
            cells = row.find_all(["td", "th"])
            if not cells:
                continue
            
            row_data = {}
            for j, cell in enumerate(cells):
                col_name = headers[j] if j < len(headers) else f"col_{j}"
                
                # Extract text
                text = cell.get_text(" ", strip=True)
                text = re.sub(r"\s+", " ", text).strip()
                
                # Extract image from the cell
                img_tag = cell.find("img")
                img_url = img_tag.get("src", "") if img_tag else ""
                
                row_data[col_name] = text
                if img_url and not row_data.get("_image"):
                    row_data["_image"] = img_url
            
            # Extract name from the most likely column
            name = ""
            for col_name in [h for h in headers if "name" in h or "type" in h]:
                name = row_data.get(col_name, "")
                if name:
                    break
            if not name:
                first_val = row_data.get(headers[0], "") if headers else ""
                if first_val and not first_val.lower() in ["icon", "image", ""]:
                    name = first_val
            
            if name and len(name) > 1:
                items.append({
                    "name": name,
                    "image": row_data.get("_image", ""),
                    "data": row_data,
                })
    
    return items

# ─── Scrapers ───

import requests as req_module
import bs4

def scrape_weapons():
    """Scrape all weapon pages."""
    logger.info("=" * 60)
    logger.info("Scraping Weapons...")
    pages = get_category_members("Weapons", limit=500)
    logger.info("Found %d weapon pages", len(pages))
    
    weapons = []
    for i, p in enumerate(pages):
        title = p["title"]
        if title.startswith("Category:") or title.startswith("Template:") or title.startswith("User:"):
            continue
        logger.debug("  [%d/%d] %s", i + 1, len(pages), title)
        
        soup = parse_page(title)
        info = parse_infobox(soup)
        
        weapon = {"name": title, "id": title.lower().replace(" ", "-").replace("'", "")}
        
        field_map = {
            "type": "type",
            "caliber": "caliber",
            "calibre": "caliber",
            "magazine_capacity": "magSize",
            "capacity": "magSize",
            "fire_rate": "fireRate",
            "weight": "weight",
            "length": "length",
        }
        for wiki_key, our_key in field_map.items():
            if wiki_key in info:
                val = info[wiki_key]
                if our_key in ("magSize", "fireRate"):
                    nums = re.findall(r'\d+', val)
                    if nums:
                        weapon[our_key] = nums[0]
                elif our_key == "weight":
                    nums = re.findall(r'[\d.]+', val)
                    if nums:
                        weapon[our_key] = float(nums[0])
                else:
                    weapon[our_key] = val
        
        img = get_page_image(title)
        if img:
            weapon["image"] = img
        
        if soup:
            text = soup.get_text(" ", strip=True)
            vendor_patterns = [
                (r'vendor[:\\s]+(\w+)', 1),
                (r'obtained\s+from\s+(\w+)', 1),
                (r'available\s+at\s+rep\s+level\s+(\d+)', 1),
            ]
            for pattern, group in vendor_patterns:
                m = re.search(pattern, text, re.IGNORECASE)
                if m:
                    key = "vendor" if "vendor" in pattern else "repLevel"
                    weapon[key] = m.group(group).strip()
        
        weapons.append(weapon)
        time.sleep(0.3)
    
    if weapons:
        path = OUTPUT_DIR / "weapons.json"
        with open(path, "w") as f:
            json.dump(weapons, f, indent=2)
        logger.info("✅ Weapons: %d saved", len(weapons))
    
    return weapons


def scrape_armor():
    """Scrape armor vests, helmets, plate carriers, headwear, and tactical rigs."""
    page_categories = {
        "vests": "Armor Vest",
        "helmets": "Helmet",
        "rigs": "Tactical Rigs",
        "backpacks": "Backpacks",
    }
    
    listing_pages = {
        "vests": "Armor Vests",
        "plate_carriers": "Plate Carriers",
        "helmets": "Headwear",
    }
    
    results = {}
    all_individual_items = {}
    
    for key, cat in page_categories.items():
        logger.info("=" * 60)
        logger.info("Scraping %s (category: %s)...", key, cat)
        pages = get_category_members(cat, limit=200)
        logger.info("Found %d pages", len(pages))
        
        items = []
        for i, p in enumerate(pages):
            title = p["title"]
            if title.startswith("Category:") or title.startswith("Template:"):
                continue
            logger.debug("  [%d/%d] %s", i + 1, len(pages), title)
            
            soup = parse_page(title)
            info = parse_infobox(soup)
            
            item = {"name": title, "id": title.lower().replace(" ", "-").replace("'", "")}
            
            field_map = {
                "type": "type",
                "armor_rating": "nij",
                "nij_rating": "nij",
                "material": "material",
                "weight": "weight",
                "capacity": "capacity",
                "grid_size": "grid",
                "armor_coverage": "plates",
                "coverage": "plates",
                "source": "source",
                "vendor": "source",
            }
            for wiki_key, our_key in field_map.items():
                if wiki_key in info:
                    val = info[wiki_key]
                    if our_key == "weight":
                        nums = re.findall(r'[\d.]+', val)
                        if nums:
                            item[our_key] = float(nums[0])
                    else:
                        item[our_key] = val
            
            img = get_page_image(title)
            if img:
                item["image"] = img
            
            items.append(item)
            all_individual_items[title.lower()] = item
            time.sleep(0.3)
        
        results[key] = items
        if items:
            path = OUTPUT_DIR / f"{key}.json"
            with open(path, "w") as f:
                json.dump(items, f, indent=2)
            logger.info("✅ %s: %d saved", key, len(items))
    
    # Phase 2: Scrape listing pages for missing items
    logger.info("=" * 60)
    logger.info("Scraping listing pages for missing armor items...")
    
    for result_key, page_title in listing_pages.items():
        logger.info("  Checking listing page: %s", page_title)
        soup = parse_page(page_title)
        if not soup:
            continue
        
        table_items = extract_listing_table(soup)
        logger.info("  Found %d items in %s table", len(table_items), page_title)
        
        for ti in table_items:
            name = ti["name"]
            if name.lower() not in all_individual_items:
                logger.info("    ➕ New item from listing: %s", name)
                new_item = {
                    "name": name,
                    "id": name.lower().replace(" ", "-").replace("'", ""),
                    "source": "Looting",
                }
                row_data = ti.get("data", {})
                for hdr, val in row_data.items():
                    h = hdr.lower().strip()
                    if "armor" in h or "nij" in h or "rating" in h:
                        new_item["nij"] = val
                    elif "material" in h:
                        new_item["material"] = val
                    elif "weight" in h:
                        nums = re.findall(r'[\d.]+', val)
                        if nums:
                            new_item["weight"] = float(nums[0])
                    elif "source" in h or "vendor" in h:
                        new_item["source"] = val
                
                img_url = ti.get("image", "")
                if img_url and img_url.startswith("http"):
                    new_item["image"] = img_url
                
                if result_key in ("vests", "plate_carriers"):
                    results.setdefault("vests", []).append(new_item)
                elif result_key == "helmets":
                    results.setdefault("helmets", []).append(new_item)
        
        time.sleep(0.3)
    
    for key in ["vests", "helmets"]:
        items = results.get(key, [])
        if items:
            path = OUTPUT_DIR / f"{key}.json"
            with open(path, "w") as f:
                json.dump(items, f, indent=2)
            logger.info("✅ %s (after listing page merge): %d saved", key, len(items))
    
    return results


def scrape_ammo():
    """Scrape ammunition data with penetration info from caliber pages."""
    logger.info("=" * 60)
    logger.info("Scraping Ammunition...")
    
    caliber_categories = [
        ".300 AAC Blackout ammunition",
        ".45 ACP Ammunition",
        "5.45x39mm ammunition",
        "5.56x45mm ammunition",
        "7.62x25mm ammunition",
        "7.62x39mm ammunition",
        "7.62x51mm ammunition",
        "7.62x54mm R Ammunition",
        "7.65 Browning ammunition",
        "9x19mm ammunition",
        "12-Gauge ammunition",
        "4.6x30mm",
    ]
    
    caliber_listing_pages = {
        ".300 AAC Blackout ammunition": ".300 AAC Blackout",
        ".45 ACP Ammunition": ".45 ACP",
        "5.45x39mm ammunition": "5.45x39mm",
        "5.56x45mm ammunition": "5.56x45mm NATO",
        "7.62x25mm ammunition": "7.62x25mm",
        "7.62x39mm ammunition": "7.62x39mm",
        "7.62x51mm ammunition": "7.62x51mm NATO",
        "7.62x54mm R Ammunition": "7.62x54R LPS (57-N-323S)",
        "7.65 Browning ammunition": "7.65mm Browning",
        "9x19mm ammunition": "9x19mm",
        "12-Gauge ammunition": "12 Gauge",
        "4.6x30mm": "4.6x30mm",
    }
    
    all_ammo_pages = []
    seen_titles = set()
    
    for cal_cat in caliber_categories:
        params = {
            "action": "query",
            "list": "categorymembers",
            "cmtitle": f"Category:{cal_cat}",
            "cmlimit": 50,
            "cmtype": "page",
        }
        data = api_call(params)
        if data:
            for m in data.get("query", {}).get("categorymembers", []):
                title = m["title"]
                if title not in seen_titles and m["ns"] == 0:
                    seen_titles.add(title)
                    all_ammo_pages.append(title)
        time.sleep(0.3)
    
    logger.info("Found %d unique ammo pages across all calibers", len(all_ammo_pages))
    
    caliber_pen_data = {}
    calibers_without_pen = set()
    
    for cal_cat, listing_page in caliber_listing_pages.items():
        logger.debug("  Checking listing page: %s", listing_page)
        soup = parse_page(listing_page)
        if not soup:
            calibers_without_pen.add(cal_cat)
            time.sleep(0.3)
            continue
        
        for table in soup.find_all("table", class_=re.compile(r"wikitable|article-table|sortable|fandom-table")):
            rows = table.find_all("tr")
            if len(rows) < 2:
                continue
            
            headers = [h.get_text(" ", strip=True).lower() for h in rows[0].find_all(["th", "td"])]
            
            pen_col = None
            name_col = None
            source_col = None
            velocity_col = None
            
            for j, h in enumerate(headers):
                h_clean = re.sub(r'[^a-z]', '', h)
                if "name" in h_clean:
                    name_col = j
                elif "stopped" in h_clean or "armor" in h_clean or "penetr" in h_clean:
                    pen_col = j
                elif "source" in h_clean or "vendor" in h_clean:
                    source_col = j
                elif "velocity" in h_clean or "muzzle" in h_clean:
                    velocity_col = j
            
            if name_col is None:
                continue
            
            for row in rows[1:]:
                cells = row.find_all(["td", "th"])
                if not cells or len(cells) <= name_col:
                    continue
                
                name = cells[name_col].get_text(" ", strip=True)
                if not name:
                    continue
                
                pen_value = cells[pen_col].get_text(" ", strip=True) if pen_col is not None and pen_col < len(cells) else ""
                source_value = cells[source_col].get_text(" ", strip=True) if source_col is not None and source_col < len(cells) else ""
                velocity_value = cells[velocity_col].get_text(" ", strip=True) if velocity_col is not None and velocity_col < len(cells) else ""
                
                caliber_pen_data[name.lower()] = {
                    "penetration": pen_value,
                    "source": source_value,
                    "velocity": velocity_value,
                    "caliber_category": cal_cat,
                }
        
        time.sleep(0.3)
    
    logger.info("  Got pen data for %d ammo types", len(caliber_pen_data))
    
    ammo_items = []
    seen_ammo = set()
    
    for title in sorted(all_ammo_pages):
        logger.debug("  %s", title)
        soup = parse_page(title)
        info = parse_infobox(soup)
        
        item = {"name": title}
        
        for wiki_key, our_key in {
            "caliber": "caliber", "calibre": "caliber",
            "type": "type",
            "velocity": "velocity",
            "weight": "weight",
            "damage": "damage",
        }.items():
            if wiki_key in info:
                item[our_key] = info[wiki_key]
        
        pen_info = caliber_pen_data.get(title.lower(), {})
        if pen_info:
            pen_val = pen_info.get("penetration", "")
            if pen_val:
                item["stopped_by_armor_class"] = pen_val
                item["penetration"] = pen_val
            if pen_info.get("source"):
                item["source"] = pen_info["source"]
            if pen_info.get("velocity") and not item.get("velocity"):
                item["velocity"] = pen_info["velocity"]
        
        img = get_page_image(title)
        if img:
            item["image"] = img
        
        if title.lower() not in seen_ammo:
            seen_ammo.add(title.lower())
            ammo_items.append(item)
        else:
            for existing in ammo_items:
                if existing["name"].lower() == title.lower():
                    if pen_info:
                        if pen_info.get("penetration"):
                            existing["stopped_by_armor_class"] = pen_info["penetration"]
                            existing["penetration"] = pen_info["penetration"]
                        if pen_info.get("source") and not existing.get("source"):
                            existing["source"] = pen_info["source"]
                    break
        
        time.sleep(0.3)
    
    if ammo_items:
        seen = set()
        unique = []
        for a in ammo_items:
            key = a.get("name", "").lower()
            if key not in seen:
                seen.add(key)
                unique.append(a)
        
        path = OUTPUT_DIR / "ammo.json"
        with open(path, "w") as f:
            json.dump(unique, f, indent=2)
        logger.info("✅ Ammo: %d entries saved (with penetration data)", len(unique))
    
    return ammo_items


def scrape_attachments():
    """Scrape weapon attachments, magazines, and mods."""
    categories = {
        "magazines": "Magazines",
        "weapon_parts": "Weapon Parts",
        "helmet_mods": "Helmet Mods",
        "helmet_mounts": "Helmet Mounts",
    }
    
    for key, cat in categories.items():
        logger.info("=" * 60)
        logger.info("Scraping %s (category: %s)...", key, cat)
        pages = get_category_members(cat, limit=200)
        logger.info("Found %d pages", len(pages))
        
        items = []
        for i, p in enumerate(pages):
            title = p["title"]
            if title.startswith("Category:") or title.startswith("Template:"):
                continue
            soup = parse_page(title)
            info = parse_infobox(soup)
            
            item = {"name": title}
            for wiki_key in ("type", "caliber", "capacity", "weight", "effect", "compatibility"):
                if wiki_key in info:
                    item[wiki_key] = info[wiki_key]
            
            img = get_page_image(title)
            if img:
                item["image"] = img
            
            items.append(item)
            time.sleep(0.3)
        
        if items:
            path = OUTPUT_DIR / f"{key}.json"
            with open(path, "w") as f:
                json.dump(items, f, indent=2)
            logger.info("✅ %s: %d saved", key, len(items))


def scrape_throwables():
    """Scrape throwables/grenades from Category:Throwables."""
    logger.info("=" * 60)
    logger.info("Scraping Throwables...")
    
    pages = get_category_members("Throwables", limit=50)
    logger.info("Found %d throwable pages", len(pages))
    
    throwables = []
    for i, p in enumerate(pages):
        title = p["title"]
        if title.startswith("Template:") or title.startswith("Category:"):
            continue
        logger.debug("  [%d/%d] %s", i + 1, len(pages), title)
        
        soup = parse_page(title)
        info = parse_infobox(soup)
        
        item = {
            "name": title,
            "id": title.lower().replace(" ", "-").replace("'", "").replace("(", "").replace(")", ""),
            "type": "throwable",
        }
        
        field_map = {
            "type": "grenade_type",
            "effect": "effect",
            "weight": "weight",
            "radius": "radius",
            "explosion_radius": "radius",
            "fuse_time": "fuse_time",
            "source": "source",
            "vendor": "source",
        }
        for wiki_key, our_key in field_map.items():
            if wiki_key in info:
                val = info[wiki_key]
                if our_key == "weight":
                    nums = re.findall(r'[\d.]+', val)
                    if nums:
                        item[our_key] = float(nums[0])
                else:
                    item[our_key] = val
        
        img = get_page_image(title)
        if img:
            item["image"] = img
        
        throwables.append(item)
        time.sleep(0.3)
    
    if throwables:
        path = OUTPUT_DIR / "throwables.json"
        with open(path, "w") as f:
            json.dump(throwables, f, indent=2)
        logger.info("✅ Throwables: %d saved", len(throwables))
    
    return throwables


def scrape_tasks():
    """Scrape all tasks/missions from comprehensive category list."""
    logger.info("=" * 60)
    logger.info("Scraping Tasks...")
    
    categories = [
        "Main tasks", "Side tasks", "Contracts", "Tasks", "Task items",
        "Squad Strike Missions Item", "Main task", "Side task",
        "Hidden Task", "Contract", "Squad Strike Missions",
        "Task Item", "Task",
    ]
    all_pages = []
    seen_titles = set()
    
    for cat in categories:
        try:
            pages = get_category_members(cat, limit=500)
            for p in pages:
                if p["title"] not in seen_titles and p["ns"] == 0:
                    seen_titles.add(p["title"])
                    p["from_category"] = cat
                    all_pages.append(p)
            logger.info("  Category %s: %d pages", cat, len(pages))
        except Exception as e:
            logger.warning("  Category %s: error - %s", cat, e)
    
    logger.info("Total unique task pages: %d", len(all_pages))
    
    tasks = []
    for i, page in enumerate(all_pages):
        title = page["title"]
        category = page.get("from_category", "Tasks")
        logger.debug("  [%d/%d] %s", i + 1, len(all_pages), title)
        
        soup = parse_page(title)
        if not soup:
            continue
        
        task = {
            "id": title.lower().replace(" ", "-").replace("'", "").replace(",", ""),
            "name": title,
            "vendor": "",
            "area": "",
            "type": "unknown",
            "objectives": [],
        }
        
        cl = category.lower()
        if "contract" in cl or "squad" in cl:
            task["type"] = "squad"
        elif "main" in cl:
            task["type"] = "main"
        elif "side" in cl:
            task["type"] = "side"
        elif "hidden" in cl:
            task["type"] = "hidden"
        
        info = parse_infobox(soup)
        for wiki_key, our_key in {"given by": "vendor", "location": "area", "area": "area", "objectives": "objectives_text"}.items():
            if wiki_key in info:
                val = info[wiki_key]
                val = re.sub(r'[\u200b\xa0]', '', val)
                if our_key in ("vendor", "area"):
                    task[our_key] = val
                elif our_key == "objectives_text":
                    task["objectives"] = [o.strip() for o in val.split("\n") if o.strip()][:8]
        
        if not task["objectives"]:
            task["objectives"] = extract_objectives(soup)
        
        tasks.append(task)
        time.sleep(0.3)
    
    if tasks:
        seen = set()
        unique_tasks = []
        for t in tasks:
            if t["name"] not in seen:
                seen.add(t["name"])
                unique_tasks.append(t)
        
        path = OUTPUT_DIR / "tasks.json"
        with open(path, "w") as f:
            json.dump(unique_tasks, f, indent=2)
        logger.info("✅ Tasks: %d saved (deduplicated from %d)", len(unique_tasks), len(tasks))
        
        by_vendor = {}
        for t in unique_tasks:
            v = t["vendor"] or "Unknown"
            by_vendor.setdefault(v, []).append(t)
        for v, ts in sorted(by_vendor.items()):
            logger.info("  %s: %d tasks", v, len(ts))
    
    return unique_tasks if 'unique_tasks' in dir() else tasks


def scrape_all_misc():
    """Scrape ALL remaining wiki categories comprehensively.
    Includes medical, gear, containers, loot, provisions, food, drinks,
    weapon parts, night vision, equipment, tools, cosmetics, AND new categories
    from the wiki homepage (glasses, face cover, headsets, headwear, belts, apparel)."""
    
    ALL_CATEGORIES = {
        # Medical & Supplies
        "medical": "Medical Item",
        "gear": "Gear",
        "containers": "Containers",
        "loot_containers": "Loot Containers",
        "loot": "Loot",
        
        # Provisions (Food & Drink)
        "provisions": "Provisions",
        "food": "Food",
        "drinks": "Drinks",
        
        # Weapon Parts subcategories
        "barrels": "Barrels",
        "muzzle_devices": "Muzzle Devices",
        "suppressors": "Suppressors",
        "stocks": "Stocks",
        "stock_adapters": "Stock Adapters",
        "pistol_grips": "Pistol Grips",
        "foregrips": "Foregrips",
        "magazines": "Magazines",
        
        # Tactical & helmets mods
        "night_vision": "Night Vision Devices",
        "helmet_mods": "Helmet Mods",
        "helmet_mounts": "Helmet Mounts",
        
        # Equipment & tools
        "repair_kits": "Repair Kits",
        "tools": "Tool",
        "military_equipment": "Military Equipment",
        "task_items": "Task Item",
        
        # Cosmetics
        "weapon_camos": "Weapons camouflage",
        
        # NEW: Missing Gear & Items categories from wiki homepage
        "glasses": "Glasses",
        "face_cover": "Face Cover",
        "headsets": "Headsets",
        "headwear_items": "Headwear",
        "belts": "Belts",
        "apparel": "Apparel",
    }
    
    for key, cat in ALL_CATEGORIES.items():
        logger.info("=" * 60)
        logger.info("Scraping %s (category: %s)...", key, cat)
        pages = get_category_members(cat, limit=500)
        logger.info("Found %d pages", len(pages))
        
        items = []
        for p in pages:
            title = p["title"]
            if title.startswith("Category:") or title.startswith("Template:"):
                continue
            soup = parse_page(title)
            info = parse_infobox(soup)
            
            item = {"name": title}
            for wiki_key in ("type", "weight", "grid", "effect", "uses", "capacity",
                           "caliber", "magazine", "rounds", "speed", "damage",
                           "penetration", "armor_class", "material", "source",
                           "vendor", "rep_level", "price", "rarity", "slots",
                           "quantity", "duration", "healing", "food", "hydration",
                           "energy", "morale"):
                if wiki_key in info:
                    item[wiki_key] = info[wiki_key]
            
            for alt in ("ammo_type", "ammotype", "cartridge", "size", "storage"):
                if alt in info:
                    item[alt] = info[alt]
            
            img = get_page_image(title)
            if img:
                item["image"] = img
            
            items.append(item)
            time.sleep(0.3)
        
        if items:
            path = OUTPUT_DIR / f"{key}.json"
            with open(path, "w") as f:
                json.dump(items, f, indent=2, ensure_ascii=False)
            logger.info("✅ %s: %d items saved", key, len(items))
        else:
            logger.info("⚠️  %s: no items found", key)
    
    # Phase 2: Scrape listing pages that have item tables but no category members
    # (Loot, Apparel, Provisions have items ONLY in tables on their listing pages)
    LISTING_PAGE_DATASETS = {
        "loot_items": "Loot",
        "apparel_items": "Apparel",
        "provisions": "Provisions",  # Replaces the single-page entry with table data
    }
    
    logger.info("=" * 60)
    logger.info("Scraping listing pages for table-based items...")
    
    for key, page_title in LISTING_PAGE_DATASETS.items():
        logger.info("  Scraping %s from %s...", key, page_title)
        soup = parse_page(page_title)
        if not soup:
            logger.warning("  Could not parse %s", page_title)
            continue
        
        table_items = extract_listing_table(soup)
        logger.info("  Found %d items in tables", len(table_items))
        
        if table_items:
            items = []
            for ti in table_items:
                name = ti["name"]
                if not name or len(name) < 2:
                    continue
                item = {
                    "name": name,
                    "id": name.lower().replace(" ", "-").replace("'", "").replace("*", "").strip("-"),
                }
                # Extract any useful fields from table data
                row_data = ti.get("data", {})
                for hdr, val in row_data.items():
                    h = hdr.lower().strip()
                    if h in ("type", "category", "class", "rarity", "source", "location", "weight", "value", "price", "grid", "slots", "description"):
                        item[h] = val
                # Get image
                img_url = ti.get("image", "")
                if img_url and img_url.startswith("http"):
                    item["image"] = img_url
                items.append(item)
            
            if items:
                # Deduplicate by name
                seen = set()
                unique = []
                for it in items:
                    n = it["name"].lower()
                    if n not in seen:
                        seen.add(n)
                        unique.append(it)
                
                path = OUTPUT_DIR / f"{key}.json"
                with open(path, "w") as f:
                    json.dump(unique, f, indent=2, ensure_ascii=False)
                logger.info("✅ %s: %d items saved (from listing page)", key, len(unique))
        
        time.sleep(0.3)


def scrape_item_images():
    """Scrape images from ALL wiki categories for comprehensive image mapping.
    Now includes ALL Gear & Items categories plus Factions."""
    logger.info("=" * 60)
    logger.info("Scraping ALL item images from every category...")
    
    image_categories = [
        "Weapons", "Armor Vest", "Helmet", "Plate Carriers", "Backpacks",
        "Tactical Rigs", "Ammunition", "Throwables", "Keys", "Keycards",
        "Medical Item", "Gear", "Containers", "Loot Containers", "Loot",
        "Provisions", "Food", "Drinks",
        "Barrels", "Muzzle Devices", "Suppressors", "Stocks",
        "Stock Adapters", "Pistol Grips", "Foregrips", "Magazines",
        "Night Vision Devices", "Helmet Mods", "Helmet Mounts",
        "Repair Kits", "Tool", "Military Equipment", "Task Item",
        "Weapon Parts", "Weapons camouflage",
        # NEW categories
        "Glasses", "Face Cover", "Headsets", "Headwear", "Belts", "Apparel",
    ]
    
    images = {}
    seen = set()
    
    for cat in image_categories:
        logger.info("Collecting images from %s...", cat)
        pages = get_category_members(cat, limit=500)
        
        for p in pages:
            title = p["title"]
            if title.startswith("Category:") or title.startswith("Template:"):
                continue
            if title in seen:
                continue
            seen.add(title)
            
            img = get_page_image(title)
            if img:
                images[title] = img
                time.sleep(0.15)
    
    listing_pages = {
        "Weapons": "weapon",
        "Armor Vests": "armor_vest",
        "Plate Carriers": "plate_carrier",
        "Backpacks": "backpack",
        "Tactical Rigs": "tactical_rig",
        "Medical Item": "medical",
        "Provisions": "provision",
        "Ammunition": "ammo",
        "Keys": "key",
        "Keycards": "keycard",
        "Throwables": "throwable",
        "Magazines": "magazine",
        "Weapon Parts": "attachment",
        "Repair Kits": "repair",
        "Tool": "tool",
        "Glasses": "glasses",
        "Face Cover": "face_cover",
        "Headsets": "headsets",
        "Headwear": "headwear",
        "Belts": "belts",
        "Apparel": "apparel",
    }
    
    caliber_listing_pages = [
        ".300 AAC Blackout",
        ".45 ACP",
        "5.45x39mm",
        "5.56x45mm NATO",
        "7.62x25mm",
        "7.62x39mm",
        "7.62x51mm NATO",
        "7.65mm Browning",
        "9x19mm",
        "12 Gauge",
        "4.6x30mm",
    ]
    
    all_images = {}
    
    for page_title, category in listing_pages.items():
        logger.info("  Scraping images from: %s", page_title)
        soup = parse_page(page_title)
        if not soup:
            logger.warning("  Could not parse %s", page_title)
            time.sleep(0.3)
            continue
        
        table_items = extract_listing_table(soup)
        logger.info("    Found %d items in tables", len(table_items))
        
        for ti in table_items:
            name = ti["name"]
            img_url = ti.get("image", "")
            if name and img_url and name not in all_images:
                if img_url.startswith("http"):
                    all_images[name] = img_url
        
        time.sleep(0.3)
    
    for page_title in caliber_listing_pages:
        logger.info("  Scraping ammo images from: %s", page_title)
        soup = parse_page(page_title)
        if not soup:
            time.sleep(0.3)
            continue
        
        table_items = extract_listing_table(soup)
        logger.info("    Found %d items in tables", len(table_items))
        
        for ti in table_items:
            name = ti["name"]
            img_url = ti.get("image", "")
            if name and img_url and name not in all_images:
                if img_url.startswith("http"):
                    all_images[name] = img_url
        
        time.sleep(0.3)
    
    data_files = OUTPUT_DIR.glob("*.json")
    for f in data_files:
        if f.name == "item_images.json":
            continue
        try:
            with open(f) as fh:
                data = json.load(fh)
            if isinstance(data, list):
                for item in data:
                    name = item.get("name", "")
                    if name and name not in all_images:
                        img = item.get("image", "")
                        if img and img.startswith("http"):
                            all_images[name] = img
            elif isinstance(data, dict):
                for key, items in data.items():
                    if isinstance(items, list):
                        for item in items:
                            name = item.get("name", "")
                            if name and name not in all_images:
                                img = item.get("image", "")
                                if img and img.startswith("http"):
                                    all_images[name] = img
        except Exception:
            pass
    
    if all_images:
        path = OUTPUT_DIR / "item_images.json"
        with open(path, "w") as f:
            json.dump(all_images, f, indent=2)
        logger.info("✅ item_images.json: %d entries saved", len(all_images))
    
    return all_images


def scrape_keys():
    """Scrape keys and keycards from Categories:Keys and Keycards."""
    logger.info("=" * 60)
    logger.info("Scraping Keys & Keycards...")
    
    categories = {
        "keys": "Keys",
        "keycards": "Keycards",
    }
    
    for key, cat in categories.items():
        logger.info("Scraping %s (category: %s)...", key, cat)
        pages = get_category_members(cat, limit=500)
        logger.info("Found %d pages", len(pages))
        
        items = []
        for i, p in enumerate(pages):
            title = p["title"]
            if title.startswith("Category:") or title.startswith("Template:"):
                continue
            
            soup = parse_page(title)
            info = parse_infobox(soup)
            
            item = {
                "name": title,
                "id": title.lower().replace(" ", "-").replace("'", "").replace("(", "").replace(")", ""),
            }
            
            for wiki_key, our_key in {
                "type": "type",
                "location": "location",
                "area": "area",
                "source": "source",
                "use": "use",
                "description": "description",
                "quest_item": "quest_item",
            }.items():
                if wiki_key in info:
                    item[our_key] = info[wiki_key]
            
            img = get_page_image(title)
            if img:
                item["image"] = img
            
            items.append(item)
            time.sleep(0.3)
        
        if items:
            path = OUTPUT_DIR / f"{key}.json"
            with open(path, "w") as f:
                json.dump(items, f, indent=2)
            logger.info("✅ %s: %d saved", key, len(items))


def scrape_factions():
    """Scrape faction info pages (lore, description, logo)."""
    logger.info("=" * 60)
    logger.info("Scraping Factions...")
    
    factions_info = [
        "Lamang Recovery Initiative",
        "Mithras Security Systems", 
        "Crimson Shield International",
        "Lamang Army Forces",
        "Bandits",
        "Lamang Liberation Army",
    ]
    
    factions = []
    for title in factions_info:
        logger.info("  Scraping faction: %s", title)
        
        soup = parse_page(title)
        info = parse_infobox(soup)
        
        faction = {
            "name": title,
            "id": title.lower().replace(" ", "-"),
        }
        
        if info.get("_image"):
            faction["image"] = info["_image"]
        
        if soup:
            text = soup.get_text(" ", strip=True)
            desc_match = re.search(r'(?:Description\s*\[?\s*edit\s*\]?\s*)?(.+?)(?:\[|\n\s*\n)', text, re.DOTALL)
            if desc_match:
                desc = desc_match.group(1).strip()
                faction["description"] = desc[:500]
            else:
                faction["description"] = text[:300]
        
        if not faction.get("image"):
            img = get_page_image(title)
            if img:
                faction["image"] = img
        
        factions.append(faction)
        time.sleep(0.3)
    
    if factions:
        path = OUTPUT_DIR / "factions.json"
        with open(path, "w") as f:
            json.dump(factions, f, indent=2, ensure_ascii=False)
        logger.info("✅ Factions: %d saved", len(factions))
    
    return factions


def scrape_info_pages():
    """Scrape informational wiki pages (Basics, Systems, Locations, etc.).
    These are single pages with game info, not item lists with multiple entries."""
    logger.info("=" * 60)
    logger.info("Scraping info/reference pages...")
    
    INFO_PAGES = {
        # Basics
        "game_modes": "Game modes",
        "lamang_island": "Lamang Island",
        "locations": "Locations",
        "changelog": "Changelog",
        "system_requirements": "System requirements",
        
        # Systems
        "health": "Health system",
        "ballistics": "Ballistics",
        "looting": "Looting",
        "experience": "Experience",
        "trading": "Trading",
    }
    
    pages = []
    for key, title in INFO_PAGES.items():
        logger.info("  Scraping info page: %s", title)
        
        params = {
            "action": "parse",
            "page": title,
            "prop": "text",
            "formatversion": "2",
        }
        data = api_call(params)
        html = data.get("parse", {}).get("text", "") if data else ""
        
        soup = None
        if html:
            soup = __import__("bs4").BeautifulSoup(html, "lxml")
        
        info = parse_infobox(soup) if soup else {}
        
        page = {
            "name": title,
            "id": key,
        }
        
        if info.get("_image"):
            page["image"] = info["_image"]
        
        if soup:
            text = soup.get_text(" ", strip=True)
            text = re.sub(r'\[\s*\d+\s*\]', '', text)
            text = re.sub(r'\s+', ' ', text).strip()
            page["summary"] = text[:1000]
            page["content_length"] = len(text)
        
        pages.append(page)
        time.sleep(0.3)
    
    if pages:
        path = OUTPUT_DIR / "info_pages.json"
        with open(path, "w") as f:
            json.dump(pages, f, indent=2, ensure_ascii=False)
        logger.info("✅ Info pages: %d saved", len(pages))
    
    return pages


# ─── Main ───

def main():
    parser = argparse.ArgumentParser(description="GZW Wiki Scraper v2")
    parser.add_argument("--weapons", action="store_true")
    parser.add_argument("--armor", action="store_true")
    parser.add_argument("--ammo", action="store_true")
    parser.add_argument("--attachments", action="store_true")
    parser.add_argument("--tasks", action="store_true")
    parser.add_argument("--other", action="store_true")
    parser.add_argument("--misc", action="store_true", help="Scrape all remaining categories (food, provisions, weapon parts, tools, glasses, etc.)")
    parser.add_argument("--throwables", action="store_true")
    parser.add_argument("--images", action="store_true")
    parser.add_argument("--keys", action="store_true")
    parser.add_argument("--factions", action="store_true")
    parser.add_argument("--info", action="store_true", help="Scrape info/reference pages (Basics, Systems, Locations)")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    
    if not any([args.weapons, args.armor, args.ammo, args.attachments, args.tasks, args.misc,
                args.throwables, args.images, args.keys, args.factions, args.info, args.all]):
        args.all = True
    
    logger.info("🏴‍☠️ GZW Wiki Scraper v2 — Full Wiki Scrape")
    logger.info("Output: %s", OUTPUT_DIR)
    
    if args.all or args.weapons:
        scrape_weapons()
    if args.all or args.armor:
        scrape_armor()
    if args.all or args.ammo:
        scrape_ammo()
    if args.all or args.attachments:
        scrape_attachments()
    if args.all or args.tasks:
        scrape_tasks()
    if args.all or args.misc:
        scrape_all_misc()
    if args.all or args.throwables:
        scrape_throwables()
    if args.all or args.keys:
        scrape_keys()
    if args.all or args.factions:
        scrape_factions()
    if args.all or args.info:
        scrape_info_pages()
    if args.all or args.images:
        scrape_item_images()
    
    # Generate summary
    summaries = []
    for f in sorted(OUTPUT_DIR.glob("*.json")):
        try:
            with open(f) as fh:
                data = json.load(fh)
            summaries.append((f.stem, len(data) if isinstance(data, list) else "obj"))
        except:
            pass
    
    logger.info("=" * 60)
    logger.info("✅ Scrape complete! Files:")
    for name, count in sorted(summaries):
        logger.info("  %s.json: %s items", name, count)


if __name__ == "__main__":
    main()
