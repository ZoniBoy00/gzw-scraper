"""Game-file datasets are added beside the wiki datasets, never replacing one; bad input is skipped."""

import json

from gzw_scraper import game_datasets as gd


def write(tmp_path, name, payload):
    path = tmp_path / name
    path.write_text(payload if isinstance(payload, str) else json.dumps(payload), encoding="utf-8")


def test_loads_only_valid_gamedata_files(tmp_path):
    write(tmp_path, "gamedata_ammo.json", [{"id": "IS_Ammo_X", "name": "X"}, {"name": "no id"}, "text"])
    write(tmp_path, "gamedata_bad.json", "{not json")
    write(tmp_path, "gamedata_empty.json", [])
    write(tmp_path, "other.json", [{"id": "a", "name": "A"}])
    assert gd.load_datasets(tmp_path) == {"gamedata_ammo.json": [{"id": "IS_Ammo_X", "name": "X"}]}


def test_missing_folder_is_non_fatal(tmp_path):
    assert gd.load_datasets(tmp_path / "nope") == {}


def test_adds_datasets_and_never_replaces_an_existing_one():
    merged = {"ammo.json": [{"id": "wiki"}], "gamedata_ammo.json": [{"id": "keep"}]}
    added = gd.apply_datasets(merged, {"gamedata_ammo.json": [{"id": "new"}], "gamedata_armor.json": [{"id": "IS_A", "name": "A"}]})
    assert added == 1
    assert merged["gamedata_ammo.json"] == [{"id": "keep"}]
    assert merged["gamedata_armor.json"] == [{"id": "IS_A", "name": "A"}]


def test_disabled_flag_adds_nothing(monkeypatch):
    monkeypatch.setattr(gd, "ENABLED", False)
    merged = {}
    assert gd.apply_datasets(merged) == 0 and merged == {}
