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


def test_extract_page_sections_returns_requested_text_fields():
    html = """
    <div class="mw-parser-output">
      <h2><span class="mw-headline">Description</span><span class="mw-editsection">[edit]</span></h2>
      <p>First line of description.</p><ul><li>Second detail.</li></ul>
      <h2><span class="mw-headline">Transcript</span><span class="mw-editsection">[edit]</span></h2>
      <ul><li class="wds-tabs__tab">Page 1</li></ul>
      <p>Newspaper transcript body.</p><p>Newspaper transcript body.</p>
      <h2><span class="mw-headline">Location</span></h2>
      <p>Midnight Sapphire.</p>
    </div>
    """
    result = pipeline.parsing.extract_page_sections(
        soup_of(html), {"description": "Description", "transcript": "Transcript"}
    )
    assert result == {
        "description": "First line of description. Second detail.",
        "transcript": "Newspaper transcript body.",
    }


def test_config_maps_wiki_content_categories_to_datasets():
    cfg = scrape.load_config()
    assert cfg["category_to_filename"]["Regions"] == "regions"
    assert cfg["category_to_filename"]["Factions"] == "factions"
    assert cfg["category_to_filename"]["Newspaper"] == "newspapers"
    assert cfg["content_sections"]["newspapers"]["transcript"] == "Transcript"
    assert {"grid_size", "weight", "sold_by"} <= set(cfg["listing_page_fields"]["plate_carriers"])
    assert cfg["supplemental_pages"]["plate_carriers"] == ["LCS Sentry (UNLRA)"]
    assert cfg["supplemental_pages"]["bosses"] == [
        "Bloodhound", "Broker", "Chaonai", "Firebrand", "Grin", "Kaptei", "Viper", "Yen"
    ]


def test_scrape_supplemental_pages_records_fields_and_revision(monkeypatch):
    monkeypatch.setattr(pipeline, "SUPPLEMENTAL_PAGES", {"bosses": ["Yen"]})
    monkeypatch.setattr(pipeline, "CONTENT_SECTIONS", {"bosses": {"biography": "Biography"}})
    monkeypatch.setattr(pipeline, "PAGE_DELAY", 0)
    monkeypatch.setattr(pipeline, "DATASET_SOURCE_REVISIONS", {})
    monkeypatch.setattr(network, "PAGE_REVISIONS", {"Yen": 18815}, raising=False)
    monkeypatch.setattr(network, "parse_page", lambda _title: soup_of(
        '<div class="mw-parser-output"><h2><span class="mw-headline">Biography</span></h2>'
        '<p>Boss biography.</p></div>'
    ))
    monkeypatch.setattr(pipeline.parsing, "parse_infobox", lambda _soup: {"faction": "LAF"})
    monkeypatch.setattr(network, "get_page_image", lambda *_args, **_kwargs: None)

    result = pipeline.scrape_supplemental_pages()

    assert result["bosses.json"][0]["faction"] == "LAF"
    assert result["bosses.json"][0]["biography"] == "Boss biography."
    assert pipeline.DATASET_SOURCE_REVISIONS["bosses"] == {"Yen": 18815}


def test_plate_carrier_listing_maps_grid_weight_and_source_for_frontend(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda _title: soup_of(LISTING_ARMOR_RATINGS_HTML))

    items = pipeline.scrape_listing_page(
        "plate_carriers",
        "Plate Carriers",
        fields={
            "protection_rating", "nij_class", "material", "armor_locations", "image",
            "grid_size", "weight", "sold_by",
        },
    )

    buran = next(item for item in items if item["name"] == "Buran's Pantsir 2.0")
    assert buran["protection_rating"] == "R3"
    assert buran["nij_class"] == "III++"
    assert buran["grid_size"] == "3x3"
    assert buran["weight"] == "9.600kg"
    assert buran["sold_by"] == "Looting"
    assert buran["image"].endswith("Buran%27s_Pantsir_2.0_icon.png/revision/latest/scale-to-width-down/180?cb=20260520233024")


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
  <tr><th>Icon</th><th>Name</th><th>NIJ</th><th>Material</th><th>Plates</th><th>Grid</th><th>Weight</th><th>Source</th></tr>
  <tr>
    <td><img src="https://static.wikia.nocookie.net/gzw/images/press_icon.png" /></td>
    <td><a href="/wiki/Covert_Tactical_Body_Armour_(Press)">Covert Tactical Body Armour (Woodland)</a></td>
    <td>P3 / IIIA</td><td>Aramid</td><td>Front, Back</td><td>3x3</td><td>9.500 kg</td><td>Handshake R.2</td>
  </tr>
  <tr>
    <td><img src="https://static.wikia.nocookie.net/gray-zone-warfare/images/5/5f/Buran%27s_Pantsir_2.0_icon.png/revision/latest/scale-to-width-down/180?cb=20260520233024" /></td>
    <td><a href="/wiki/Buran%27s_Pantsir_2.0">Buran's Pantsir 2.0</a></td>
    <td>R3 / III++</td><td>Ceramic</td><td>Front, Back, Sides</td><td>3x3</td><td>9.600kg</td><td>Looting</td>
  </tr>
  <tr>
    <td><img src="https://static.wikia.nocookie.net/gzw/images/m2_icon.png" /></td>
    <td><a href="/wiki/M2_Plate_Carrier">M2 Plate Carrier</a></td>
    <td>R3 / III++</td><td>Ceramic</td><td>Front, Back</td><td>3x4</td><td>10.000kg</td><td>Gunny R.3</td>
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


def test_scrape_listing_page_restores_missing_image_from_listing(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda _title: soup_of(LISTING_ARMOR_RATINGS_HTML))
    existing = [{"name": "Buran's Pantsir 2.0", "id": "burans-pantsir-2.0"}]

    listing_items = scrape.scrape_listing_page(
        "plate_carriers", "Plate Carriers", existing_items=existing, fields={"image"}
    )
    items = storage.merge_items(existing, listing_items)

    buran = next(item for item in items if item["name"] == "Buran's Pantsir 2.0")
    assert buran["image"] == "https://static.wikia.nocookie.net/gray-zone-warfare/images/5/5f/Buran%27s_Pantsir_2.0_icon.png/revision/latest/scale-to-width-down/180?cb=20260520233024"


def test_listing_page_configures_vest_and_plate_carrier_rating_fields():
    cfg = scrape.load_config()
    assert cfg["listing_pages"]["vests"] == "Armor Vests"
    assert cfg["listing_pages"]["plate_carriers"] == "Plate Carriers"
    assert cfg["listing_page_fields"]["vests"] == [
        "protection_rating", "nij_class", "material", "armor_locations", "image"
    ]
    assert cfg["listing_page_fields"]["plate_carriers"] == [
        "protection_rating", "nij_class", "material", "armor_locations", "image",
        "grid_size", "weight", "sold_by",
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
        {"*": "Regions", "size": 12},
        {"*": "Factions", "size": 6},
        {"*": "Newspaper", "size": 44},
        {"*": "Crafting", "size": 8},
        {"*": "Hideout", "size": 5},
    ]
    kept = scrape.filter_game_categories(cats)
    titles = {k["title"] for k in kept}
    for expected in ("Weapons", "Tasks", "Keys", "Tech", "Task",
                     "Tiger Bay", "Squad Strike Missions", "Loot", "Apparel",
                     "Regions", "Factions", "Newspaper", "Crafting", "Hideout"):
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


def test_filtered_category_report_lists_nonempty_skipped_categories():
    all_categories = [
        {"*": "Crafting", "size": 5},
        {"*": "Systems", "size": 12},
        {"*": "Empty Category", "size": 0},
    ]
    included = [{"title": "Crafting", "name": "Crafting", "pages": 5}]

    assert pipeline.get_filtered_categories(all_categories, included) == [
        {"title": "Systems", "pages": 12}
    ]


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


def test_task_category_source_revisions_are_attributed_to_split_dataset(monkeypatch):
    monkeypatch.setattr(network, "get_category_members", lambda *_args, **_kwargs: [{"title": "Task Alpha"}])
    monkeypatch.setattr(network, "parse_page", lambda _title: bs4.BeautifulSoup("<html><body></body></html>", "html.parser"))
    monkeypatch.setattr(pipeline.parsing, "parse_infobox", lambda _soup: {})
    monkeypatch.setattr(network, "get_page_image", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(network, "PAGE_REVISIONS", {"Task Alpha": 123}, raising=False)
    monkeypatch.setattr(pipeline, "DATASET_SOURCE_REVISIONS", {})
    monkeypatch.setattr(pipeline.time, "sleep", lambda _seconds: None)

    pipeline.scrape_category("Main task", "Main task")

    assert pipeline.DATASET_SOURCE_REVISIONS["main_task"] == {"Task Alpha": 123}


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


# ─── whole-page sections and uncovered-page report ───


def test_extract_page_sections_whole_page_wildcard():
    from gzw_scraper import parsing

    soup = bs4.BeautifulSoup(
        "<div class='mw-parser-output'><aside><p>infobox noise</p></aside>"
        "<p>Intro text.</p><h2><span class='mw-headline'>Part</span></h2>"
        "<p>Body text.</p></div>",
        "html.parser",
    )
    out = parsing.extract_page_sections(soup, {"content": "*"})
    assert out == {"content": "Intro text. Body text."}


def test_find_uncovered_pages_ignores_covered_and_skipped(monkeypatch):
    monkeypatch.setattr(pipeline, "SKIP_PAGES", {"Intels"})
    result = pipeline.find_uncovered_pages(
        ["Hideout", "AK-74", "Intels", "New Page"],
        {"weapons.json": [{"name": "ak-74"}], "hideout.json": [{"name": "Hideout"}]},
    )
    assert result == ["New Page"]


def test_get_all_pages_follows_continuation(monkeypatch):
    responses = iter([
        {"query": {"allpages": [{"title": "A"}]}, "continue": {"apcontinue": "B"}},
        {"query": {"allpages": [{"title": "B"}]}},
    ])
    monkeypatch.setattr(network, "api_call", lambda params, **kw: next(responses))
    assert network.get_all_pages() == ["A", "B"]


def test_get_all_pages_returns_none_on_api_failure(monkeypatch):
    monkeypatch.setattr(network, "api_call", lambda params, **kw: None)
    assert network.get_all_pages() is None


# ─── hideout tables ───

HIDEOUT_HTML = """
<div class="mw-parser-output">
<h2><span class="mw-headline">Rooms</span></h2>
<h3><span class="mw-headline">Laboratory</span></h3>
<table>
<tr><th colspan="4">Heat Source</th></tr>
<tr><th>Level</th><th>Requirements</th><th>Function</th></tr>
<tr><th>1</th><td><ul><li>3 <a title="Steel">Steel</a></li><li>1 <a title="Wood">Wood</a></li></ul></td><td><ul><li>+ x Heat control</li></ul></td></tr>
<tr><th>2</th><td><ul><li>4 <a title="Steel">Steel</a></li></ul></td><td><ul><li>+ x Heat control</li></ul></td></tr>
<tr><th>2</th><td><ul><li>10 <a title="Steel">Steel</a></li><li>1 $40,000 <a title="Dollars">Dollars</a></li></ul></td><td><ul><li>+ 2 Heat control</li></ul></td></tr>
</table>
<h3><span class="mw-headline">Electronics Workshop</span></h3>
<table>
<tr><th colspan="4">Heat Source</th></tr>
<tr><th>Level</th><th>Requirements</th><th>Function</th></tr>
<tr><th>1</th><td><ul><li>2 <a title="Copper">Copper</a></li></ul></td><td><ul><li>+ x Power</li></ul></td></tr>
</table>
<h2><span class="mw-headline">Disassembly recipes</span></h2>
<table>
<tr><th colspan="6">Recipes</th></tr>
<tr><th colspan="2">Disassembled item</th><th>Duration</th><th colspan="2">Outcome</th></tr>
<tr>
<td><a title="File:Radio icon.png">File:Radio icon.png</a><br/><a title="Manpack Tactical Radio">Manpack Tactical Radio</a></td>
<th>→</th><th>12 hours</th><th>→</th>
<td><a title="Encryption Key Drive">Encryption Key Drive</a> + <a title="FPGA Module">FPGA Module</a></td>
</tr>
</table>
</div>
"""


def test_parse_hideout_modules_reads_levels_requirements_and_rooms(caplog):
    from gzw_scraper import parsing

    soup = bs4.BeautifulSoup(HIDEOUT_HTML, "html.parser")
    modules = parsing.parse_hideout_modules(soup)

    assert [m["name"] for m in modules] == [
        "Heat Source Level 1 (Laboratory)",
        "Heat Source Level 2 (Laboratory)",
        "Heat Source Level 3 (Laboratory)",
        "Heat Source Level 1 (Electronics Workshop)",
    ]
    assert len({m["id"] for m in modules}) == 4
    assert modules[0]["requirements"] == [{"name": "Steel", "quantity": 3}, {"name": "Wood", "quantity": 1}]
    assert modules[0]["requirements_text"] == "3 Steel, 1 Wood"
    # A repeated level number on the wiki is treated as the next level, and money becomes Dollars.
    assert modules[2]["level"] == 3
    assert {"name": "Dollars", "quantity": 40000} in modules[2]["requirements"]


def test_parse_disassembly_recipes_ignores_file_links():
    from gzw_scraper import parsing

    soup = bs4.BeautifulSoup(HIDEOUT_HTML, "html.parser")
    assert parsing.parse_disassembly_recipes(soup) == [{
        "name": "Manpack Tactical Radio",
        "id": "manpack-tactical-radio",
        "duration": "12 hours",
        "outcome": ["Encryption Key Drive", "FPGA Module"],
        "outcome_text": "Encryption Key Drive, FPGA Module",
    }]


def test_hideout_parsers_return_nothing_for_other_pages():
    from gzw_scraper import parsing

    soup = bs4.BeautifulSoup("<div class='mw-parser-output'><p>Nothing here.</p></div>", "html.parser")
    assert parsing.parse_hideout_modules(soup) == []
    assert parsing.parse_disassembly_recipes(soup) == []
    assert parsing.parse_hideout_modules(None) == []


def test_scrape_hideout_tables_skips_when_page_unavailable(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda title: None)
    assert pipeline.scrape_hideout_tables() == {}


def test_scrape_hideout_tables_returns_both_datasets(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda title: bs4.BeautifulSoup(HIDEOUT_HTML, "html.parser"))
    result = pipeline.scrape_hideout_tables()
    assert set(result) == {"hideout_modules.json", "disassembly_recipes.json"}
    assert len(result["hideout_modules.json"]) == 4


# ─── experience tables ───

EXPERIENCE_HTML = """
<div class="mw-parser-output">
<table>
<tr><th colspan="2">Lamang enemies kill EXP</th></tr>
<tr><th>Type</th><th>Experience</th></tr>
<tr><td>LAF Commanders</td><td>30</td></tr>
<tr><td>Other PMCs (Players)</td><td>200</td></tr>
</table>
<table>
<tr><th colspan="2">Discovering POIs</th></tr>
<tr><th>Type</th><th>Experience</th></tr>
<tr><td>Points of Interest</td><td>30</td></tr>
<tr><td>LAF Commanders</td><td>999</td></tr>
</table>
<table>
<tr><th>Icon</th><th>Level</th><th>Level Threshold</th><th>Rewards</th></tr>
<tr><td rowspan="2"></td><td>1</td><td>0</td><td><a title="Tactical Gloves (Blue)">Tactical Gloves (Blue)</a></td></tr>
<tr><td>2</td><td>600</td><td></td></tr>
<tr><td></td><td>3</td><td>1,500</td><td><a title="File:Shirt.png">x</a><a title="Tactical T-shirt (Green)">Tactical T-shirt (Green)</a></td></tr>
</table>
</div>
"""


def test_parse_experience_actions_reads_values_and_keeps_duplicate_labels():
    from gzw_scraper import parsing

    actions = parsing.parse_experience_actions(bs4.BeautifulSoup(EXPERIENCE_HTML, "html.parser"))

    assert [(a["name"], a["experience"]) for a in actions] == [
        ("LAF Commanders", 30),
        ("Other PMCs (Players)", 200),
        ("Points of Interest", 30),
        ("LAF Commanders (Discovering POIs)", 999),
    ]
    assert actions[0]["category"] == "Lamang enemies kill EXP"
    assert len({a["id"] for a in actions}) == 4


def test_parse_experience_levels_handles_rowspan_icon_column():
    from gzw_scraper import parsing

    levels = parsing.parse_experience_levels(bs4.BeautifulSoup(EXPERIENCE_HTML, "html.parser"))

    assert [(row["level"], row["threshold"]) for row in levels] == [(1, 0), (2, 600), (3, 1500)]
    assert levels[0]["rewards"] == ["Tactical Gloves (Blue)"]
    assert levels[1]["rewards"] == []
    assert levels[2]["rewards"] == ["Tactical T-shirt (Green)"]


def test_experience_parsers_ignore_other_pages():
    from gzw_scraper import parsing

    soup = bs4.BeautifulSoup("<div class='mw-parser-output'><p>No tables.</p></div>", "html.parser")
    assert parsing.parse_experience_actions(soup) == []
    assert parsing.parse_experience_levels(soup) == []
    assert parsing.parse_experience_levels(None) == []


def test_scrape_experience_tables_skips_missing_parts(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda title: None)
    assert pipeline.scrape_experience_tables() == {}

    only_levels = "<div class='mw-parser-output'>" + EXPERIENCE_HTML.split("</table>", 2)[2] + "</div>"
    monkeypatch.setattr(network, "parse_page", lambda title: bs4.BeautifulSoup(only_levels, "html.parser"))
    assert set(pipeline.scrape_experience_tables()) == {"experience_levels.json"}


# ─── trading vendors and vendor stock ───

TRADING_HTML = """
<div class="mw-parser-output">
<table>
<tr><th>Nickname</th><th>Name</th><th>Image</th><th>Sells</th><th>Buys</th></tr>
<tr><td>Gunny</td><td>Anton Jackson</td><td></td><td>Weapons , Ammunition ,</td><td>Weapons and Tools.</td></tr>
<tr><td>Vulture</td><td>Unknown</td><td></td><td>Any.</td><td>Valuables</td></tr>
</table>
</div>
"""


def test_parse_trading_vendors_cleans_list_text():
    from gzw_scraper import parsing

    vendors = parsing.parse_trading_vendors(bs4.BeautifulSoup(TRADING_HTML, "html.parser"))

    assert vendors == [
        {"name": "Gunny", "id": "gunny", "sells": "Weapons, Ammunition,", "buys": "Weapons and Tools."},
        {"name": "Vulture", "id": "vulture", "sells": "Any.", "buys": "Valuables"},
    ]
    assert parsing.parse_trading_vendors(None) == []
    assert parsing.parse_trading_vendors(bs4.BeautifulSoup("<p>none</p>", "html.parser")) == []


def test_build_vendor_stock_splits_sold_by_and_skips_non_vendors():
    datasets = {
        "weapons.json": [
            {"name": "AK-12", "sold_by": "Turncoat R.2", "type": "Assault Rifle"},
            {"name": "M4", "sold_by": "Gunny R.1, Artisan R.3"},
            {"name": "Found Rifle", "sold_by": "Loot only"},
            {"name": "Mystery", "sold_by": "???"},
            {"name": "Looted", "sold_by": "Looting"},
        ],
        "medical.json": [{"name": "Bandage", "sold_by": "Lab Rat", "buy_price": "$50"}],
        "backpacks.json": [{"name": "Pack", "sold_by": "Handshake R.1"}],
        "containers.json": [{"name": "Pack", "sold_by": "Handshake R.4"}],
        "upcoming_content.json": [{"name": "Future Gun", "sold_by": "Gunny R.1"}],
    }

    rows = pipeline.build_vendor_stock(datasets, {"Gunny", "Artisan", "Turncoat", "Lab Rat", "Handshake"})
    by_name = {row["name"]: row for row in rows}

    assert set(by_name) == {"AK-12 (Turncoat)", "M4 (Gunny)", "M4 (Artisan)", "Bandage (Lab Rat)", "Pack (Handshake)"}
    assert by_name["AK-12 (Turncoat)"]["rank"] == 2 and by_name["AK-12 (Turncoat)"]["type"] == "Assault Rifle"
    assert by_name["M4 (Artisan)"]["rank"] == 3
    assert by_name["Bandage (Lab Rat)"]["rank"] is None and by_name["Bandage (Lab Rat)"]["buy_price"] == "$50"
    # The same item in two datasets is kept once, from the first dataset alphabetically.
    assert by_name["Pack (Handshake)"]["category"] == "backpacks" and by_name["Pack (Handshake)"]["rank"] == 1
    assert len({row["id"] for row in rows}) == len(rows)


def test_build_vendor_stock_ignores_unknown_vendor_labels():
    datasets = {"weapons.json": [{"name": "Rifle", "sold_by": "Some Label R.1"}]}
    assert pipeline.build_vendor_stock(datasets, {"Gunny"}) == []
    assert len(pipeline.build_vendor_stock(datasets, None)) == 1


def test_scrape_trading_vendors_handles_missing_page(monkeypatch):
    monkeypatch.setattr(network, "parse_page", lambda title: None)
    assert pipeline.scrape_trading_vendors() == []
    monkeypatch.setattr(network, "parse_page", lambda title: bs4.BeautifulSoup(TRADING_HTML, "html.parser"))
    assert [vendor["name"] for vendor in pipeline.scrape_trading_vendors()] == ["Gunny", "Vulture"]
