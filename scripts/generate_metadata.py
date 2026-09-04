"""Generate deterministic metadata for scraped JSON datasets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

SCRAPER_VERSION = "4.1.0"
PARSER_REVISION = "universal-parser-v4"


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
) -> dict[str, Any]:
    """Generate metadata for all dataset JSON files in ``data_dir``."""
    datasets = [
        describe_dataset(path)
        for path in sorted(data_dir.glob("*.json"))
        if not path.name.startswith("_")
    ]
    metadata: dict[str, Any] = {
        "source": "gzw-scraper",
        "scraperVersion": scraper_version,
        "parserRevision": parser_revision,
        "datasetCount": len(datasets),
        "datasets": datasets,
    }
    if last_scraped_at is not None:
        metadata["lastScrapedAt"] = last_scraped_at
    return metadata


def write_metadata(
    data_dir: Path,
    last_scraped_at: str | None = None,
    scraper_version: str = SCRAPER_VERSION,
    parser_revision: str = PARSER_REVISION,
) -> Path:
    """Write metadata atomically and return its target path."""
    data_dir.mkdir(parents=True, exist_ok=True)
    target = data_dir / "_metadata.json"
    temporary = target.with_suffix(".tmp")
    payload = generate_metadata(data_dir, last_scraped_at, scraper_version, parser_revision)
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
