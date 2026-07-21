"""
Regression tests for parse_infobox and scrape_listing_page.

These two functions are the scraper's most fragile points because they
rely on Fandom wiki HTML structure (portable-infobox / wikitable).
If Fandom changes their templates, these tests will break first —
before production data silently degrades.

Run: pytest test_scrape.py -v
"""

import bs4
import pytest

import scrape


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


def test_scrape_listing_page_extracts_rows(monkeypatch):
    monkeypatch.setattr(scrape, "parse_page", lambda title: soup_of(LISTING_TABLE_HTML))
    items = scrape.scrape_listing_page("medical", "Medical Items")

    names = {item["name"] for item in items}
    assert names == {"Bandage", "Splint"}

    bandage = next(i for i in items if i["name"] == "Bandage")
    assert bandage["id"] == "bandage"
    assert bandage["type"] == "Medical"
    assert bandage["image"] == "https://static.wikia.nocookie.net/gzw/images/bandage.png"


def test_scrape_listing_page_skips_tables_without_name_or_icon(monkeypatch):
    monkeypatch.setattr(
        scrape, "parse_page", lambda title: soup_of(LISTING_TABLE_HTML_NO_NAME_OR_ICON_COLUMN)
    )
    items = scrape.scrape_listing_page("misc", "Misc Page")
    assert items == []


def test_scrape_listing_page_skips_tables_with_only_header_row(monkeypatch):
    monkeypatch.setattr(scrape, "parse_page", lambda title: soup_of(LISTING_TABLE_HTML_EMPTY_TABLE))
    items = scrape.scrape_listing_page("misc", "Misc Page")
    assert items == []


def test_scrape_listing_page_returns_empty_when_page_fails_to_parse(monkeypatch):
    # parse_page returns None when the API call/HTML parse fails upstream.
    monkeypatch.setattr(scrape, "parse_page", lambda title: None)
    items = scrape.scrape_listing_page("medical", "Medical Items")
    assert items == []


def test_scrape_listing_page_falls_back_to_data_src_for_lazy_loaded_images(monkeypatch):
    monkeypatch.setattr(
        scrape, "parse_page", lambda title: soup_of(LISTING_TABLE_HTML_DATA_SRC_FALLBACK)
    )
    items = scrape.scrape_listing_page("medical", "Medical Items")
    assert len(items) == 1
    assert items[0]["image"] == "https://static.wikia.nocookie.net/gzw/images/real.png"


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
