# GZW Scraper

**Gray Zone Warfare** — Complete wiki data scraper.

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Donate](https://img.shields.io/badge/donate-Buy%20me%20a%20coffee-f0b429?logo=buymeacoffee)](https://buymeacoffee.com/zoniboy00)

**v4 — Configurable & Bulletproof.** Automatically discovers ALL game categories from the [GZW Fandom Wiki](https://gray-zone-warfare.fandom.com) and scrapes every page into structured JSON.

## Output metadata

After a successful full scrape, the scraper writes `data/_metadata.json`. It contains the UTC `lastScrapedAt` timestamp plus deterministic metadata for every dataset: dataset name and file, item count, observed fields, detected JSON types, present-field counts, optional/nullable flags, and a stable example value. The gzw-data deployment copies this metadata file so the API and dashboard can show the actual data update time and inspect the generated dataset shape separately from the API request time.

Generate metadata manually for an existing data directory with:

```bash
python scripts/generate_metadata.py data
```

## How it works

```bash
python scrape.py --all
```

1. **Discovery** — Fetches all 165+ wiki categories, filters out wiki-internal ones (Templates, Images, etc.), keeps 120+ game categories
2. **Scrape** — Universal parser extracts infobox data from every page in every category (parallel, up to 4 workers)
3. **Validate** — Data is validated before saving: checks for empty items, excessive duplicates, anomaly detection
4. **Save** — Each category becomes a `.json` file in `data/`, with backup before overwrite

If the wiki gets a new category (e.g., `Crafting`), the scraper **finds it automatically** on the next run.

## Bulletproof features

| Feature | What it does |
|---------|-------------|
| 🔄 Exponential backoff | Retries API calls with 2s, 4s, 8s delay |
| ✅ Data validation | Rejects empty or corrupt data before saving |
| 📉 Drop-guard | A category that drops >70% is NOT saved (rate limit / wiki hiccup can't wipe data) — `--force` overrides |
| 💾 Previous-data seeding | CI seeds the previous gzw-data before scraping; missing datasets keep their last good version |
| 🚫 No pruning | Stale-file deletion removed — data never silently disappears from gzw-data |
| 🛡️ Per-item error handling | One bad page won't crash the whole scrape |
| ⏱️ Rate limiting | 0.5s delay between pages, handles 429s gracefully |
| ⚡ Parallel scraping | Scrapes multiple categories at once (configurable workers) |
| 📊 Progress bar | Real-time progress with tqdm |
| 🔧 Config-driven | All settings in `config.toml`, no hardcoded values |
| 🏷️ Type hints | Full type annotations for better IDE support |

> **Why the drop-guard?** On 2026-08-10 the wiki rate-limited the scraper and
> 27 datasets (weapons, keys, tasks, …) collapsed. The old workflow pruned the
> "missing" files and gzw-data lost 11k+ lines. Now a >70% drop aborts the
> save instead of overwriting, and the workflow seeds the previous data so a
> failed scrape keeps the last good version.

## Configuration

All settings are in `config.toml`:

```toml
[wiki]
api_url = "https://gray-zone-warfare.fandom.com/api.php"
user_agent = "GZW-Tools/4.0 (community tool; github.com/ZoniBoy00/gzw-tools)"

[scraper]
max_retries = 3
page_delay = 0.5
max_workers = 4        # parallel category scraping
max_safe_deviation = 0.7
```

Run with a custom config:
```bash
python scrape.py --all --config /path/to/config.toml
```

## Output

All `.json` files go to `data/` — each file is an array of items with `name`, `id`, and infobox fields. Pushed to [gzw-data](https://github.com/ZoniBoy00/gzw-data) every Monday via GitHub Actions.

## Automation

GitHub Actions runs `python scrape.py --all` every Monday at 06:00 UTC. It can also be triggered manually via `workflow_dispatch`.

Each run now publishes a `scrape-report` artifact containing `scrape-report.json` and the raw `scrape.log`. The GitHub Actions summary shows:

- dataset files added, removed, changed, and unchanged
- item counts before and after the scrape
- items added, removed, and changed
- changed fields by name
- scraper errors, warnings, and rate-limit matches

`_metadata.json` is intentionally excluded from the content diff. Its scrape timestamp changes on every successful run, but the file is still published to `data/` for API metadata consumers.

If the report says **No captured data changes**, the scraper completed and produced JSON matching the previous `gzw-data` snapshot. This does not claim that every wiki edit was unchanged; only the fields captured by this scraper were unchanged.

## Requirements and installation

- Python 3.11+
- `requests>=2.31`
- `beautifulsoup4>=4.12`
- `lxml>=5.0`
- `tqdm>=4.66`

The dependencies are defined in `pyproject.toml`. Install the scraper locally with:

```bash
python -m pip install -e .
```

## Development

```bash
# Install the test dependency
python -m pip install pytest

# Run tests
python -m pytest tests/ -v
```
