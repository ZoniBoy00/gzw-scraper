"""Community sell prices only fill gaps; they never replace wiki data and failures are non-fatal."""

import requests

from gzw_scraper import community_prices as cp


def entry(id_, price, name=""):
    return {"id": id_, "name": name, "price": price}


def test_fills_only_records_without_a_usable_price():
    datasets = {
        "food.json": [
            {"id": "canned-beans", "name": "Canned Beans"},
            {"id": "bread", "name": "Bread", "sell_price": "$40"},
            {"id": "mystery", "name": "Mystery", "sell_price": "???"},
            {"id": "unknown", "name": "Unknown"},
        ]
    }
    filled = cp.apply_prices(datasets, [entry("canned-beans", 1250), entry("bread", 99), entry("mystery", 7.5)])
    food = {r["id"]: r for r in datasets["food.json"]}
    assert filled == 2
    assert food["canned-beans"]["sell_price"] == "$1,250"
    assert food["canned-beans"]["sell_price_source"] == "gzwitems.com"
    assert food["bread"]["sell_price"] == "$40" and "sell_price_source" not in food["bread"]
    assert food["mystery"]["sell_price"] == "$7.5"
    assert "sell_price" not in food["unknown"]


def test_matches_ids_that_differ_only_in_punctuation_and_falls_back_to_the_name():
    datasets = {"ammo.json": [
        {"id": ".45-acp-ap", "name": ".45 ACP AP"},
        {"id": "9x19mm-xtreme-penetrator-p+", "name": "9x19mm Xtreme Penetrator +P"},
        {"id": "renamed-id", "name": "Lucky Pork Liver Pate"},
    ]}
    filled = cp.apply_prices(datasets, [
        entry("45-acp-ap", 3, ".45 ACP AP"),
        entry("9x19mm-xtreme-penetrator-p", 5, "9x19mm Xtreme Penetrator +P"),
        entry("other-id", 9, "Lucky Pork Liver Pate"),
    ])
    prices = {r["id"]: r["sell_price"] for r in datasets["ammo.json"]}
    assert filled == 3
    assert prices == {".45-acp-ap": "$3", "9x19mm-xtreme-penetrator-p+": "$5", "renamed-id": "$9"}


def test_ambiguous_matches_are_skipped():
    datasets = {"misc.json": [{"id": "key-a", "name": "Key A"}]}
    assert cp.apply_prices(datasets, [entry("key-a", 5), entry("key.a", 7)]) == 0
    assert "sell_price" not in datasets["misc.json"][0]


def test_disabled_or_empty_prices_change_nothing(monkeypatch):
    datasets = {"food.json": [{"id": "bread", "name": "Bread"}]}
    monkeypatch.setattr(cp, "ENABLED", False)
    assert cp.apply_prices(datasets) == 0
    assert cp.apply_prices(datasets, []) == 0
    assert datasets == {"food.json": [{"id": "bread", "name": "Bread"}]}


def test_fetch_keeps_only_positive_numeric_prices(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [
                {"id": "a", "name": "A", "sellPrice": 10},
                {"id": "b", "sellPrice": 0},
                {"id": "c", "sellPrice": "5"},
                {"id": "d", "sellPrice": True},
                {"id": "e"},
                "junk",
            ]}

    seen = {}

    def fake_get(url, headers=None, timeout=None):
        seen["headers"] = headers
        return Response()

    monkeypatch.setattr(cp.requests, "get", fake_get)
    assert cp.fetch_prices() == [{"id": "a", "name": "A", "price": 10}]
    assert seen["headers"]["X-Client"] == "gzw-scraper"


def test_fetch_failure_is_not_fatal(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(cp.requests, "get", boom)
    assert cp.fetch_prices() == []
