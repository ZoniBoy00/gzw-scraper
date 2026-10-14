import json

from gzw_scraper import storage
from gzw_scraper.numeric_values import normalize_numeric_fields


def test_normalize_numeric_fields_only_extracts_unambiguous_values_with_units():
    record = {
        "name": "AK-12",
        "weight": "3.553 kg",
        "fire_rate": "700 RPM",
        "accuracy": "2.81 MOA",
        "recoil_control": "+16%",
        "muzzle_velocity": "970 m/s",
        "caliber": "5.45x39mm",
        "grid_size": "1x1",
        "localized_weight": "3,780 kg",
        "capacity": "30",
    }
    original = dict(record)

    normalized = normalize_numeric_fields(record)

    assert normalized["numeric_values"] == {
        "weight": {"value": 3.553, "unit": "kg"},
        "fire_rate": {"value": 700.0, "unit": "rpm"},
        "accuracy": {"value": 2.81, "unit": "moa"},
        "recoil_control": {"value": 16.0, "unit": "%"},
        "muzzle_velocity": {"value": 970.0, "unit": "m/s"},
    }
    assert normalized["weight"] == "3.553 kg"
    assert record == original
    assert "numeric_values" not in record


def test_existing_numeric_values_are_preserved_and_take_precedence():
    record = {
        "weight": "3.553 kg",
        "numeric_values": {
            "weight": {"value": 3.5, "unit": "kg"},
            "capacity": {"value": 30, "unit": "rounds"},
        },
    }

    normalized = normalize_numeric_fields(record)

    assert normalized["numeric_values"] == {
        "weight": {"value": 3.5, "unit": "kg"},
        "capacity": {"value": 30, "unit": "rounds"},
    }


def test_safe_save_persists_numeric_projections_without_mutating_input(tmp_path, monkeypatch):
    output_dir = tmp_path / "data"
    output_dir.mkdir()
    monkeypatch.setattr(storage, "OUTPUT_DIR", output_dir)
    monkeypatch.setattr(storage, "BACKUP_DIR", tmp_path / "backup")
    item = {"name": "AK-12", "weight": "3.553 kg", "fire_rate": "700 RPM"}

    assert storage.safe_save("weapons.json", [item]) is True

    saved = json.loads((output_dir / "weapons.json").read_text(encoding="utf-8"))
    assert saved[0]["weight"] == "3.553 kg"
    assert saved[0]["numeric_values"] == {
        "weight": {"value": 3.553, "unit": "kg"},
        "fire_rate": {"value": 700.0, "unit": "rpm"},
    }
    assert "numeric_values" not in item


def test_malformed_existing_numeric_values_are_not_overwritten():
    record = {"weight": "3.553 kg", "numeric_values": "source text"}

    normalized = normalize_numeric_fields(record)

    assert normalized["numeric_values"] == "source text"
