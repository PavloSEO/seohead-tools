"""Issue #955: playbooks expose ordered steps and headings without running anything."""

from __future__ import annotations

from seohead.projects.runtime import playbook_list, playbook_outline, playbook_show


def test_outline_reads_headings_and_numbered_steps_and_skips_fenced_code():
    content = (
        "# Title\n\n## 1. Establish scope.\n\nText.\n\n```bash\n# not a heading\n"
        "## 2. not a step either\n```\n\n**2. Crawl the site**\n\n### Covers\n"
    )
    outline = playbook_outline(content)
    assert outline["steps"] == ["Establish scope", "Crawl the site"]
    assert [heading["title"] for heading in outline["headings"]] == [
        "Title",
        "1. Establish scope.",
        "Covers",
    ]
    assert outline["headings"][0] == {"level": 1, "title": "Title"}


def test_list_entries_carry_steps_and_show_carries_the_outline():
    skills = playbook_list("skill")["items"]
    control = next(item for item in skills if item["id"] == "skill:workflow/control")
    assert isinstance(control["steps"], list)
    assert "content" not in control

    shown = playbook_show("full-audit", "scenario")
    assert shown["headings"] and isinstance(shown["steps"], list)


def test_scenario_list_returns_only_scenarios():
    from seohead.mcp.handlers import scenario_list

    items = scenario_list()["items"]
    assert items and all(item["kind"] == "scenario" for item in items)
    assert any(item["id"] == "scenario:full-audit" for item in items)
