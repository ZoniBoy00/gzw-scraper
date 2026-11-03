"""Community sell prices only fill gaps; they never replace wiki data and failures are non-fatal."""

import requests

from gzw_scraper import community_prices as cp


def test_fills_only_records_without_a_usable_price():
    datasets = {
        "food.json": [
            {"id": "canned-beans", "name": "Canned Beans"},
            {"id": "bread", "name": "Bread", "sell_price": "$40"},
            {"id": "mystery", "name": "Mystery", "sell_price": "???"},
            {"id": "unknown", "name": "Unknown"},
        ]
    }
    filled = cp.apply_prices(datasets, {"canned-beans": 1250, "bread": 99, "mystery": 7.5})
    food = {r["id"]: r for r in datasets["food.json"]}
    assert filled == 2
    assert food["canned-beans"]["sell_price"] == "$1,250"
    assert food["canned-beans"]["sell_price_source"] == "gzwitems.com"
    assert food["bread"]["sell_price"] == "$40" and "sell_price_source" not in food["bread"]
    assert food["mystery"]["sell_price"] == "$7.5"
    assert "sell_price" not in food["unknown"]


def test_disabled_or_empty_prices_change_nothing(monkeypatch):
    datasets = {"food.json": [{"id": "bread", "name": "Bread"}]}
    monkeypatch.setattr(cp, "ENABLED", False)
    assert cp.apply_prices(datasets) == 0
    assert cp.apply_prices(datasets, {}) == 0
    assert datasets == {"food.json": [{"id": "bread", "name": "Bread"}]}


def test_fetch_keeps_only_positive_numeric_prices(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {"items": [
                {"id": "a", "sellPrice": 10},
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
    assert cp.fetch_prices() == {"a": 10}
    assert seen["headers"]["X-Client"] == "gzw-scraper"


def test_fetch_failure_is_not_fatal(monkeypatch):
    def boom(*args, **kwargs):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(cp.requests, "get", boom)
    assert cp.fetch_prices() == {}
