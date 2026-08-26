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
