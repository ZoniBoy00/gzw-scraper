import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "scripts"))

from generate_metadata import generate_metadata  # noqa: E402


def test_generate_metadata_describes_fields_and_optional_values(tmp_path):
    (tmp_path / "weapons.json").write_text(json.dumps([
        {"id": "ak-12", "name": "AK-12", "weight": "3.553 kg", "optic": None},
        {"id": "m4", "name": "M4", "weight": "3.4 kg"},
    ]), encoding="utf-8")

    metadata = generate_metadata(tmp_path, last_scraped_at="2026-08-26T12:00:00Z")

    assert metadata["source"] == "gzw-scraper"
    assert metadata["scraperVersion"] == "4.5.1"
    assert metadata["parserRevision"] == "universal-parser-v8"
    assert metadata["datasetCount"] == 1
    assert metadata["lastScrapedAt"] == "2026-08-26T12:00:00Z"
    fields = metadata["datasets"][0]["fields"]
    assert fields["id"] == {
        "types": ["string"],
        "presentCount": 2,
        "optional": False,
        "nullable": False,
        "example": "ak-12",
    }
    assert fields["optic"]["optional"] is True
    assert fields["optic"]["nullable"] is True
    assert fields["weight"]["example"] == "3.4 kg"


def test_generate_metadata_is_sorted_and_ignores_metadata_file(tmp_path):
    (tmp_path / "zeta.json").write_text(json.dumps([{"id": "z"}]), encoding="utf-8")
    (tmp_path / "alpha.json").write_text(json.dumps([{"id": "a"}]), encoding="utf-8")
    (tmp_path / "_metadata.json").write_text("{}", encoding="utf-8")
    (tmp_path / "_history.json").write_text("[]", encoding="utf-8")

    metadata = generate_metadata(tmp_path)

    assert metadata["datasetCount"] == 2
    assert [dataset["name"] for dataset in metadata["datasets"]] == ["alpha", "zeta"]


def test_generate_metadata_examples_are_deterministic(tmp_path):
    first = [{"id": "b", "value": "z"}, {"id": "a", "value": "a"}]
    second = list(reversed(first))
    (tmp_path / "first.json").write_text(json.dumps(first), encoding="utf-8")
    first_metadata = generate_metadata(tmp_path)
    (tmp_path / "first.json").write_text(json.dumps(second), encoding="utf-8")
    second_metadata = generate_metadata(tmp_path)

    assert first_metadata == second_metadata


def test_generate_metadata_tracks_per_dataset_scrape_time_and_source_revision(tmp_path):
    (tmp_path / "ammo.json").write_text(json.dumps([{"id": "m855", "name": "M855"}]), encoding="utf-8")
    (tmp_path / "vests.json").write_text(json.dumps([{"id": "vest", "name": "Vest"}]), encoding="utf-8")
    previous = {
        "lastScrapedAt": "2026-10-05T10:00:00Z",
        "lastFullScrapeAt": "2026-10-05T10:00:00Z",
        "datasets": [
            {"name": "ammo", "lastScrapedAt": "2026-10-05T10:00:00Z", "sourcePages": [{"title": "Ammo page", "revisionId": 10}]},
            {"name": "vests", "lastScrapedAt": "2026-10-05T10:00:00Z", "sourcePages": [{"title": "Old vest page", "revisionId": 20}]},
        ],
    }

    metadata = generate_metadata(
        tmp_path,
        last_scraped_at="2026-10-11T12:00:00Z",
        updated_datasets={"vests"},
        source_revisions={"vests": {"Armor Vests": 321}},
        previous_metadata=previous,
    )

    datasets = {dataset["name"]: dataset for dataset in metadata["datasets"]}
    assert metadata["lastScrapedAt"] == "2026-10-11T12:00:00Z"
    assert metadata["lastFullScrapeAt"] == "2026-10-05T10:00:00Z"
    assert datasets["ammo"]["lastScrapedAt"] == "2026-10-05T10:00:00Z"
    assert datasets["ammo"]["sourcePages"] == [{"title": "Ammo page", "revisionId": 10}]
    assert datasets["vests"]["lastScrapedAt"] == "2026-10-11T12:00:00Z"
    assert datasets["vests"]["sourcePages"] == [
        {"title": "Armor Vests", "revisionId": 321},
        {"title": "Old vest page", "revisionId": 20},
    ]


def test_generate_metadata_manual_regeneration_preserves_run_timestamps(tmp_path):
    (tmp_path / "vests.json").write_text(json.dumps([{"id": "vest", "name": "Vest"}]), encoding="utf-8")
    previous = {
        "lastScrapedAt": "2026-10-11T10:00:00Z",
        "lastFullScrapeAt": "2026-10-05T10:00:00Z",
        "datasets": [{"name": "vests", "lastScrapedAt": "2026-10-11T10:00:00Z"}],
    }

    metadata = generate_metadata(tmp_path, previous_metadata=previous)

    assert metadata["lastScrapedAt"] == "2026-10-11T10:00:00Z"
    assert metadata["lastFullScrapeAt"] == "2026-10-05T10:00:00Z"
    assert metadata["datasets"][0]["lastScrapedAt"] == "2026-10-11T10:00:00Z"


def test_generate_metadata_merges_source_revisions_and_marks_full_scrape_time(tmp_path):
    (tmp_path / "vests.json").write_text(json.dumps([{"id": "vest", "name": "Vest"}]), encoding="utf-8")
    previous = {
        "datasets": [{"name": "vests", "sourcePages": [{"title": "Old page", "revisionId": 10}]}],
    }

    metadata = generate_metadata(
        tmp_path,
        last_scraped_at="2026-10-11T12:00:00Z",
        updated_datasets={"vests"},
        source_revisions={"vests": {"New page": 20}},
        previous_metadata=previous,
        full_scrape=True,
    )

    vest = metadata["datasets"][0]
    assert metadata["lastFullScrapeAt"] == "2026-10-11T12:00:00Z"
    assert vest["sourcePages"] == [
        {"title": "New page", "revisionId": 20},
        {"title": "Old page", "revisionId": 10},
    ]
