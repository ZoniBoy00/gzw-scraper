"""GZW wiki game-category scraper.

Small, maintainable package extracted from the former single-file
``scrape.py``. Module layout:

- ``config``   — TOML configuration, defaults, deep-merge, settings distribution
- ``network``  — MediaWiki API access: throttling, retries, page fetching, category discovery
- ``parsing``  — infobox/listing-table/ballistics HTML parsing and value sanitising
- ``storage``  — validation, safe_save (backup, drop-guard, field preservation), merging
- ``pipeline`` — scraping orchestration and the full/single-category runs
- ``cli``      — command-line entry point
"""

from gzw_scraper import cli, config, network, parsing, pipeline, storage

# Load config.toml (repo root) once at import time and distribute the values
# to the modules that consume them (network/pipeline/storage). This mirrors
# the old behaviour where importing ``scrape`` fully configured the scraper.
# ``gzw_scraper.cli.main`` re-applies settings when ``--config`` points at a
# custom file, and restores the defaults afterwards.
config.apply_settings()

__all__ = ["cli", "config", "network", "parsing", "pipeline", "storage"]
__version__ = "4.3.0"
