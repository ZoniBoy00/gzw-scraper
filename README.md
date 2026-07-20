# GZW Scraper

**Gray Zone Warfare** — Complete wiki data scraper.

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Scrapes **the entire** [GZW Fandom Wiki](https://gray-zone-warfare.fandom.com) — all 6,771+ pages, 4,448+ files — and pushes structured JSON to [gzw-data](https://github.com/ZoniBoy00/gzw-data) every Monday.

## Coverage

### 🗺️ All 4 Wiki Main Categories

**1. Basics**
- Game modes, Lamang Island, Locations, Changelog, System requirements

**2. Systems**
- Health, Ballistics, Looting, Experience, Trading

**3. Gear & Items** (full item database)
- Weapons, Ammunition, Armor Vests, Plate Carriers
- Backpacks, Tactical Rigs, Containers
- Glasses, Face Cover, Headsets, Headwear, Belts, Apparel
- Keys, Loot, Medical, Provisions
- Weapon Parts (barrels, stocks, grips, suppressors, magazines, etc.)
- Helmet Mods, Night Vision, Mounts
- Tools, Repair Kits, Military Equipment
- Throwables, Task Items, Weapon Camos

**4. Factions**
- Lamang Recovery Initiative, Mithras Security Systems, Crimson Shield International
- Lamang Army Forces, Bandits, Lamang Liberation Army

### 📊 Output Files

| Category | Files | Items expected |
|----------|-------|----------------|
| Weapons | `weapons.json` | 44+ |
| Ammunition | `ammo.json` | 67+ |
| Armor | `vests.json`, `helmets.json` | 60+ |
| Backpacks | `backpacks.json` | 17+ |
| Tactical rigs | `rigs.json` | 12+ |
| Keys & keycards | `keys.json`, `keycards.json` | 124+ |
| Tasks | `tasks.json` | 159+ |
| Medical | `medical.json` | 30+ |
| Provisions | `provisions.json`, `food.json`, `drinks.json` | 50+ |
| Weapon parts | `magazines.json`, `barrels.json`, `muzzle_devices.json`, `suppressors.json`, `stocks.json`, `stock_adapters.json`, `pistol_grips.json`, `foregrips.json`, `weapon_parts.json` | 200+ |
| Helmet mods | `helmet_mods.json`, `helmet_mounts.json`, `night_vision.json` | 20+ |
| Gear | `gear.json`, `containers.json`, `loot.json`, `loot_containers.json` | 80+ |
| Wearables | `glasses.json`, `face_cover.json`, `headsets.json`, `headwear_items.json`, `belts.json`, `apparel.json` | 60+ |
| Equipment | `repair_kits.json`, `tools.json`, `military_equipment.json` | 30+ |
| Cosmetics | `weapon_camos.json` | 20+ |
| Misc | `throwables.json`, `task_items.json` | 15+ |
| Factions | `factions.json` | 6 |
| Reference | `info_pages.json` | 10 |
| Images | `item_images.json` | 400+ |

## Pipeline

```
scrape.py
  └─ weapons, armor, ammo, attachments, misc, keys, factions, info pages, images
     │
     ├── scrape_weapons()     — weapon pages
     ├── scrape_armor()       — vests, helmets, rigs, backpacks + listing page merge
     ├── scrape_ammo()        — penetration data from caliber listing pages
     ├── scrape_attachments() — magazines, weapon parts, helmet mods/mounts
     ├── scrape_all_misc()    — 30+ categories (medical, provisions, weapon parts, glasses, etc.)
     ├── scrape_tasks()       — missions (main, side, hidden, contracts)
     ├── scrape_throwables()  — grenades
     ├── scrape_keys()        — keys & keycards
     ├── scrape_factions()    — faction lore & logos
     ├── scrape_info_pages()  — reference pages (basics, systems)
     └── scrape_item_images() — comprehensive image collection from ALL categories
             │
             ▼
         data/*.json → pushed to gzw-data
```

## Local usage

```bash
pip install requests beautifulsoup4 lxml

# Full scrape (everything)
python scrape.py --all

# Or selective scraping
python scrape.py --weapons --armor --ammo
python scrape.py --misc       # all 30+ remaining item categories
python scrape.py --keys       # keys & keycards
python scrape.py --factions   # faction lore
python scrape.py --info       # reference pages
python scrape.py --images     # image URL collection

# Generated files are in data/
ls data/
```

## GitHub Actions

- **Schedule:** Every Monday at 06:00 UTC
- **Trigger:** `workflow_dispatch` (manual), `push`, `repository_dispatch`
- **Output:** Results committed to `gzw-data/data/` with auto-commit

## Architecture

```
gzw-scraper (this repo — Python, scheduled)
    │ weekly scrape → push
    ▼
gzw-data (data + dynamic API on Vercel)
    │ rebuild on push
    ▼
gzw-tools (frontend SPA — optional consumer)
```

## Rate Limiting

The scraper respects the wiki by using 0.3s delays between page requests. A full scrape takes 20-45 minutes depending on wiki response times.

## Data Format

Every item follows this base structure:

```json
{
  "name": "Item Name",
  "id": "item-name-slug",
  "type": "Category",
  "...type-specific-fields": "...",
  "image": "https://static.wikia.nocookie.net/..."
}
```

## License

MIT — Game content belongs to M.A.G. Studios.
