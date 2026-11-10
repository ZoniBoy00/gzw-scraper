import json

import bs4
import pytest

from gzw_scraper import config, parsing, pipeline, publication, storage


@pytest.mark.parametrize("value, expected", [("3ss", "3s"), ("11s s", "11s"), ("0.5ss", "0.5s"), ("Glass", "Glass"), ("3s", "3s")])
def test_duration_sanitization(value, expected):
    assert parsing.sanitize_value(value) == expected


def test_infobox_compatibility_fields():
    soup = bs4.BeautifulSoup('<aside class="portable-infobox"><div class="pi-data"><h3 class="pi-data-label">Armor class</h3><div class="pi-data-value">NIJ III+</div></div><div class="pi-data"><h3 class="pi-data-label">Use time</h3><div class="pi-data-value">3ss</div></div></aside>', 'html.parser')
    assert parsing.parse_infobox(soup) == {"nij_class": "NIJ III+", "use_time": "3s"}


def test_iv_joins_medical_in_file_and_defaults(tmp_path):
    for cfg in (config.load_config(), config.load_config(tmp_path / "missing.toml")):
        assert cfg["category_to_filename"]["IV"] == "medical"


def test_reviewed_rename_preserves_id_but_ambiguous_identity_does_not():
    incoming = [{"name": "Civilian Trekstars 70L", "id": "civilian-trekstars-70l"}]
    previous = [{"name": "Civilian Trekstars 70L Backpack", "id": "legacy-pack"}]
    publication.preserve_record_ids(incoming, previous)
    assert incoming[0]["id"] == "legacy-pack"
    publication.preserve_record_ids(incoming, previous + [{"name": "Civilian Trekstars 70L", "id": "other"}])
    assert incoming[0]["id"] == "legacy-pack"


def test_vendor_ids_survive_category_and_reviewed_name_changes():
    previous = [{"id": "handshake-helmets-cap", "item": "Cap", "vendor": "Handshake"}, {"id": "artisan-backpacks-old-pack", "item": "Civilian Trekstars 70L Backpack", "vendor": "Artisan"}]
    datasets = {"hat.json": [{"name": "Cap", "sold_by": "Handshake R.2"}], "backpacks.json": [{"name": "Civilian Trekstars 70L", "sold_by": "Artisan R.4"}]}
    rows = pipeline.build_vendor_stock(datasets, {"Handshake", "Artisan"}, previous)
    assert {r["id"] for r in rows} == {r["id"] for r in previous}


def test_inventory_uses_retained_and_guarded_data_and_fresh_records(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(storage, "BACKUP_DIR", tmp_path / "backup")
    old = [{"name": f"Grenade {n}", "id": f"grenade-{n}", "sold_by": "Gunny R.2"} for n in range(10)]
    (tmp_path / "throwables.json").write_text(json.dumps(old), encoding="utf-8")
    assert storage.safe_save("throwables.json", old[:1], 10) is False
    (tmp_path / "face_cover.json").write_text(json.dumps([{"name": "Mask", "sold_by": "Gunny R.1"}]), encoding="utf-8")
    (tmp_path / "face_mask.json").write_text(json.dumps([{"name": "Mask", "sold_by": "Loot only"}]), encoding="utf-8")
    (tmp_path / "gamedata_test.json").write_text(json.dumps([{"name": "Game Only", "sold_by": "Gunny R.1"}]), encoding="utf-8")
    datasets = publication.load_published_wiki_data(tmp_path)
    rows = pipeline.build_vendor_stock(datasets, {"Gunny"}, preferred_datasets={"face_mask.json"})
    assert len(rows) == 10
    assert all(row["item"].startswith("Grenade") for row in rows)


def test_shared_current_prices_fill_gaps_without_overwriting_or_guessing():
    datasets = {"loot_items.json": [{"name": "Tool", "sell_price": "$50", "sell_price_source": "gzwitems.com"}], "tools.json": [{"name": "Tool"}, {"name": "Known", "sell_price": "$12"}]}
    publication.fill_shared_sell_prices(datasets)
    assert datasets["tools.json"][0]["sell_price"] == "$50"
    assert datasets["tools.json"][0]["sell_price_source"] == "gzwitems.com"
    assert datasets["tools.json"][1]["sell_price"] == "$12"
    datasets["loot_items.json"].append({"name": "Tool", "sell_price": "$60"})
    datasets["tools.json"][0] = {"name": "Tool"}
    publication.fill_shared_sell_prices(datasets)
    assert "sell_price" not in datasets["tools.json"][0]


def test_provision_totals_require_current_unambiguous_inputs():
    datasets = {"food.json": [{"name": "Chews", "charges": "3", "grid_size": "1x1"}], "provisions.json": [{"name": "Chews", "energy": "+5"}, {"name": "Missing", "energy": "+10"}]}
    publication.fill_provision_totals(datasets)
    assert datasets["provisions.json"][0]["total_energy_per_grid"] == "+15"
    assert "total_energy_per_grid" not in datasets["provisions.json"][1]


def test_full_scrape_builds_inventory_after_guarded_saves(tmp_path, monkeypatch):
    from gzw_scraper import community_prices, game_data_corrections, game_datasets, network

    monkeypatch.setattr(storage, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(storage, "BACKUP_DIR", tmp_path / "backup")
    old = [{"name": f"Grenade {n}", "id": f"grenade-{n}", "sold_by": "Gunny R.2"} for n in range(10)]
    (tmp_path / "throwables.json").write_text(json.dumps(old), encoding="utf-8")
    (tmp_path / "vendors.json").write_text(json.dumps([{"name": "Gunny", "id": "gunny"}]), encoding="utf-8")
    monkeypatch.setattr(network, "get_all_categories", lambda: [{"name": "Throwables", "title": "Throwables", "pages": 1}])
    monkeypatch.setattr(network, "filter_game_categories", lambda categories: categories)
    monkeypatch.setattr(network, "get_all_pages", lambda: [])
    monkeypatch.setattr(pipeline, "get_filtered_categories", lambda *args: [])
    monkeypatch.setattr(pipeline, "scrape_single_category_task", lambda *args: ("throwables.json", old[:1]))
    monkeypatch.setattr(pipeline, "scrape_supplemental_pages", lambda: {})
    monkeypatch.setattr(pipeline, "scrape_hideout_tables", lambda: {})
    monkeypatch.setattr(pipeline, "scrape_experience_tables", lambda: {})
    monkeypatch.setattr(pipeline, "scrape_trading_vendors", lambda: [])
    monkeypatch.setattr(pipeline, "LISTING_PAGES", {})
    monkeypatch.setattr(pipeline, "write_scrape_metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "tqdm", None)
    monkeypatch.setattr(community_prices, "ENABLED", False)
    monkeypatch.setattr(game_data_corrections, "ENABLED", False)
    monkeypatch.setattr(game_datasets, "ENABLED", False)
    pipeline.run_full_scrape()
    assert len(json.loads((tmp_path / "throwables.json").read_text())) == 10
    inventory = json.loads((tmp_path / "vendor_stock.json").read_text())
    assert len(inventory) == 10
    assert {row["vendor"] for row in inventory} == {"Gunny"}


def test_game_file_identifiers_are_not_rewritten(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "OUTPUT_DIR", tmp_path)
    monkeypatch.setattr(storage, "BACKUP_DIR", tmp_path / "backup")
    (tmp_path / "gamedata_test.json").write_text(json.dumps([{"name": "Example", "id": "IS_Old"}]), encoding="utf-8")
    assert storage.safe_save("gamedata_test.json", [{"name": "Example", "id": "IS_New"}], 1)
    assert json.loads((tmp_path / "gamedata_test.json").read_text())[0]["id"] == "IS_New"
