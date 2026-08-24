#!/usr/bin/env python3
"""Compare two scraper data directories and emit a machine-readable report."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


Item = dict[str, Any]


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def item_key(item: Item) -> str:
    value = item.get("id") or item.get("name")
    return str(value) if value is not None else json.dumps(item, sort_keys=True)


def compare_items(before: Any, after: Any) -> dict[str, Any]:
    if not isinstance(before, list) or not isinstance(after, list):
        return {
            "before_count": 0 if before is None else 1,
            "after_count": 0 if after is None else 1,
            "items_added": 0,
            "items_removed": 0,
            "items_changed": 1 if before != after else 0,
            "changed_fields": {},
            "added_names": [],
            "removed_names": [],
        }

    old = {item_key(item): item for item in before if isinstance(item, dict)}
    new = {item_key(item): item for item in after if isinstance(item, dict)}
    added = sorted(set(new) - set(old))
    removed = sorted(set(old) - set(new))
    changed_fields: Counter[str] = Counter()
    changed = 0

    for key in sorted(set(old) & set(new)):
        old_item = old[key]
        new_item = new[key]
        fields = set(old_item) | set(new_item)
        fields.discard("id")
        fields.discard("name")
        changed_keys = [field for field in fields if old_item.get(field) != new_item.get(field)]
        if changed_keys:
            changed += 1
            changed_fields.update(changed_keys)

    return {
        "before_count": len(before),
        "after_count": len(after),
        "items_added": len(added),
        "items_removed": len(removed),
        "items_changed": changed,
        "changed_fields": dict(sorted(changed_fields.items())),
        "added_names": added[:25],
        "removed_names": removed[:25],
    }


def build_report(before_dir: Path, after_dir: Path, log_path: Path | None = None) -> dict[str, Any]:
    before_files = {path.name for path in before_dir.glob("*.json")}
    after_files = {path.name for path in after_dir.glob("*.json")}
    datasets: list[dict[str, Any]] = []

    for filename in sorted(before_files | after_files):
        before = load_json(before_dir / filename) if filename in before_files else None
        after = load_json(after_dir / filename) if filename in after_files else None
        diff = compare_items(before, after)
        if filename not in before_files:
            status = "added"
        elif filename not in after_files:
            status = "removed"
        elif before == after:
            status = "unchanged"
        else:
            status = "changed"
        datasets.append({"file": filename, "status": status, **diff})

    log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path and log_path.exists() else ""
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "files": {
            "before": len(before_files),
            "after": len(after_files),
            "added": sum(item["status"] == "added" for item in datasets),
            "removed": sum(item["status"] == "removed" for item in datasets),
            "changed": sum(item["status"] == "changed" for item in datasets),
            "unchanged": sum(item["status"] == "unchanged" for item in datasets),
        },
        "datasets": datasets,
        "totals": {
            "items_before": sum(item["before_count"] for item in datasets),
            "items_after": sum(item["after_count"] for item in datasets),
            "items_added": sum(item["items_added"] for item in datasets),
            "items_removed": sum(item["items_removed"] for item in datasets),
            "items_changed": sum(item["items_changed"] for item in datasets),
            "changed_fields": dict(sorted(Counter(
                field
                for item in datasets
                for field, count in item["changed_fields"].items()
                for _ in range(count)
            ).items())),
        },
        "log": {
            "errors": len(re.findall(r"\bERROR\b|❌", log_text)),
            "warnings": len(re.findall(r"\bWARNING\b|⚠️", log_text)),
            "rate_limits": len(re.findall(r"429|rate.?limit", log_text, re.IGNORECASE)),
        },
    }
    return report


def markdown_summary(report: dict[str, Any]) -> str:
    files = report["files"]
    totals = report["totals"]
    log = report["log"]
    lines = [
        "## Scraper data report",
        f"- Datasets: {files['after']} after scrape ({files['changed']} changed, {files['added']} added, {files['removed']} removed, {files['unchanged']} unchanged)",
        f"- Items: {totals['items_after']} after scrape ({totals['items_added']} added, {totals['items_removed']} removed, {totals['items_changed']} changed)",
        f"- Log: {log['errors']} errors, {log['warnings']} warnings, {log['rate_limits']} rate-limit matches",
    ]
    changed = [item for item in report["datasets"] if item["status"] != "unchanged"]
    if changed:
        lines.append("")
        lines.append("### Dataset changes")
        lines.extend(
            f"- `{item['file']}`: {item['status']}; {item['before_count']} → {item['after_count']} items; +{item['items_added']} / -{item['items_removed']} / ~{item['items_changed']}"
            for item in changed
        )
    else:
        lines.append("- **No captured data changes.** The scraped JSON matches the previous dataset.")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--log", type=Path)
    parser.add_argument("--json-out", type=Path, required=True)
    args = parser.parse_args()

    report = build_report(args.before, args.after, args.log)
    args.json_out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(markdown_summary(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
