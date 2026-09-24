"""Command-line entry point for the GZW wiki scraper."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import List, Optional, Sequence

from gzw_scraper import config, pipeline
from gzw_scraper.config import CONFIG_PATH


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser (kept separate from main for testability)."""
    parser = argparse.ArgumentParser(
        prog="scrape.py",
        description="GZW Wiki Scraper v4.3.0",
    )
    parser.add_argument("--category", help="Scrape a single category by name")
    parser.add_argument("--all", action="store_true", help="Run full scrape (all categories)")
    parser.add_argument("--config", default=str(CONFIG_PATH), help="Path to config.toml")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Override the >70%% item-drop guard and write data even when a "
             "category collapsed (only use if the wiki really lost content)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> None:
    """Parse CLI arguments and dispatch to the pipeline."""
    parser = build_parser()
    args = parser.parse_args(argv)

    pipeline.FORCE_SAVE = args.force

    config_path = Path(args.config)
    if config_path != CONFIG_PATH:
        # Reload config from custom path and rebind module settings
        config.apply_settings(config_path)

    try:
        if args.category:
            pipeline.run_single_category(args.category)
        elif args.all:
            pipeline.run_full_scrape()
        else:
            pipeline.run_full_scrape()
    finally:
        if config_path != CONFIG_PATH:
            # Restore default settings so repeated in-process runs are independent
            config.apply_settings()


if __name__ == "__main__":
    main()
