# GZW Scraper

**Gray Zone Warfare** — Wiki data scraper.

Scrapes the [GZW Fandom Wiki](https://gray-zone-warfare.fandom.com) and pushes structured data to the [gzw-data](https://github.com/ZoniBoy00/gzw-data) repository.

## What it scrapes

- Weapons (stats, caliber, mag size, fire rate)
- Armor (vests, plate carriers, helmets with NIJ class, material)
- Backpacks & tactical rigs
- Keys & keycards (124+ across 12 locations)
- Tasks/missions (278+ with objectives, rewards, categories)
- Throwables/grenades
- Item images (199+ wiki URLs)

## How it works

```
scrape.py → enrich_tasks.py → categorize_tasks.py → gen_frontend_data.py
```

1. **scrape.py** — Crawls wiki pages for all item categories
2. **enrich_tasks.py** — Extracts objectives, rewards, and item links
3. **categorize_tasks.py** — Sorts tasks into Main/Side/Hidden/Contract/Squad
4. **gen_frontend_data.py** — Generates clean JSON output files

## Data output

Generated JSON files land in `data/` and are pushed to [ZoniBoy00/gzw-data](https://github.com/ZoniBoy00/gzw-data) via GitHub Actions.

## Local usage

```bash
pip install requests beautifulsoup4 lxml
python scrape.py --all
python enrich_tasks.py
python categorize_tasks.py
python gen_frontend_data.py
```

## GitHub Actions

Runs every Monday at 06:00 UTC. Results are automatically pushed to `gzw-data/data/`.

## License

MIT — Game content belongs to M.A.G. Studios.
