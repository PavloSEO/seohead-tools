"""Per-site observer projections stay bounded, local and explicit about unfinished work."""

from __future__ import annotations

from pathlib import Path

from seohead.projects.observer import observe
from seohead.projects.runtime import prepare_project
from seohead.projects.workspace import create_project
from seohead.servers.mcp_server import build_server
from tests.test_scan_history import _finished


def _prepare_with_competitors(root: Path) -> dict:
    create_project(
        root,
        "https://owner.example.test/",
        label="Owner",
        template_references=["ecommerce/product-card"],
        profile_references=["ecommerce/basic"],
    )

    def unavailable_crawl(**_kwargs):
        return {"ok": False, "error": "synthetic crawler unavailable"}

    return prepare_project(
        str(root),
        tools={"crawl_site": unavailable_crawl},
        competitors=[
            {
                "url": "https://competitor-one.example.test/",
                "source": "synthetic shortlist",
                "observed_at": None,
            },
            {
                "url": "https://competitor-two.example.test/",
                "source": "synthetic shortlist",
                "observed_at": None,
            },
        ],
    )


def test_observer_projects_scans_coverage_and_methods_per_declared_site(tmp_path):
    root = tmp_path / "owner"
    preparation = _prepare_with_competitors(root)["preparation"]
    first = root / preparation["competitors"][0]["directory"]
    second = root / preparation["competitors"][1]["directory"]
    _finished(root / "scans" / "owner.sqlite")
    _finished(first / "scans" / "competitor-one.sqlite")
    _finished(second / "scans" / "competitor-two.sqlite")
    owner_before = (root / "scans" / "owner.sqlite").read_bytes()
    competitor_before = (first / "scans" / "competitor-one.sqlite").read_bytes()

    snapshot = observe(str(root), scan_limit=1)

    assert snapshot["policy"] == {
        "ok": True,
        "applied": False,
        "revision": 0,
        "policy": {
            "approval_thresholds": {"pages": 1000, "requests": 3000, "seconds": 600},
            "quick_crawl": {"pages": 50, "requests": 150, "seconds": 60},
            "crawl_overrides": {},
            "competitor_limit": 5,
        },
    }
    assert snapshot["sites"]["total"] == 3
    assert snapshot["sites"]["scan_limit_per_site"] == 1
    owner, competitor_one, competitor_two = snapshot["sites"]["items"]
    assert owner["role"] == "primary"
    assert owner["site"]["target"] == "https://owner.example.test/"
    assert owner["candidate"] is None
    assert owner["scans"]["items"][0]["artifact"] == {
        "state": "available",
        "path": "scans/owner.sqlite",
    }
    assert owner["scans"]["items"][0]["evidence"]["state"] == "available"
    assert owner["coverage"]["state"] == "initialized"
    assert owner["methods"]["kinds"]["scenario"]["expected"] > 0
    assert owner["methods"]["kinds"]["scenario"]["completed"] == 0
    assert competitor_one["role"] == competitor_two["role"] == "competitor"
    assert competitor_one["candidate"]["state"] == "candidate; audit not run"
    assert competitor_one["candidate"]["source"] == "synthetic shortlist"
    assert competitor_one["scans"]["items"][0]["artifact"]["path"].startswith("scans/")
    assert competitor_one["scans"]["items"][0]["evidence"]["state"] == "available"
    assert competitor_two["site"]["target"] == "https://competitor-two.example.test/"
    assert snapshot["scans"]["total"] == 1  # Legacy root-only projection remains compatible.
    assert all(method["site"]["target"] for method in snapshot["methods"])
    assert (root / "scans" / "owner.sqlite").read_bytes() == owner_before
    assert (first / "scans" / "competitor-one.sqlite").read_bytes() == competitor_before


def test_mcp_observer_exposes_the_same_bounded_site_projection(tmp_path):
    root = tmp_path / "owner"
    preparation = _prepare_with_competitors(root)["preparation"]
    competitor = root / preparation["competitors"][0]["directory"]
    _finished(root / "scans" / "owner.sqlite")
    _finished(competitor / "scans" / "competitor.sqlite")

    tool = build_server()._tool_manager.get_tool("seo_project_observe")
    snapshot = tool.fn(directory=str(root), scan_limit=1)

    assert snapshot["sites"]["scan_limit_per_site"] == 1
    assert {row["role"] for row in snapshot["sites"]["items"]} == {"primary", "competitor"}
    assert snapshot["policy"]["policy"]["quick_crawl"]["pages"] == 50
    assert all(item["scans"]["shown"] <= 1 for item in snapshot["sites"]["items"])
