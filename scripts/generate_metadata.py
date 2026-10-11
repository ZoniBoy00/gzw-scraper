"""Generate deterministic metadata for scraped JSON datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCRAPER_VERSION = "4.5.0"
PARSER_REVISION = "universal-parser-v7"


def value_type(value: Any) -> str:
    """Return the stable metadata type name for a JSON value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, (int, float)):
        return "number"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return "string"


def example_sort_key(value: Any) -> str:
    """Create a stable ordering key for JSON-compatible example values."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def describe_dataset(path: Path) -> dict[str, Any]:
    """Describe one JSON array dataset."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    items = payload if isinstance(payload, list) else []
    item_count = len(items)
    field_values: dict[str, list[Any]] = {}

    for item in items:
        if not isinstance(item, dict):
            continue
        for field, value in item.items():
            field_values.setdefault(field, []).append(value)

    fields: dict[str, Any] = {}
    for field in sorted(field_values):
        values = field_values[field]
        non_null = [value for value in values if value is not None]
        examples = sorted(non_null, key=example_sort_key)
        fields[field] = {
            "types": sorted({value_type(value) for value in values}),
            "presentCount": len(values),
            "optional": len(values) < item_count,
            "nullable": any(value is None for value in values),
            "example": examples[0] if examples else None,
        }

    return {
        "name": path.stem,
        "file": path.name,
        "itemCount": item_count,
        "fields": fields,
    }


def generate_metadata(
    data_dir: Path,
    last_scraped_at: str | None = None,
    scraper_version: str = SCRAPER_VERSION,
    parser_revision: str = PARSER_REVISION,
    *,
    updated_datasets: set[str] | None = None,
    source_revisions: dict[str, dict[str, int]] | None = None,
    previous_metadata: dict[str, Any] | None = None,
    full_scrape: bool = False,
) -> dict[str, Any]:
    """Generate metadata for all dataset JSON files in ``data_dir``."""
    datasets = [
        describe_dataset(path)
        for path in sorted(data_dir.glob("*.json"))
        if not path.name.startswith("_")
    ]
    previous_metadata = previous_metadata or {}
    previous_datasets = {
        dataset.get("name"): dataset
        for dataset in previous_metadata.get("datasets", [])
        if isinstance(dataset, dict) and isinstance(dataset.get("name"), str)
    }
    updated_names = updated_datasets or set()
    source_revisions = source_revisions or {}

    for dataset in datasets:
        name = dataset["name"]
        previous = previous_datasets.get(name, {})
        dataset_scraped_at = last_scraped_at if name in updated_names else previous.get("lastScrapedAt")
        if isinstance(dataset_scraped_at, str):
            dataset["lastScrapedAt"] = dataset_scraped_at

        sources: dict[str, int] = {}
        for source in previous.get("sourcePages", []):
            if isinstance(source, dict) and isinstance(source.get("title"), str) and isinstance(source.get("revisionId"), int):
                sources[source["title"]] = source["revisionId"]
        if name in updated_names:
            sources.update(source_revisions.get(name, {}))
        if sources:
            dataset["sourcePages"] = [
                {"title": title, "revisionId": revision_id}
                for title, revision_id in sorted(sources.items())
            ]

    metadata: dict[str, Any] = {
        "source": "gzw-scraper",
        "scraperVersion": scraper_version,
        "parserRevision": parser_revision,
        "datasetCount": len(datasets),
        "datasets": datasets,
    }
    metadata_last_scraped_at = last_scraped_at or previous_metadata.get("lastScrapedAt")
    if isinstance(metadata_last_scraped_at, str):
        metadata["lastScrapedAt"] = metadata_last_scraped_at
    last_full_scrape = last_scraped_at if full_scrape else previous_metadata.get("lastFullScrapeAt")
    if isinstance(last_full_scrape, str):
        metadata["lastFullScrapeAt"] = last_full_scrape
    return metadata


def write_metadata(
    data_dir: Path,
    last_scraped_at: str | None = None,
    scraper_version: str = SCRAPER_VERSION,
    parser_revision: str = PARSER_REVISION,
    *,
    updated_datasets: set[str] | None = None,
    source_revisions: dict[str, dict[str, int]] | None = None,
    full_scrape: bool = False,
) -> Path:
    """Write metadata atomically and return its target path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "_metadata.json"
    temporary = target.with_suffix(".tmp")
    previous_metadata = None
    if target.exists():
        try:
            previous_metadata = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            previous_metadata = None
    payload = generate_metadata(
        data_dir,
        last_scraped_at,
        scraper_version,
        parser_revision,
        updated_datasets=updated_datasets,
        source_revisions=source_revisions,
        previous_metadata=previous_metadata,
        full_scrape=full_scrape,
    )
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    temporary.replace(target)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate GZW dataset metadata")
    parser.add_argument("data_dir", type=Path, nargs="?", default=Path("data"))
    args = parser.parse_args()
    path = write_metadata(args.data_dir)
    print(f"Generated {path}")


if __name__ == "__main__":
    main()
