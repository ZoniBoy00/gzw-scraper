# GZW Scraper

**Gray Zone Warfare** — Wiki game-category data scraper.

[![Python](https://img.shields.io/badge/python-3.11%2B-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![Donate](https://img.shields.io/badge/donate-Buy%20me%20a%20coffee-f0b429?logo=buymeacoffee)](https://buymeacoffee.com/zoniboy00)

**v4.6.1 — rate-limit handling and single-source versioning.** HTTP 429 responses now honour `Retry-After`, the final failed attempt no longer waits needlessly, and the scraper version lives in one file (`gzw_scraper/_version.py`). No change to the generated data format.

**v4.6.0 — numeric projections and reviewed data handoff.** Adds optional unit-bearing `numeric_values` without changing source fields, and routes scraper updates to `gzw-data` through a reviewable pull request. Failed or anomalous runs now produce a report artifact and attention alert for follow-up.

**v4.5.1 — Complete carrier-list enrichment.** In addition to the v4.5.0 wiki coverage, plate-carrier listing rows now provide grid, weight, and source fields for blank item pages. New and filtered-out wiki categories are reported for review.

## Output metadata

After a successful full scrape, the scraper writes `data/_metadata.json`. It contains the UTC `lastScrapedAt` timestamp, `scraperVersion`, `parserRevision`, plus deterministic metadata for every dataset: dataset name and file, item count, observed fields, detected JSON types, present-field counts, optional/nullable flags, and a stable example value. The gzw-data deployment copies this metadata file so the API and dashboard can show the actual data update time and inspect the generated dataset shape separately from the API request time.

### Stable output fields

`gzw-data` and its clients read the files below. These fields are stable: they are only added to, and a removal or rename is a breaking change that needs a major version (`5.0.0`) and coordinated API/SDK updates.

`_metadata.json`:

- `source` (always `gzw-scraper`), `scraperVersion`, `parserRevision`, `datasetCount`
- `lastScrapedAt` (latest save) and `lastFullScrapeAt` (latest completed full scrape, used for freshness monitoring)
- `datasets[]` entries: `name`, `file`, `itemCount`, `fields` (per field: `types`, `presentCount`, `optional`, `nullable`, `example`), and when known `lastScrapedAt` and `sourcePages[]` (`title`, `revisionId`)

`_manifest.json`:

- `manifestVersion`, `generatedAt`, `source`
- `datasets.<name>`: `file`, `records`, `sha256`

`parserRevision` and the `example` values are informational and may change on any release. Do not parse `_field_preservation.json`; it is internal scraper state and is not published.

Generate metadata manually for an existing data directory with:

```bash
python scripts/generate_metadata.py data
```

## How it works

```bash
python scrape.py --all
```

1. **Discovery** — Fetches wiki categories automatically and filters out wiki-internal ones (Templates, Images, etc.)
2. **Scrape** — Universal parser extracts infobox data from supported categories (parallel, up to 3 workers by default)
3. **Validate** — Data is validated before saving: checks for empty items, excessive duplicates, anomaly detection
4. **Save** — Each category becomes a `.json` file in `data/`, with backup before overwrite

`Removed Content` and `Upcoming Content` are real game-data categories, not wiki infrastructure; both remain enabled and map to `removed_content.json` and `upcoming_content.json`.

The `Armor Vests` and `Plate Carriers` listing pages enrich matching items with `protection_rating` (for example `P3` or `R1`) while retaining the legacy `nij_class` field. Listing aliases resolve to their canonical item pages to avoid duplicate records; table-only entries are added to the corresponding dataset.

The pipeline covers eligible wiki categories, selected standalone game pages, and configured listing pages. Regions, Factions, Newspaper transcripts, and standalone bosses use configured section extraction; item and task pages retain their infobox fields.

If the wiki gets a new game category (e.g., `Crafting`), the scraper **finds it automatically** on the next run.

Two cases are not automatic. Categories that match the wiki-infrastructure filter, and standalone articles that sit in no scraped category, are skipped; every run logs `Category not scraped: ...` and `Uncovered page: ...` lines for them, and you add the page under `[supplemental_pages]` in `config.toml`. Articles without a fixed layout (Hideout, Experience, ...) can be captured whole by mapping a field to the section name `"*"` under `[content_sections.<dataset>]`.

## Code layout

The implementation is split into the `gzw_scraper/` package by responsibility. The root `scrape.py` remains as a compatibility launcher, so existing commands such as `python scrape.py --all` continue to work.

- `config.py` — TOML loading, defaults, and runtime settings
- `network.py` — MediaWiki requests, throttling, retries, and category discovery
- `parsing.py` — infobox, article-section, listing-table, and Ballistics parsing
- `storage.py` — validation, backups, drop guard, field preservation, and JSON writes
- `pipeline.py` — category scraping and full-run orchestration
- `cli.py` — command-line argument parsing and dispatch

## Bulletproof features

| Feature | What it does |
|---------|-------------|
| 🔄 Exponential backoff | Retries API calls with 1s, 2s, 4s, 8s delays; HTTP 429 honours `Retry-After` (capped at 2 minutes) or backs off 10s, 20s, 40s... |
| ✅ Data validation | Rejects empty or corrupt data before saving |
| 📉 Drop-guard | A category that drops >70% is NOT saved (rate limit / wiki hiccup can't wipe data) — `--force` overrides |
| 💾 Previous-data seeding | CI seeds the previous gzw-data before scraping; missing datasets keep their last good version |
| 🧬 Schema-aware preservation | Common fields survive one partial parser run; repeated omissions are dropped instead of becoming permanent stale data |
| 🚫 No pruning | Stale-file deletion removed — data never silently disappears from gzw-data |
| 🛡️ Per-item error handling | One bad page won't crash the whole scrape |
| ⏱️ Rate limiting | 0.8s per-page delay plus a 0.5s global request throttle; handles 429s gracefully |
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
user_agent = "GZW-Scraper/{version} (community tool; github.com/ZoniBoy00/gzw-scraper; scraper@gzw-data.dev)"

[scraper]
max_retries = 5
base_delay = 1.0
page_delay = 0.8
request_interval = 0.5
max_workers = 3        # parallel category scraping
max_safe_deviation = 0.7
```

Run with a custom config:
```bash
python scrape.py --all --config /path/to/config.toml
```

## Output

All `.json` files go to `data/` — each public dataset array includes `name`, `id`, and infobox fields. The weekly GitHub Actions workflow validates these files and proposes updates to [gzw-data](https://github.com/ZoniBoy00/gzw-data) in a reviewable pull request; it never pushes scraped output directly to `main`.

## Automation

GitHub Actions runs `python scrape.py --all` every Monday at 06:17 UTC. It can also be triggered manually via `workflow_dispatch`.

Each run publishes a `scrape-report` artifact containing `scrape-report.json` and the raw `scrape.log`. Before a pull request is opened, the workflow validates that the manifest and scrape report are present and structurally valid, then runs the `gzw-data` API tests and dataset metadata validation. A `GH_PAT` Actions secret with write access to the `gzw-data` repository is required for the cross-repository branch and pull request. The GitHub Actions summary shows:

- dataset files added, removed, changed, and unchanged
- item counts before and after the scrape
- items added, removed, and changed
- changed fields by name
- scraper errors, warnings, and rate-limit matches
- schema warnings for added, removed, or type-changed fields
- anomaly warnings for empty or massively reduced datasets

`_metadata.json`, `_history.json`, `_id_aliases.json`, and `_manifest.json` are intentionally excluded from the content diff. Metadata timestamps and manifest generation times change on successful runs, but these files are still published to `data/` for API provenance, alias resolution, and snapshot consumers. The scraper's internal `_field_preservation.json` state is not copied into the API repository.

Scraped source fields stay unchanged. When a value is an unambiguous number with a recognized unit, the scraper adds a `numeric_values` projection that stores the numeric value and unit separately; ambiguous comma-decimal forms and unknown units are left untouched. The datasets' rights are distinct from the scraper code license; see the [gzw-data source and licensing notice](https://github.com/ZoniBoy00/gzw-data/blob/main/DATA_LICENSE.md).

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
python -m pip install pytest ruff

# Lint and run tests
ruff check .
python -m pytest tests/ -v
```

## Related projects

- [gzw-data](https://github.com/ZoniBoy00/gzw-data): the public API and datasets ([gzw-data.dev](https://gzw-data.dev/))
- [gzw-data-js](https://github.com/ZoniBoy00/gzw-data-js): JavaScript / TypeScript client ([npm](https://www.npmjs.com/package/@zoniboy/gzw-data-client))
- [gzw-data-py](https://github.com/ZoniBoy00/gzw-data-py): Python client ([PyPI](https://pypi.org/project/gzw-data/))
- [gzw-tools](https://github.com/ZoniBoy00/gzw-tools): the fan reference app ([gzw-tools.vercel.app](https://gzw-tools.vercel.app/))

## License

Scraper code is released under the [MIT License](LICENSE). The scraped datasets have separate rights; see the [gzw-data source and licensing notice](https://github.com/ZoniBoy00/gzw-data/blob/main/DATA_LICENSE.md).
