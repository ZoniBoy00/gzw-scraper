"""GZW Items values win over the wiki; unmatched entries are added only for configured categories; failures are non-fatal."""

import requests

from gzw_scraper import community_prices as cp


def entry(id_, price, name="", **extra):
    return {"id": id_, "name": name or id_, "sellPrice": price, **extra}


def test_gzwitems_values_win_and_mark_their_source():
    datasets = {
        "loot_items.json": [
            {"id": "morphine", "name": "Morphine", "sell_price": "$4,000", "weight": "5kg", "grid_size": "2x2",
             "price_per_grid_cell": "$1,000", "numeric_values": {"weight": {"value": 5, "unit": "kg"}}},
            {"id": "camera", "name": "Camera", "sell_price": "$150", "weight": "0.35kg"},
            {"id": "beans", "name": "Beans"},
            {"id": "unknown", "name": "Unknown"},
        ]
    }
    changed, added, skipped = cp.apply_items(datasets, [
        entry("morphine", 8000, sellPricePerSlot=2000, weight=5.5, gridSize="2x2"),
        entry("camera", 150, sellPricePerSlot=150, weight=0.35, gridSize="1x1"),
        entry("beans", 1250, weight=0.4),
    ])
    rows = {r["id"]: r for r in datasets["loot_items.json"]}
    assert (changed, added, skipped) == (4, 0, 0)
    morphine = rows["morphine"]
    assert morphine["sell_price"] == "$8,000" and morphine["sell_price_source"] == "gzwitems.com"
    assert morphine["price_per_grid_cell"] == "$2,000" and morphine["weight"] == "5.5 kg"
    assert "weight" not in morphine["numeric_values"]  # recomputed from the new text on save
    assert "grid_size_source" not in morphine  # unchanged values keep no marker
    # Equal values in another spelling are left alone; fields the record lacks are not added (except the price).
    assert rows["camera"] == {"id": "camera", "name": "Camera", "sell_price": "$150", "weight": "0.35kg"}
    assert rows["beans"] == {"id": "beans", "name": "Beans", "sell_price": "$1,250", "sell_price_source": "gzwitems.com"}
    assert "sell_price" not in rows["unknown"]


def test_fractional_ammo_prices_keep_their_decimals():
    datasets = {"ammo.json": [{"id": "fmj", "name": "FMJ"}]}
    cp.apply_items(datasets, [entry("fmj", 0.2)])
    assert datasets["ammo.json"][0]["sell_price"] == "$0.2"
    assert cp.format_price(26844.3) == "$26,844.3" and cp.format_price(1250.0) == "$1,250"


def test_matches_ids_that_differ_only_in_punctuation_and_falls_back_to_the_name():
    datasets = {"ammo.json": [
        {"id": ".45-acp-ap", "name": ".45 ACP AP"},
        {"id": "9x19mm-xtreme-penetrator-p+", "name": "9x19mm Xtreme Penetrator +P"},
        {"id": "renamed-id", "name": "Lucky Pork Liver Pate"},
    ]}
    cp.apply_items(datasets, [
        entry("45-acp-ap", 3, ".45 ACP AP"),
        entry("9x19mm-xtreme-penetrator-p", 5, "9x19mm Xtreme Penetrator +P"),
        entry("other-id", 9, "Lucky Pork Liver Pate"),
    ])
    prices = {r["id"]: r["sell_price"] for r in datasets["ammo.json"]}
    assert prices == {".45-acp-ap": "$3", "9x19mm-xtreme-penetrator-p+": "$5", "renamed-id": "$9"}


def test_ambiguous_matches_are_skipped():
    datasets = {"misc.json": [{"id": "key-a", "name": "Key A"}]}
    cp.apply_items(datasets, [entry("key-a", 5, "Key one"), entry("key.a", 7, "Key two")])
    assert "sell_price" not in datasets["misc.json"][0]


def test_unmatched_entries_are_added_only_for_configured_categories(monkeypatch):
    monkeypatch.setattr(cp, "ADD_CATEGORIES", {"Intel": "intels"})
    datasets = {"intels.json": [{"id": "poster", "name": "Poster", "type": "Intel"}]}
    changed, added, skipped = cp.apply_items(datasets, [
        entry("ban-pa-note", 300, "Ban Pa\nNote", category="Intel", weight=0.01, gridSize="1x1"),
        entry("broken", 1, "Angered PMC�s Diary", category="Intel"),
        entry("renamed-key", 50, "Renamed Key", category="Keys"),
    ])
    assert (added, skipped) == (1, 2)
    assert datasets["intels.json"][1] == {
        "name": "Ban Pa Note", "id": "ban-pa-note", "type": "Intel",
        "sell_price": "$300", "weight": "0.01 kg", "grid_size": "1x1", "source": "gzwitems.com",
    }


def test_disabled_or_empty_data_changes_nothing(monkeypatch):
    datasets = {"food.json": [{"id": "bread", "name": "Bread"}]}
    monkeypatch.setattr(cp, "ENABLED", False)
    assert cp.apply_items(datasets) == (0, 0, 0)
    assert cp.apply_items(datasets, []) == (0, 0, 0)
    assert datasets == {"food.json": [{"id": "bread", "name": "Bread"}]}


def test_unsellable_items_get_no_price():
    datasets = {"keys.json": [{"id": "key", "name": "Key"}]}
    cp.apply_items(datasets, [entry("key", 100, sellable=False)])
    assert "sell_price" not in datasets["keys.json"][0]


def test_fetch_keeps_entries_with_id_and_name(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [{"id": "a", "name": "A", "sellPrice": 10}, {"id": "b"}, {"name": "C"}, "junk"]}

    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return Response()

    monkeypatch.setattr(cp.requests, "get", fake_get)
    assert cp.fetch_items() == [{"id": "a", "name": "A", "sellPrice": 10}]
    assert seen["headers"]["X-Client"] == "gzw-scraper"


def test_fetch_failure_is_not_fatal(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(cp.requests, "get", boom)
    assert cp.fetch_items() == []
