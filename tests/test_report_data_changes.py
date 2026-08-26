import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from report_data_changes import build_report, markdown_summary  # noqa: E402


def write_json(directory: Path, filename: str, value: object) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / filename).write_text(json.dumps(value), encoding="utf-8")


def test_build_report_detects_item_and_field_changes(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    write_json(before, "weapons.json", [
        {"name": "AK-74", "id": "ak-74", "caliber": "5.45"},
        {"name": "M4", "id": "m4"},
    ])
    write_json(after, "weapons.json", [
        {"name": "AK-74", "id": "ak-74", "caliber": "5.56"},
        {"name": "AK-12", "id": "ak-12"},
    ])

    report = build_report(before, after)

    assert report["files"]["changed"] == 1
    assert report["totals"]["items_added"] == 1
    assert report["totals"]["items_removed"] == 1
    assert report["totals"]["items_changed"] == 1
    assert report["totals"]["changed_fields"] == {"caliber": 1}
    assert "weapons.json" in markdown_summary(report)


def test_build_report_ignores_scrape_metadata_changes(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    write_json(before, "weapons.json", [{"name": "AK-74", "id": "ak-74"}])
    write_json(after, "weapons.json", [{"name": "AK-74", "id": "ak-74"}])
    write_json(before, "_metadata.json", {"lastScrapedAt": "2026-08-25T06:00:00Z"})
    write_json(after, "_metadata.json", {"lastScrapedAt": "2026-08-26T06:00:00Z"})
    write_json(before, "_history.json", [{"version": "old"}])
    write_json(after, "_history.json", [{"version": "new"}])

    report = build_report(before, after)

    assert report["files"] == {"before": 1, "after": 1, "added": 0, "removed": 0, "changed": 0, "unchanged": 1}
    assert report["excluded_files"] == ["_history.json", "_metadata.json"]
    assert report["totals"]["items_before"] == 1
    assert report["totals"]["items_after"] == 1


def test_build_report_detects_new_and_removed_datasets(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    write_json(before, "old.json", [{"name": "Old", "id": "old"}])
    write_json(after, "new.json", [{"name": "New", "id": "new"}])

    report = build_report(before, after)

    assert report["files"]["added"] == 1
    assert report["files"]["removed"] == 1
    statuses = {item["file"]: item["status"] for item in report["datasets"]}
    assert statuses == {"new.json": "added", "old.json": "removed"}


def test_build_report_counts_log_signals(tmp_path):
    before = tmp_path / "before"
    after = tmp_path / "after"
    log = tmp_path / "scrape.log"
    write_json(before, "items.json", [])
    write_json(after, "items.json", [])
    log.write_text("WARNING rate limit 429\nERROR failed\n", encoding="utf-8")

    report = build_report(before, after, log)

    assert report["log"] == {"errors": 1, "warnings": 1, "rate_limits": 2}
