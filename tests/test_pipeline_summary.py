from __future__ import annotations

from gzw_scraper import network, pipeline


def test_full_scrape_summary_counts_split_task_files(monkeypatch):
    categories = [
        {"title": "Main task", "pages": 1},
        {"title": "Side task", "pages": 1},
    ]
    saved_files: list[str] = []

    monkeypatch.setattr(pipeline, "get_previous_counts", lambda: {})
    monkeypatch.setattr(network, "get_all_categories", lambda: categories)
    monkeypatch.setattr(network, "filter_game_categories", lambda all_categories: all_categories)
    monkeypatch.setattr(pipeline, "get_filtered_categories", lambda all_categories, game_categories: [])
    monkeypatch.setattr(
        pipeline,
        "scrape_single_category_task",
        lambda category, previous_counts: (
            "task_items.json",
            [{"id": category["title"], "name": category["title"]}],
        ),
    )
    monkeypatch.setattr(pipeline, "scrape_supplemental_pages", lambda: {})
    monkeypatch.setattr(pipeline, "scrape_hideout_tables", lambda: {})
    monkeypatch.setattr(pipeline, "scrape_experience_tables", lambda: {})
    monkeypatch.setattr(network, "get_all_pages", lambda: [])
    monkeypatch.setattr(pipeline, "LISTING_PAGES", {})
    monkeypatch.setattr(pipeline, "get_output_filename", lambda title: "task_items.json")
    monkeypatch.setattr(pipeline.storage, "merge_items", lambda existing, items: existing + items)
    monkeypatch.setattr(
        pipeline.storage,
        "safe_save",
        lambda filename, items, previous_count, force=False: saved_files.append(filename) or True,
    )
    monkeypatch.setattr(pipeline, "write_scrape_metadata", lambda *args, **kwargs: None)
    monkeypatch.setattr(pipeline, "tqdm", None)

    log_messages: list[str] = []
    monkeypatch.setattr(
        pipeline.logger,
        "info",
        lambda message, *args: log_messages.append(message % args if args else message),
    )
    assert pipeline.run_full_scrape() is True

    assert sorted(saved_files) == ["main_task.json", "side_task.json", "task_items.json"]
    assert "  Files saved: 3/3" in log_messages
