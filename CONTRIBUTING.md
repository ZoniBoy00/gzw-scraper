# Contributing to GZW Scraper

Thanks for helping maintain the Gray Zone Warfare data scraper.

## Repository scope

`gzw-scraper` discovers game categories from the GZW wiki, parses page data, validates the result, and produces the JSON consumed by [`gzw-data`](https://github.com/ZoniBoy00/gzw-data).

The scraper is not the API repository. API route changes belong in `gzw-data`; SDK changes belong in [`gzw-data-js`](https://github.com/ZoniBoy00/gzw-data-js).

## Requirements

- Python 3.11 or newer
- A virtual environment is recommended
- Network access is required only for live scraping

Install the project and test dependencies according to the project setup. For a local virtual environment:

```bash
python -m venv .venv
.venv\\Scripts\\activate
python -m pip install -e .
```

On Linux/macOS, activate with:

```bash
source .venv/bin/activate
```

## Parser changes

The scraper uses a configurable discovery and parsing flow. A parser change must not silently change unrelated datasets.

Before editing:

1. Identify the affected wiki layout or parser path.
2. Find an existing test covering the behavior.
3. Add a small fixture for the relevant HTML or parser input when practical.
4. Decide which fields are expected, optional, or intentionally preserved.

When implementing:

- Keep parsing deterministic.
- Preserve the existing item `id` and `name` rules unless the change explicitly changes the data contract.
- Do not use text similarity to invent gameplay relationships.
- Handle missing fields explicitly.
- Keep one malformed page from aborting the entire scrape.
- Do not remove old data solely because one page or category failed to load.
- Keep secrets and private credentials out of fixtures, logs, and commits.

## Fixtures and tests

Parser fixtures should be:

- Minimal and representative
- Sanitized of account information and tokens
- Named after the behavior they cover
- Stable against irrelevant whitespace changes

Run the test suite:

```bash
python -m pytest tests/ -q
```

For a parser change, add a focused test first, then run the full suite. Do not rely only on a successful live scrape.

## Running the scraper

A full scrape is a network operation and should not run in normal pull request CI:

```bash
python scrape.py --all
```

Use a custom configuration only when needed:

```bash
python scrape.py --all --config /path/to/config.toml
```

Before running against the wiki, confirm that the output directory and configuration are correct. Do not point a scrape at production data or publish output without reviewing the generated diff.

## Validation and safety checks

The scraper has several protections that must remain enabled:

- Retries and exponential backoff
- Per-item error handling
- Empty/corrupt data validation
- Drop guard for large category reductions
- Previous-data seeding in CI
- No-pruning behavior
- Metadata generation
- Snapshot recording after the data update

If a change affects validation thresholds or preservation behavior, include a test for both the safe path and the failure path.

## Data update and release flow

The normal flow is:

```text
parser change
  -> fixture tests
  -> full scraper tests
  -> reviewed generated data
  -> metadata generation
  -> gzw-data snapshot recording
  -> API deployment
```

Do not manually delete datasets to make a scrape pass. Investigate the cause first.

When reviewing a generated data change, check:

- Dataset counts
- Unexpected large drops
- New or removed fields
- Duplicate IDs
- Unexpected null-heavy output
- Parser warnings and errors
- `_metadata.json` changes
- The generated data diff

## Pull requests

A scraper pull request should include:

- The affected parser or behavior
- The source layout or reproduction details
- Fixture/test coverage
- The exact test command and result
- Whether a live scrape was run
- Whether generated data changed
- Any compatibility or preservation impact

Keep parser, workflow, and unrelated cleanup changes separate when possible.

## Commits

Use concise conventional-style subjects, for example:

```text
fix: preserve infobox fields on partial pages
feat: add parser fixture for mission rewards
chore: update scraper metadata handling
```

Use real line breaks in commit bodies. Never include credentials or literal `\\n` escape sequences in documentation or commit messages.

## Cross-repository changes

If the parser changes the shape of a dataset:

1. Update or regenerate metadata.
2. Check the `gzw-data` API schema output.
3. Check affected SDK types or optional fields.
4. Add or update contract tests where the public response changes.
5. Document the change in the pull request.
