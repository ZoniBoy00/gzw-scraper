"""Game-data corrections replace only the observed stale wiki value; a changed wiki value wins and failures are non-fatal."""

import json

from gzw_scraper import game_data_corrections as gdc


def correction(field="sold_by", old="Banshee R.4", new="Banshee R.3", id_="4.6x30mm-ap-sx", dataset="ammo"):
    return {"dataset": dataset, "id": id_, "field": field, "from": old, "to": new}


def test_replaces_the_stale_value_and_marks_the_source():
    datasets = {"ammo.json": [{"id": "4.6x30mm-ap-sx", "name": "4.6x30mm AP SX", "sold_by": "Banshee R.4"}]}
    assert gdc.apply_corrections(datasets, [correction()]) == (1, 0, 0)
    record = datasets["ammo.json"][0]
    assert record["sold_by"] == "Banshee R.3"
    assert record["sold_by_source"] == "game-files"


def test_skips_when_the_wiki_value_changed_since_the_observation():
    datasets = {"ammo.json": [{"id": "4.6x30mm-ap-sx", "sold_by": "Banshee R.2"}]}
    assert gdc.apply_corrections(datasets, [correction()]) == (0, 1, 0)
    assert datasets["ammo.json"][0]["sold_by"] == "Banshee R.2"
    assert "sold_by_source" not in datasets["ammo.json"][0]


def test_already_correct_values_are_left_alone_and_a_second_run_changes_nothing():
    datasets = {"ammo.json": [{"id": "4.6x30mm-ap-sx", "sold_by": "Banshee R.4"}]}
    gdc.apply_corrections(datasets, [correction()])
    assert gdc.apply_corrections(datasets, [correction()]) == (0, 0, 0)
    datasets = {"ammo.json": [{"id": "4.6x30mm-ap-sx", "sold_by": "Banshee R.3"}]}
    assert gdc.apply_corrections(datasets, [correction()]) == (0, 0, 0)
    assert "sold_by_source" not in datasets["ammo.json"][0]


def test_unknown_records_and_missing_fields_are_counted_not_created():
    datasets = {"ammo.json": [{"id": "other", "sold_by": "Gunny R.1"}], "food.json": [{"id": "4.6x30mm-ap-sx"}]}
    assert gdc.apply_corrections(datasets, [correction(), correction(dataset="food")]) == (0, 0, 2)
    assert "sold_by" not in datasets["food.json"][0]


def test_stale_numeric_projection_is_dropped_so_it_is_recomputed():
    datasets = {"ammo.json": [{"id": "x", "weight": "0.01kg", "muzzle_velocity": "680 m/s",
                               "numeric_values": {"weight": {"value": 0.01, "unit": "kg"}, "muzzle_velocity": {"value": 680.0, "unit": "m/s"}}}]}
    gdc.apply_corrections(datasets, [correction("weight", "0.01kg", "0.0105 kg", "x")])
    assert datasets["ammo.json"][0]["weight"] == "0.0105 kg"
    assert datasets["ammo.json"][0]["numeric_values"] == {"muzzle_velocity": {"value": 680.0, "unit": "m/s"}}


def test_load_ignores_missing_invalid_and_malformed_entries(tmp_path):
    assert gdc.load_corrections(tmp_path / "missing.json") == []
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    assert gdc.load_corrections(bad) == []
    good = tmp_path / "good.json"
    good.write_text(json.dumps({"corrections": [correction(), {"dataset": "ammo"}, "text"]}), encoding="utf-8")
    assert gdc.load_corrections(good) == [correction()]


def test_disabled_flag_applies_nothing(monkeypatch):
    monkeypatch.setattr(gdc, "ENABLED", False)
    datasets = {"ammo.json": [{"id": "4.6x30mm-ap-sx", "sold_by": "Banshee R.4"}]}
    assert gdc.apply_corrections(datasets) == (0, 0, 0)
