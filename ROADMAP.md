# GZW Scraper Roadmap

This roadmap covers the `gzw-scraper` repository: wiki discovery, parsing, validation, generated JSON, metadata, and the data handoff to `gzw-data`.

## Current status

- **Version:** `4.0.0`
- **Runtime:** Python 3.11+
- **Source:** GZW Fandom Wiki
- **Tests:** 28 passing on 2026-08-27
- **Output:** generated JSON datasets plus `_metadata.json`
- **License:** MIT

## Completed

- [x] Discover wiki categories automatically.
- [x] Parse category pages into structured JSON datasets.
- [x] Use retries, exponential backoff, request pacing, and configurable workers.
- [x] Validate empty/corrupt data before saving.
- [x] Keep per-item parser failures from aborting the full run.
- [x] Add the >70% category drop guard.
- [x] Seed previous data in CI and disable silent dataset pruning.
- [x] Generate deterministic dataset metadata.
- [x] Exclude `_metadata.json` and `_history.json` from normal data-diff calculations.
- [x] Record a new `gzw-data` snapshot after the scraper data handoff.
- [x] Add a machine-readable scrape report.
- [x] Add a separate `CONTRIBUTING.md` with parser and release guidance.

## Next priorities

### 1. Schema-aware scraping

- [ ] Compare the new result against the previous schema.
- [ ] Detect added fields, removed fields, and field-type changes.
- [ ] Make field preservation aware of schema coverage and parser confidence.
- [ ] Prevent a partial scrape from silently preserving stale fields indefinitely.
- [ ] Publish schema warnings in the scrape report.

### 2. Parser reliability

- [ ] Add sanitized HTML fixtures for representative wiki layouts.
- [ ] Add regression tests for infobox changes, missing fields, duplicate IDs, and malformed pages.
- [ ] Add anomaly detection for mass name changes, mass ID changes, null-heavy output, and important-field loss.
- [ ] Keep thresholds configurable and test both safe and rejected paths.

### 3. Run provenance

- [ ] Add a scraper-run manifest with start/end time, dataset count, item count, warnings, errors, and scraper version.
- [ ] Add scraper version and parser revision to `_metadata.json`.
- [ ] Add dataset-level checksums when they reduce review cost.
- [ ] Define which metadata is stable enough for API consumers.

### 4. CI and publishing

- [ ] Add a lightweight push/pull-request CI workflow that does not run a live scrape.
- [ ] Run parser fixtures, metadata generation, and validation in pull-request CI.
- [ ] Keep the scheduled live scrape separate from normal code CI.
- [ ] Add a clear reviewed-data handoff to `gzw-data`.
- [ ] Remove any tracked `__pycache__` files and keep the repository clean.

## Live scrape checklist

Before a live scrape:

1. Confirm the config and output directory.
2. Confirm the wiki source is reachable.
3. Run parser and validation tests.
4. Run the scrape with the drop guard enabled.
5. Review dataset count and field changes.
6. Review warnings and errors.
7. Review `_metadata.json`.
8. Record the `gzw-data` snapshot only after the data handoff is reviewed.

Never delete datasets manually to make a scrape pass.

## Definition of done for parser changes

- A focused fixture or regression test exists.
- The full test suite passes with `python -m pytest tests/ -q`.
- The generated data diff has been reviewed.
- Drop-guard and no-pruning behavior remain intact.
- Metadata output is regenerated when the data shape changes.
- The pull request states whether a live scrape was run.

## Out of scope without verified source data

Do not add inferred weapon compatibility, task requirements, or map relationships. Text matches may be reported as review hints, but not published as confirmed game data.
