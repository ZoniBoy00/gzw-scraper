# GZW Scraper

**Gray Zone Warfare** — Complete wiki data scraper.

[![License](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

**v3 — Universal & Bulletproof.** Automatically discovers ALL game categories from the [GZW Fandom Wiki](https://gray-zone-warfare.fandom.com) and scrapes every page into structured JSON.

## How it works

```bash
python scrape.py --all
```

1. **Discovery** — Fetches all 165+ wiki categories, filters out wiki-internal ones (Templates, Images, etc.), keeps 120+ game categories
2. **Scrape** — Universal parser extracts infobox data from every page in every category
3. **Validate** — Data is validated before saving: checks for empty items, excessive duplicates, anomaly detection
4. **Save** — Each category becomes a `.json` file in `data/`, with backup before overwrite

If the wiki gets a new category (e.g., `Crafting`), the scraper **finds it automatically** on the next run.

## Bulletproof features

| Feature | What it does |
|---------|-------------|
| 🔄 Exponential backoff | Retries API calls with 2s, 4s, 8s delay |
| ✅ Data validation | Rejects empty or corrupt data before saving |
| 📉 Anomaly detection | Flags if item count drops >70% |
| 💾 Backup/restore | Previous data is backed up before overwrite |
| 🛡️ Per-item error handling | One bad page won't crash the whole scrape |
| ⏱️ Rate limiting | 0.5s delay between pages, handles 429s gracefully |

## Output

All `.json` files go to `data/` — each file is an array of items with `name`, `id`, and infobox fields. Pushed to [gzw-data](https://github.com/ZoniBoy00/gzw-data) every Monday via GitHub Actions.

## Automation

GitHub Actions runs `python scrape.py --all` every Monday at 06:00 UTC. Can also be triggered manually via `workflow_dispatch`.

## Requirements

- Python 3.11+
- `requests`, `beautifulsoup4`, `lxml`

```bash
pip install requests beautifulsoup4 lxml
```
