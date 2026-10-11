"""Competitor candidates can be recorded without a preparation crawl."""

from __future__ import annotations

import pytest

from seohead.projects.runtime import add_competitors, preparation_status, prepare_project
from seohead.projects.workspace import create_project


def _candidate(url: str) -> dict:
    return {"url": url, "source": "desktop paste", "observed_at": "2026-10-11T00:00:00Z"}


def test_adds_candidates_as_separate_workspaces_without_crawling(tmp_path):
    root = tmp_path / "owner"
    create_project(root, "https://owner.example.test/")

    result = add_competitors(str(root), [_candidate("https://one.example.test/")])

    assert result["ok"] is True
    row = result["competitors"][0]
    assert row["url"] == "https://one.example.test/"
    assert row["state"] == "candidate; audit not run"
    assert (root / row["directory"] / "project.json").is_file()
    stored = preparation_status(str(root))
    assert stored["competitors"][0]["project_uuid"] == row["project_uuid"]
    assert stored["steps"]["competitors"]["count"] == 1
    assert not list((root / row["directory"]).glob("scans/*.sqlite"))


def test_second_call_keeps_earlier_candidates_and_refuses_duplicates(tmp_path):
    root = tmp_path / "owner"
    create_project(root, "https://owner.example.test/")
    add_competitors(str(root), [_candidate("https://one.example.test/")])

    result = add_competitors(str(root), [_candidate("https://two.example.test/")])
    assert [row["url"] for row in result["competitors"]] == [
        "https://one.example.test/",
        "https://two.example.test/",
    ]
    with pytest.raises(ValueError, match="already recorded"):
        add_competitors(str(root), [_candidate("https://one.example.test/")])
    assert len(preparation_status(str(root))["competitors"]) == 2


def test_refuses_primary_site_batch_duplicates_and_limit(tmp_path):
    root = tmp_path / "owner"
    create_project(root, "https://owner.example.test/")

    with pytest.raises(ValueError, match="primary site"):
        add_competitors(str(root), [_candidate("https://owner.example.test/")])
    with pytest.raises(ValueError, match="duplicate"):
        add_competitors(
            str(root), [_candidate("https://one.example.test/"), _candidate("https://one.example.test/")]
        )
    too_many = [_candidate(f"https://c{i}.example.test/") for i in range(6)]
    with pytest.raises(ValueError, match="project limit"):
        add_competitors(str(root), too_many)
    assert preparation_status(str(root))["state"] == "pending"


def test_refuses_while_preparation_lock_is_held(tmp_path):
    root = tmp_path / "owner"
    create_project(root, "https://owner.example.test/")
    (root / ".prepare.lock").write_text("", encoding="utf-8")

    with pytest.raises(ValueError, match="already running"):
        add_competitors(str(root), [_candidate("https://one.example.test/")])
    assert (root / ".prepare.lock").exists()


def test_preserves_prepared_state_written_by_preparation(tmp_path):
    root = tmp_path / "owner"
    create_project(root, "https://owner.example.test/")
    prepare_project(str(root), tools={"crawl_site": lambda **_: {"ok": False, "error": "offline"}})
    before = preparation_status(str(root))

    add_competitors(str(root), [_candidate("https://one.example.test/")])

    after = preparation_status(str(root))
    assert after["steps"]["crawl"] == before["steps"]["crawl"]
    assert after["revision"] == before["revision"] + 1
    assert len(after["competitors"]) == 1
