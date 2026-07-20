# GZW Scraper

**Gray Zone Warfare** — Wiki data scraper.

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

Scrapes the [GZW Fandom Wiki](https://gray-zone-warfare.fandom.com) and pushes structured JSON data to the [gzw-data](https://github.com/ZoniBoy00/gzw-data) repository every Monday.

## What it scrapes

| Data | Items | Details |
|------|-------|---------|
| Weapons | 51 | Stats, caliber, mag size, fire rate, source, image |
| Armor | 61 | Vests, plate carriers, helmets — NIJ class, material, weight |
| Backpacks | 16 | Weight, grid size, image |
| Tactical rigs | 11 | Weight, grid size, image |
| Keys & keycards | 124 | Location, wiki link, task flag |
| Tasks/missions | 278 | Objectives, rewards, categories (Main/Side/Hidden) |
| Throwables | 8 | Grenades: frag, smoke, stun |
| Item images | 199+ | Wiki image URL lookup |

## Pipeline

```
scrape.py → enrich_tasks.py → categorize_tasks.py → gen_frontend_data.py
```

1. **`scrape.py`** — Crawls wiki pages for all item categories
2. **`enrich_tasks.py`** — Extracts objectives, rewards, and item links from task pages
3. **`categorize_tasks.py`** — Sorts tasks into Main/Side/Hidden/Contract/Squad
4. **`gen_frontend_data.py`** — Generates clean, normalized JSON output files

## Output

Generated JSON files land in `data/` and are automatically pushed to **[ZoniBoy00/gzw-data](https://github.com/ZoniBoy00/gzw-data)**.

## Local usage

```bash
pip install requests beautifulsoup4 lxml

# Run full pipeline
python scrape.py --all
python enrich_tasks.py
python categorize_tasks.py
python gen_frontend_data.py

# Generated files are in data/
ls data/
```

## GitHub Actions

- **Schedule:** Every Monday at 06:00 UTC
- **Trigger:** Also supports `workflow_dispatch` (manual) and `repository_dispatch`
- **Target:** Results pushed to `gzw-data/data/` with auto-commit

## Architecture

```
gzw-scraper (this repo)
    ↓ weekly scrape + push
gzw-data (data + API + test page)
    ↓ data sync
gzw-tools (frontend SPA)
```

## License

MIT — Game content belongs to M.A.G. Studios.
