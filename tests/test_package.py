"""
Seam tests for the ``gzw_scraper`` package modules.

Covers the boundaries the facade regression tests in ``test_scrape.py``
do not: config loading/merging, network throttling and API pagination,
item merging, output-filename mapping, CLI flag handling and the facade
re-export contract itself.
"""

from __future__ import annotations

import re
from pathlib import Path

import bs4
import pytest

import gzw_scraper
import scrape
from gzw_scraper import cli, config, network, parsing, pipeline, storage

# ─── config ───

def test_default_config_keeps_removed_and_upcoming_content_scrapable():
    skip = set(config.DEFAULT_CONFIG["skip_categories"]["infrastructure"])
    assert "Removed Content" not in skip
    assert "Upcoming Content" not in skip


def test_load_config_merges_real_config_file_over_defaults():
    cfg = config.load_config(config.CONFIG_PATH)
    assert cfg["category_to_filename"]["Removed Content"] == "removed_content"
    assert cfg["category_to_filename"]["Upcoming Content"] == "upcoming_content"
    # Entries that exist only in config.toml, not in the built-in defaults
    assert cfg["category_to_filename"]["Tiger Bay"] == "tasks"
    assert "Intels" in cfg["skip_pages"]["titles"]


def test_load_config_falls_back_to_defaults_when_file_missing(tmp_path):
    cfg = config.load_config(tmp_path / "missing.toml")
    assert cfg["wiki"]["api_url"] == "https://gray-zone-warfare.fandom.com/api.php"


def test_deep_merge_prefers_override_and_keeps_base_keys():
    merged = config._deep_merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}})
    assert merged == {"a": {"x": 1, "y": 3}, "b": 1}


def test_apply_settings_rebinds_every_consumer_module(tmp_path):
    custom = tmp_path / "custom.toml"
    custom.write_text(
        '[output]\ndirectory = "custom_data"\nbackup_directory = "custom_backup"\n'
        '[skip_pages]\ntitles = ["Custom Skip"]\n',
        encoding="utf-8",
    )
    try:
        config.apply_settings(custom)
        assert storage.OUTPUT_DIR.name == "custom_data"
        assert storage.BACKUP_DIR.name == "custom_backup"
        assert "Custom Skip" in pipeline.SKIP_PAGES
    finally:
        config.apply_settings()

    assert storage.OUTPUT_DIR == config.OUTPUT_DIR
    assert "Custom Skip" not in pipeline.SKIP_PAGES


# ─── network ───

def test_throttle_request_sleeps_between_immediate_calls(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(network, "_last_request_time", 0.0)
    monkeypatch.setattr(network, "REQUEST_INTERVAL", 0.05)
    monkeypatch.setattr(network.time, "sleep", lambda seconds: sleeps.append(seconds))

    network.throttle_request()
    network.throttle_request()

    assert len(sleeps) == 1
    assert sleeps[0] > 0


def test_get_category_members_follows_api_continuation(monkeypatch):
    responses = [
        {"query": {"categorymembers": [{"title": "A"}]}, "continue": {"cmcontinue": "next"}},
        {"query": {"categorymembers": [{"title": "B"}]}},
    ]
    seen: list[dict] = []

    def fake_api_call(params):
        seen.append(dict(params))
        return responses[len(seen) - 1]

    monkeypatch.setattr(network, "api_call", fake_api_call)

    pages = network.get_category_members("Weapons")

    assert [page["title"] for page in pages] == ["A", "B"]
    assert seen[0]["cmtitle"] == "Category:Weapons"
    assert seen[1]["cmcontinue"] == "next"


def test_api_call_backs_off_on_rate_limit_then_gives_up(monkeypatch):
    import requests

    sleeps: list[float] = []

    class FakeResponse:
        status_code = 429

        def raise_for_status(self) -> None:
            raise requests.exceptions.HTTPError("429 Too Many Requests", response=self)

    attempts: list[str] = []

    def fake_get(url, params=None, headers=None, timeout=None):
        attempts.append(url)
        return FakeResponse()

    monkeypatch.setattr(network.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(network, "throttle_request", lambda: None)
    monkeypatch.setattr(network.requests, "get", fake_get)

    result = network.api_call({"action": "query"}, max_retries=2)

    assert result is None
    assert len(attempts) == 2
    # 429 backoff is long and jittered: 2**attempt * 10 seconds (plus jitter)
    assert any(wait >= 10 for wait in sleeps)
    # No pointless sleep after the final attempt: one wait between two attempts
    assert len(sleeps) == 1


def test_api_call_honours_retry_after_and_caps_it(monkeypatch, caplog):
    import requests

    sleeps: list[float] = []

    class FakeResponse:
        status_code = 429

        def __init__(self, retry_after: str) -> None:
            self.headers = {"Retry-After": retry_after}

        def raise_for_status(self) -> None:
            raise requests.exceptions.HTTPError("429 Too Many Requests", response=self)

    responses = iter([FakeResponse("7"), FakeResponse("9999"), FakeResponse("soon")])
    monkeypatch.setattr(network.time, "sleep", lambda seconds: sleeps.append(seconds))
    monkeypatch.setattr(network, "throttle_request", lambda: None)
    monkeypatch.setattr(network.requests, "get", lambda *a, **k: next(responses))

    with caplog.at_level("ERROR", logger="gzw-scraper"):
        assert network.api_call({"action": "query"}, max_retries=3) is None

    assert sleeps[0] == 7.0
    assert sleeps[1] == network.MAX_RETRY_AFTER
    # The final failure is reported with its reason instead of an empty message
    assert "HTTP 429" in caplog.text


def test_retry_after_seconds_ignores_missing_and_invalid_values():
    class Resp:
        def __init__(self, headers):
            self.headers = headers

    assert network.retry_after_seconds(None) is None
    assert network.retry_after_seconds(Resp({})) is None
    assert network.retry_after_seconds(Resp({"Retry-After": "0"})) is None
    assert network.retry_after_seconds(Resp({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    assert network.retry_after_seconds(Resp({"Retry-After": "3"})) == 3.0


# ─── version ───

ROOT = Path(__file__).resolve().parent.parent


def test_version_has_a_single_source_and_docs_match():
    version = gzw_scraper.__version__
    assert re.fullmatch(r"\d+\.\d+\.\d+", version)
    assert 'dynamic = ["version"]' in (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f"GZW Wiki Scraper v{version}" in (ROOT / "scrape.py").read_text(encoding="utf-8")
    assert f"- **Version:** `{version}`" in (ROOT / "ROADMAP.md").read_text(encoding="utf-8")
    assert f"**v{version} " in (ROOT / "README.md").read_text(encoding="utf-8")


def test_user_agent_version_placeholder_is_replaced():
    assert "{version}" in (ROOT / "config.toml").read_text(encoding="utf-8")
    assert gzw_scraper.__version__ in network.HEADERS["User-Agent"]
    assert "{version}" not in network.HEADERS["User-Agent"]


# ─── parsing ───

def test_sanitize_value_collapses_duplicated_units_and_percent_signs():
    assert parsing.sanitize_value("0.01  kgkg") == "0.01 kg"
    assert parsing.sanitize_value("+3% %") == "+3%"


# ─── storage ───

def test_merge_items_dedupes_by_name_and_merges_fields():
    existing = [{"name": "AK-74", "id": "ak-74", "caliber": "5.45x39mm"}]
    incoming = [{"name": "AK-74", "image": "ak74.png"}, {"name": "M4", "id": "m4"}]

    merged = storage.merge_items(existing, incoming)

    by_name = {item["name"]: item for item in merged}
    assert len(merged) == 2
    assert by_name["AK-74"]["caliber"] == "5.45x39mm"  # existing field kept
    assert by_name["AK-74"]["image"] == "ak74.png"  # incoming field added
    assert by_name["M4"]["id"] == "m4"


def test_merge_items_ignores_nameless_items():
    assert storage.merge_items([], [{"id": "no-name"}]) == []


# ─── pipeline ───

def test_get_output_filename_maps_known_and_unknown_categories():
    assert pipeline.get_output_filename("Helmet") == "helmets.json"
    assert pipeline.get_output_filename("Removed Content") == "removed_content.json"
    assert pipeline.get_output_filename("Upcoming Content") == "upcoming_content.json"
    assert pipeline.get_output_filename("Crafting") == "crafting.json"
    assert pipeline.get_output_filename("Hideout") == "hideout.json"
    assert pipeline.get_output_filename("Some Brand New Category") == "some_brand_new_category.json"


INFOBOX_ONLY_PAGE = """
<aside class="portable-infobox">
  <div class="pi-data">
    <h3 class="pi-data-label">Caliber</h3>
    <div class="pi-data-value">5.45x39mm</div>
  </div>
  <img src="https://static.wikia.nocookie.net/gzw/images/ak74.png" />
</aside>
"""


def test_scrape_category_builds_items_from_infobox(monkeypatch):
    monkeypatch.setattr(network, "get_category_members", lambda name, limit: [{"title": "AK-74"}])
    monkeypatch.setattr(network, "parse_page", lambda title: bs4.BeautifulSoup(INFOBOX_ONLY_PAGE, "lxml"))
    monkeypatch.setattr(pipeline, "PAGE_DELAY", 0)

    items = pipeline.scrape_category("Weapons", "Weapons")

    assert items == [
        {
            "name": "AK-74",
            "id": "ak-74",
            "caliber": "5.45x39mm",
            "image": "https://static.wikia.nocookie.net/gzw/images/ak74.png",
        }
    ]


# ─── cli ───

def test_cli_help_exits_zero(capsys):
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["--help"])

    assert excinfo.value.code == 0
    out = capsys.readouterr().out
    assert "--all" in out
    assert "--force" in out
    assert f"GZW Wiki Scraper v{gzw_scraper.__version__}" in out


def test_cli_dispatches_category_over_all(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(pipeline, "run_full_scrape", lambda: calls.append("full"))
    monkeypatch.setattr(pipeline, "run_single_category", lambda name: calls.append(f"category:{name}"))

    cli.main(["--category", "Weapons", "--all"])
    cli.main([])

    assert calls == ["category:Weapons", "full"]


def test_cli_force_toggles_drop_guard_override(monkeypatch):
    seen: dict[str, bool] = {}
    monkeypatch.setattr(
        pipeline, "run_full_scrape", lambda: seen.setdefault("force", pipeline.FORCE_SAVE)
    )

    cli.main(["--force", "--all"])
    assert seen["force"] is True

    cli.main(["--all"])
    assert pipeline.FORCE_SAVE is False


def test_cli_custom_config_rebinds_settings(monkeypatch, tmp_path):
    custom = tmp_path / "custom.toml"
    custom.write_text('[output]\ndirectory = "cli_data"\n', encoding="utf-8")
    observed: dict[str, object] = {}

    def capture_settings():
        observed["output_dir"] = storage.OUTPUT_DIR

    monkeypatch.setattr(pipeline, "run_full_scrape", capture_settings)

    cli.main(["--config", str(custom), "--all"])

    assert observed["output_dir"] == config.CONFIG_PATH.parent / "cli_data"
    assert storage.OUTPUT_DIR == config.CONFIG_PATH.parent / "data"


# ─── facade ───

def test_scrape_facade_reexports_package_implementation():
    assert scrape.parse_infobox is parsing.parse_infobox
    assert scrape.parse_page is network.parse_page
    assert scrape.api_call is network.api_call
    assert scrape.safe_save is storage.safe_save
    assert scrape.merge_items is storage.merge_items
    assert scrape.run_full_scrape is pipeline.run_full_scrape
    assert scrape.CATEGORY_TO_FILENAME == pipeline.CATEGORY_TO_FILENAME
