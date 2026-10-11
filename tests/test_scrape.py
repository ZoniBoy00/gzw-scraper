"""
Regression tests for the scraper's fragile seams.

The implementation lives in the ``gzw_scraper`` package (``scrape`` is a thin
compatibility facade that re-exports it). Tests patch the owning modules:
``gzw_scraper.network`` for wiki/API access, ``gzw_scraper.pipeline`` for
orchestration and ``gzw_scraper.storage`` for persistence. The seams cover
Fandom HTML structure (portable-infobox / wikitable), ballistics parsing,
category filtering (real game categories must never be skipped) and
safe_save's >70% drop-guard — if any of these regress, these tests break
before production data silently degrades.

Run: pytest tests/ -v
"""

import json
from pathlib import Path

import bs4
import pytest

import scrape
from gzw_scraper import network, pipeline, storage


# ─── parse_infobox ───

INFOBOX_HTML = """
<html><body>
<aside class="portable-infobox pi-background pi-theme-wikia">
  <h2 class="pi-item pi-title">AK-74</h2>
  <div class="pi-image-collection">
    <img src="https://static.wikia.nocookie.net/gzw/images/ak74.png" />
  </div>
  <div class="pi-item pi-data" data-source="caliber">
    <h3 class="pi-data-label">Caliber</h3>
    <div class="pi-data-value">5.45x39mm</div>
  </div>
  <div class="pi-item pi-data" data-source="fire_rate">
    <h3 class="pi-data-label">Fire Rate</h3>
    <div class="pi-data-value">650   RPM</div>
  </div>
</aside>
</body></html>
"""

INFOBOX_HTML_MULTI_VALUE_WHITESPACE = """
<aside class="portable-infobox">
  <div class="pi-data">
    <h3 class="pi-data-label">Weight</h3>
    <div class="pi-data-value">
        3.2
        kg
    </div>
  </div>
</aside>
"""

INFOBOX_HTML_NO_LABEL = """
<aside class="portable-infobox">
  <div class="pi-data">
    <div class="pi-data-value">orphan value, no label</div>
  </div>
</aside>
"""

INFOBOX_HTML_MISSING = """
<html><body><p>No infobox on this page at all.</p></body></html>
"""


def soup_of(html):
    return bs4.BeautifulSoup(html, "lxml")


def fixture(name: str) -> str:
    return (Path(__file__).parent / "fixtures" / name).read_text(encoding="utf-8")


def test_parse_infobox_uses_standard_sanitized_fixture():
    data = scrape.parse_infobox(soup_of(fixture("infobox_standard.html")))
    assert data["caliber"] == "5.45x39mm"
    assert data["fire_rate"] == "650 RPM"
    assert data["_image"] == "https://static.example.test/ak-74.png"


def test_parse_infobox_uses_missing_field_fixture_without_crashing():
    data = scrape.parse_infobox(soup_of(fixture("infobox_missing_fields.html")))
    assert data["name"] == "Example Item"
    assert "empty_value" not in data


def test_parse_infobox_handles_malformed_fixture():
    data = scrape.parse_infobox(soup_of(fixture("malformed_page.html")))
    assert data["broken"] == "Value"


def test_parse_infobox_extracts_label_value_pairs():
    data = scrape.parse_infobox(soup_of(INFOBOX_HTML))
    assert data["caliber"] == "5.45x39mm"
    assert data["fire_rate"] == "650 RPM"  # whitespace collapsed


def test_parse_infobox_extracts_image():
    data = scrape.parse_infobox(soup_of(INFOBOX_HTML))
    assert data["_image"] == "https://static.wikia.nocookie.net/gzw/images/ak74.png"


def test_parse_infobox_collapses_multiline_whitespace():
    data = scrape.parse_infobox(soup_of(INFOBOX_HTML_MULTI_VALUE_WHITESPACE))
    assert data["weight"] == "3.2 kg"


def test_parse_infobox_skips_entries_without_label():
    # A pi-data block missing its label should not crash and should not
    # produce a bogus key.
    data = scrape.parse_infobox(soup_of(INFOBOX_HTML_NO_LABEL))
    assert data == {}


def test_parse_infobox_returns_empty_dict_when_no_infobox():
    data = scrape.parse_infobox(soup_of(INFOBOX_HTML_MISSING))
    assert data == {}


def test_parse_infobox_handles_none_soup():
    # Guards against parse_page() returning None (failed fetch/parse).
    assert scrape.parse_infobox(None) == {}


def test_parse_infobox_label_normalization():
    # Labels get lowercased and spaces -> underscores; this is the contract
    # scrape_category relies on when merging fields into an item dict.
    html = """
    <aside class="portable-infobox">
      <div class="pi-data">
        <h3 class="pi-data-label">Muzzle Velocity</h3>
        <div class="pi-data-value">880 m/s</div>
      </div>
    </aside>
    """
    data = scrape.parse_infobox(soup_of(html))
    assert "muzzle_velocity" in data
    assert data["muzzle_velocity"] == "880 m/s"


# ─── scrape_listing_page ───
# scrape_listing_page() internally calls parse_page() to fetch the wiki
# page via the API, so it isn't a pure function -- we monkeypatch
# parse_page to inject fixture HTML instead of hitting the network.

LISTING_TABLE_HTML = """
<html><body>
<table class="wikitable sortable">
  <tr><th>Icon</th><th>Name</th><th>Type</th><th>Weight</th></tr>
  <tr>
    <td><img src="https://static.wikia.nocookie.net/gzw/images/bandage.png" /></td>
    <td>Bandage</td>
    <td>Medical</td>
    <td>0.1 kg</td>
  </tr>
  <tr>
    <td><img src="https://static.wikia.nocookie.net/gzw/images/splint.png" /></td>
    <td>Splint</td>
    <td>Medical</td>
    <td>0.2 kg</td>
  </tr>
</table>
</body></html>
"""

LISTING_ARMOR_RATINGS_HTML = """
<html><body>
<table class="wikitable sortable">
  <tr><th>Icon</th><th>Name</th><th>NIJ</th><th>Material</th><th>Plates</th></tr>
  <tr>
    <td><img src="https://static.wikia.nocookie.net/gzw/images/press_icon.png" /></td>
    <td><a href="/wiki/Covert_Tactical_Body_Armour_(Press)">Covert Tactical Body Armour (Woodland)</a></td>
    <td>P3 / IIIA</td><td>Aramid</td><td>Front, Back</td>
  </tr>
  <tr>
    <td><img src="https://static.wikia.nocookie.net/gzw/images/buran_icon.png" /></td>
    <td><a href="/wiki/Buran%27s_Pantsir_2.0">Buran's Pantsir 2.0</a></td>
    <td>R3 / III++</td><td>Ceramic</td><td>Front, Back, Sides</td>
  </tr>
  <tr>
    <td><img src="https://static.wikia.nocookie.net/gzw/images/m2_icon.png" /></td>
    <td><a href="/wiki/M2_Plate_Carrier">M2 Plate Carrier</a></td>
    <td>R3 / III++</td><td>Ceramic</td><td>Front, Back</td>
  </tr>
</table>
</body></html>
"""

LISTING_TABLE_HTML_NO_NAME_OR_ICON_COLUMN = """
<html><body>
<table class="wikitable">
  <tr><th>Price</th><th>Rarity</th></tr>
  <tr><td>500</td><td>Common</td></tr>
</table>
</body></html>
"""

LISTING_TABLE_HTML_EMPTY_TABLE = """
<html><body>
<table class="wikitable"><tr><th>Name</th></tr></table>
</body></html>
"""

LISTING_TABLE_HTML_DATA_SRC_FALLBACK = """
<html><body>
<table class="wikitable">
  <tr><th>Name</th><th>Type</th></tr>
  <tr>
    <td><img src="data:image/gif;base64,R0lGOD" data-src="https://static.wikia.nocookie.net/gzw/images/real.png" /> Suture Kit</td>
    <td>Medical</td>
  </tr>
</table>
</body></html>
"""


BALLISTICS_TABLE_HTML = """
<html><body>
<table class="wikitable">
  <tr><th>Caliber</th><th>Name</th><th>Speed</th><th>Acc</th><th>Dur</th><th>Bullet effectiveness against armor class</th></tr>
  <tr>
    <td><a href="/wiki/5.45x39mm">5.45x39mm</a></td>
    <td><a href="/wiki/5.45x39mm_BP">5.45x39mm BP (7N22)</a></td>
    <td>840 m/s</td><td>+1</td><td>-100</td>
    <td>2</td><td>2</td><td>2</td><td>2</td><td>2</td><td>2</td><td>2</td><td>2</td>
  </tr>
  <tr>
    <td><a href="/wiki/5.45x39mm">5.45x39mm</a></td>
    <td><a href="/wiki/5.45x39mm_BS">5.45x39mm BS (7N24)</a></td>
    <td>840 m/s</td><td>+6</td><td>-100</td>
    <td>2</td><td>2</td><td>2</td><td>2</td><td>2</td><td>2</td><td>2</td><td>1</td>
  </tr>
  <tr>
    <td><a href="/wiki/5.45x39mm">5.45x39mm</a></td>
    <td><a href="/wiki/5.45x39mm_WOLF">5.45x39mm WOLF</a></td>
    <td>840 m/s</td><td>-2</td><td></td>
    <td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td>
  </tr>
</table>
</body></html>
"""


def test_scrape_ballistics_penetration_maps_thresholds(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda title: soup_of(BALLISTICS_TABLE_HTML))
    thresholds = scrape.scrape_ballistics_penetration()

    assert thresholds["5.45x39mm bp (7n22)"] == "IV"
    assert thresholds["5.45x39mm bs (7n24)"] == "III++"
    assert thresholds["5.45x39mm wolf"] is None


def test_normalise_ammo_name_collapses_whitespace_and_nonbreaking_spaces():
    assert scrape._normalise_ammo_name("5.45x39mm\u00a0  BP") == "5.45x39mm bp"


def test_scrape_ballistics_parses_caliber_link_with_plain_text_ammo_name(monkeypatch):
    html = """
    <table>
      <tr><th>Caliber</th><th>Name</th><th>Bullet effectiveness against armor class</th></tr>
      <tr>
        <td><a href="/wiki/7.62x25mm">7.62x25mm</a></td><td>FMJ</td>
        <td>2</td><td>2</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td><td>0</td>
      </tr>
    </table>
    """
    monkeypatch.setattr(network, "parse_page", lambda title: soup_of(html))

    thresholds = scrape.scrape_ballistics_penetration()

    assert thresholds["7.62x25mm fmj"] == "IIA+"


def test_apply_ballistics_penetration_marks_all_zero_thresholds():
    items = [
        {"name": "5.45x39mm BP (7N22)"},
        {"name": "5.45x39mm WOLF", "stopped_by_armor_class": "NIJ I"},
    ]
    updated = scrape.apply_ballistics_penetration(
        items,
        {"5.45x39mm bp (7n22)": "IV", "5.45x39mm wolf": None},
    )

    assert updated == 2
    assert items[0]["stopped_by_armor_class"] == "NIJ IV"
    assert items[1]["stopped_by_armor_class"] == "NIJ 0"


def test_scrape_listing_page_extracts_rows(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda title: soup_of(LISTING_TABLE_HTML))
    items = scrape.scrape_listing_page("medical", "Medical Items")

    names = {item["name"] for item in items}
    assert names == {"Bandage", "Splint"}

    bandage = next(i for i in items if i["name"] == "Bandage")
    assert bandage["id"] == "bandage"
    assert bandage["type"] == "Medical"
    assert bandage["image"] == "https://static.wikia.nocookie.net/gzw/images/bandage.png"


def test_scrape_listing_page_adds_armor_rating_and_merges_table_alias(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda title: soup_of(LISTING_ARMOR_RATINGS_HTML))
    existing = [{
        "name": "Covert Tactical Body Armour (Press)",
        "id": "covert-tactical-body-armour-press",
        "nij_class": "IIIA",
        "material": "Aramid",
        "armor_locations": "Front, Back",
        "image": "https://example.test/press-inspect.png",
    }, {
        "name": "M2 Plate Carrier",
        "id": "m2-plate-carrier",
        "nij_class": "III+",
    }]
    listing_items = scrape.scrape_listing_page(
        "vests",
        "Armor Vests",
        existing_items=existing,
        fields={"protection_rating", "nij_class", "material", "armor_locations"},
    )
    items = storage.merge_items(existing, listing_items)

    assert len(items) == 3
    press = next(item for item in items if item["name"] == "Covert Tactical Body Armour (Press)")
    assert press["id"] == "covert-tactical-body-armour-press"
    assert press["protection_rating"] == "P3"
    assert press["nij_class"] == "IIIA"
    assert press["material"] == "Aramid"
    assert press["armor_locations"] == "Front, Back"
    assert press["image"] == "https://example.test/press-inspect.png"

    m2 = next(item for item in items if item["name"] == "M2 Plate Carrier")
    assert m2["nij_class"] == "III++"
    assert m2["protection_rating"] == "R3"

    buran = next(item for item in items if item["name"] == "Buran's Pantsir 2.0")
    assert buran["id"] == "burans-pantsir-2.0"
    assert buran["protection_rating"] == "R3"
    assert buran["nij_class"] == "III++"
    assert buran["material"] == "Ceramic"
    assert buran["armor_locations"] == "Front, Back, Sides"


def test_listing_page_configures_vest_and_plate_carrier_rating_fields():
    cfg = scrape.load_config()
    assert cfg["listing_pages"]["vests"] == "Armor Vests"
    assert cfg["listing_pages"]["plate_carriers"] == "Plate Carriers"
    assert cfg["listing_page_fields"]["vests"] == [
        "protection_rating", "nij_class", "material", "armor_locations"
    ]
    assert cfg["listing_page_fields"]["plate_carriers"] == [
        "protection_rating", "nij_class", "material", "armor_locations"
    ]
    assert cfg["listing_page_only"]["datasets"] == ["provisions"]
    assert cfg["category_index_pages"]["Armor Vest"] == "Armor Vests"


def test_scrape_listing_page_skips_tables_without_name_or_icon(monkeypatch):
    monkeypatch.setattr(
        network, "parse_page", lambda title: soup_of(LISTING_TABLE_HTML_NO_NAME_OR_ICON_COLUMN)
    )
    items = scrape.scrape_listing_page("misc", "Misc Page")
    assert items == []


def test_scrape_listing_page_skips_tables_with_only_header_row(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda title: soup_of(LISTING_TABLE_HTML_EMPTY_TABLE))
    items = scrape.scrape_listing_page("misc", "Misc Page")
    assert items == []


def test_scrape_listing_page_returns_empty_when_page_fails_to_parse(monkeypatch):
    # parse_page returns None when the API call/HTML parse fails upstream.
    monkeypatch.setattr(network, "parse_page", lambda title: None)
    items = scrape.scrape_listing_page("medical", "Medical Items")
    assert items == []


def test_scrape_listing_page_falls_back_to_data_src_for_lazy_loaded_images(monkeypatch):
    monkeypatch.setattr(
        network, "parse_page", lambda title: soup_of(LISTING_TABLE_HTML_DATA_SRC_FALLBACK)
    )
    items = scrape.scrape_listing_page("medical", "Medical Items")
    assert len(items) == 1
    assert items[0]["image"] == "https://static.wikia.nocookie.net/gzw/images/real.png"


# ─── Category filtering ───
# Regression: real game categories (Tasks, Keys, Tech, Task, Tiger Bay) were
# once added to the infrastructure skip list, which silently wiped their
# datasets from gzw-data. They MUST survive filtering.

def test_filter_game_categories_keeps_real_game_categories():
    cats = [
        {"*": "Weapons", "size": 44},
        {"*": "Tasks", "size": 128},
        {"*": "Keys", "size": 127},
        {"*": "Tech", "size": 1},
        {"*": "Task", "size": 1},
        {"*": "Tiger Bay", "size": 1},
        {"*": "Squad Strike Missions", "size": 32},
        {"*": "Loot", "size": 1},
        {"*": "Apparel", "size": 1},
        {"*": "Removed Content", "size": 24},
        {"*": "Upcoming Content", "size": 11},
    ]
    kept = scrape.filter_game_categories(cats)
    titles = {k["title"] for k in kept}
    for expected in ("Weapons", "Tasks", "Keys", "Tech", "Task",
                     "Tiger Bay", "Squad Strike Missions", "Loot", "Apparel"):
        assert expected in titles, f"{expected} was filtered out!"
    assert {"Removed Content", "Upcoming Content"} <= titles
    assert scrape.CATEGORY_TO_FILENAME["Removed Content"] == "removed_content"
    assert scrape.CATEGORY_TO_FILENAME["Upcoming Content"] == "upcoming_content"


def test_filter_game_categories_skips_wiki_infrastructure():
    cats = [
        {"*": "Templates", "size": 16},
        {"*": "Images", "size": 3},
        {"*": "Front page", "size": 1},
        {"*": "Pages missing details", "size": 334},
        {"*": "Navbox templates", "size": 9},
    ]
    kept = scrape.filter_game_categories(cats)
    assert kept == []


# ─── Data-loss guards ───
# A failed scrape (rate limit / wiki down) must NEVER overwrite good data.

def test_get_category_members_returns_none_on_api_failure(monkeypatch):
    monkeypatch.setattr(network, "api_call", lambda params: None)
    assert network.get_category_members("Weapons") is None


def test_scrape_category_returns_none_when_members_fetch_fails(monkeypatch):
    monkeypatch.setattr(network, "get_category_members", lambda name, limit: None)
    assert scrape.scrape_category("Weapons", "Weapons") is None


def test_scrape_category_skips_explicitly_listed_pages(monkeypatch):
    # Pages in the config [skip_pages] list (redirects/index/lore articles)
    # must be dropped even though they sit in a game category.
    monkeypatch.setattr(
        network, "get_category_members",
        lambda name, limit: [{"title": "Intels"}, {"title": "M14 Rifle"}],
    )
    monkeypatch.setattr(network, "parse_page", lambda title: soup_of(INFOBOX_HTML_MISSING))
    # Keep the test hermetic: the page-image fallback must not reach the wiki API.
    monkeypatch.setattr(network, "get_page_image", lambda title, soup=None: None)
    monkeypatch.setattr(pipeline, "SKIP_PAGES", {"Intels"})
    monkeypatch.setattr(pipeline, "PAGE_DELAY", 0)
    items = scrape.scrape_category("Upcoming Content", "Upcoming Content")
    assert [i["name"] for i in items] == ["M14 Rifle"]


def test_safe_save_aborts_on_major_drop_preserving_previous_data(tmp_path, monkeypatch):
    out = tmp_path / "data"
    bak = tmp_path / "data_backup"
    out.mkdir()
    old_items = [{"name": f"Item {i}", "id": f"item-{i}"} for i in range(42)]
    (out / "helmets.json").write_text(json.dumps(old_items), encoding="utf-8")
    monkeypatch.setattr(storage, "OUTPUT_DIR", out)
    monkeypatch.setattr(storage, "BACKUP_DIR", bak)

    # 42 -> 2 items is a >70% drop: must be rejected, file stays intact
    new_items = [
        {"name": "AMP-1 TP LC (Ranger Green)"},
        {"name": "AMP-1 TP LC (UNLRA)"},
    ]
    ok = scrape.safe_save("helmets.json", new_items, previous_count=42)
    assert ok is False
    saved = json.loads((out / "helmets.json").read_text(encoding="utf-8"))
    assert len(saved) == 42


def test_safe_save_force_overrides_drop_guard(tmp_path, monkeypatch):
    out = tmp_path / "data"
    bak = tmp_path / "data_backup"
    out.mkdir()
    old_items = [{"name": f"Item {i}"} for i in range(42)]
    (out / "helmets.json").write_text(json.dumps(old_items), encoding="utf-8")
    monkeypatch.setattr(storage, "OUTPUT_DIR", out)
    monkeypatch.setattr(storage, "BACKUP_DIR", bak)

    new_items = [{"name": "AMP-1 TP LC (Ranger Green)"}]
    ok = scrape.safe_save("helmets.json", new_items, previous_count=42, force=True)
    assert ok is True
    saved = json.loads((out / "helmets.json").read_text(encoding="utf-8"))
    assert len(saved) == 1


def test_safe_save_accepts_normal_growth(tmp_path, monkeypatch):
    out = tmp_path / "data"
    bak = tmp_path / "data_backup"
    out.mkdir()
    old_items = [{"name": f"Item {i}"} for i in range(10)]
    (out / "test.json").write_text(json.dumps(old_items), encoding="utf-8")
    monkeypatch.setattr(storage, "OUTPUT_DIR", out)
    monkeypatch.setattr(storage, "BACKUP_DIR", bak)

    new_items = [{"name": f"Item {i}"} for i in range(12)]
    ok = scrape.safe_save("test.json", new_items, previous_count=10)
    assert ok is True
    saved = json.loads((out / "test.json").read_text(encoding="utf-8"))
    assert len(saved) == 12


def test_safe_save_preserves_partial_field_once_then_drops_stale_field(tmp_path, monkeypatch):
    out = tmp_path / "data"
    bak = tmp_path / "data_backup"
    out.mkdir()
    (out / "items.json").write_text(
        json.dumps([{"name": "Item 1", "id": "item-1", "legacy_field": "old"}]),
        encoding="utf-8",
    )
    monkeypatch.setattr(storage, "OUTPUT_DIR", out)
    monkeypatch.setattr(storage, "BACKUP_DIR", bak)

    first = [{"name": "Item 1", "id": "item-1"}]
    assert scrape.safe_save("items.json", first, previous_count=1) is True
    assert json.loads((out / "items.json").read_text())[0]["legacy_field"] == "old"

    second = [{"name": "Item 1", "id": "item-1"}]
    assert scrape.safe_save("items.json", second, previous_count=1) is True
    assert "legacy_field" not in json.loads((out / "items.json").read_text())[0]
    state = json.loads((out / scrape.FIELD_PRESERVATION_FILENAME).read_text())
    assert state["items.json"]["Item 1"] == {}


def test_safe_save_does_not_preserve_low_coverage_field(tmp_path, monkeypatch):
    out = tmp_path / "data"
    bak = tmp_path / "data_backup"
    out.mkdir()
    old = [
        {"name": "Item 1", "id": "item-1", "rare": "x"},
        {"name": "Item 2", "id": "item-2"},
        {"name": "Item 3", "id": "item-3"},
    ]
    (out / "items.json").write_text(json.dumps(old), encoding="utf-8")
    monkeypatch.setattr(storage, "OUTPUT_DIR", out)
    monkeypatch.setattr(storage, "BACKUP_DIR", bak)

    new = [{"name": "Item 1", "id": "item-1"}]
    assert scrape.safe_save("items.json", new, previous_count=3, force=True) is True
    assert "rare" not in json.loads((out / "items.json").read_text())[0]


def test_write_scrape_metadata(tmp_path, monkeypatch):
    out = tmp_path / "data"
    out.mkdir()
    (out / "vests.json").write_text(json.dumps([{"name": "B Krogan", "id": "b-krogan"}]), encoding="utf-8")
    monkeypatch.setattr(storage, "OUTPUT_DIR", out)
    monkeypatch.setattr(pipeline, "DATASET_SOURCE_REVISIONS", {"vests": {"B Krogan": 18815}})

    scrape.write_scrape_metadata({"vests"}, full_scrape=True)

    metadata = json.loads((out / scrape.METADATA_FILENAME).read_text(encoding="utf-8"))
    assert metadata["source"] == "gzw-scraper"
    assert metadata["lastScrapedAt"].endswith("Z")
    assert metadata["lastFullScrapeAt"] == metadata["lastScrapedAt"]
    vest = metadata["datasets"][0]
    assert vest["lastScrapedAt"] == metadata["lastScrapedAt"]
    assert vest["sourcePages"] == [{"title": "B Krogan", "revisionId": 18815}]


def test_parse_page_records_mediawiki_revision_id(monkeypatch):
    payload = {"parse": {"title": "B Krogan", "pageid": 5647, "revid": 18815, "text": "<p>item</p>"}}
    monkeypatch.setattr(network, "api_call", lambda _params: payload)
    monkeypatch.setattr(network, "PAGE_REVISIONS", {}, raising=False)

    page = network.parse_page("B Krogan")

    assert page is not None
    assert network.PAGE_REVISIONS["B Krogan"] == 18815


def test_scrape_category_skips_configured_plural_index_title(monkeypatch):
    pages = [{"title": "Armor Vests"}, {"title": "B Krogan"}]
    monkeypatch.setattr(network, "get_category_members", lambda _category, **_kwargs: pages)
    monkeypatch.setattr(network, "parse_page", lambda _title: soup_of("<html></html>"))
    monkeypatch.setattr(pipeline.parsing, "parse_infobox", lambda _soup: {"type": "Vest"})
    monkeypatch.setattr(network, "get_page_image", lambda *_args: None)
    monkeypatch.setattr(pipeline, "CATEGORY_INDEX_PAGE_TITLES", {"Armor Vest": "Armor Vests"}, raising=False)
    monkeypatch.setattr(pipeline, "PAGE_DELAY", 0)

    items = pipeline.scrape_category("Armor_Vest", "Armor Vest")

    assert [item["name"] for item in items] == ["B Krogan"]


def test_category_pages_are_kept_when_listing_tables_enrich_same_dataset(monkeypatch):
    items = [{"name": "B Krogan", "id": "b-krogan"}]
    monkeypatch.setattr(pipeline, "scrape_category", lambda name, title: items)

    result = pipeline.scrape_single_category_task(
        {"name": "Armor_Vest", "title": "Armor Vest", "pages": 19},
        {},
    )

    assert result == ("vests.json", items)


def test_provisions_category_is_skipped_in_favor_of_its_listing_table(monkeypatch):
    def fail_if_called(_name, _title):
        raise AssertionError("listing-only category page must be skipped")

    monkeypatch.setattr(pipeline, "scrape_category", fail_if_called)

    result = pipeline.scrape_single_category_task(
        {"name": "Provisions", "title": "Provisions", "pages": 1},
        {},
    )

    assert result is None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
