"""A project keeps a link and a summary of its semantic core, never the core itself.

The semantic core lives in its own database. The project records only operator-entered
facts that point at it, so the same ``project-facts`` surface and precedence rules apply.
Detection is injected and never makes a request.
"""

from __future__ import annotations

import json

import pytest

from seohead.projects.facts import project_facts
from seohead.projects.workspace import create_project

SEMCORE_FACTS = [
    {
        "name": "semcore_project",
        "value": "semantics/semcore/example-shop",
        "provenance": "operator: semantic core project directory",
        "observed_at": None,
    },
    {
        "name": "semcore_cluster_count",
        "value": 42,
        "provenance": "operator: semcore report summary",
        "observed_at": None,
    },
    {
        "name": "semcore_landing_mapping",
        "value": "38 of 42 clusters mapped to a landing page",
        "provenance": "operator: semcore landing-page mapping summary",
        "observed_at": None,
    },
]


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    return root


def test_semcore_link_is_recorded_as_operator_facts_and_read_back(project):
    result = project_facts(str(project), facts=SEMCORE_FACTS, apply=True)

    assert result["applied"] is True
    assert [change["action"] for change in result["changes"]] == ["recorded"] * 3
    saved = json.loads((project / "project.json").read_text(encoding="utf-8"))["facts"]
    recorded = {fact["name"]: fact["value"] for fact in saved}
    assert recorded == {
        "semcore_project": "semantics/semcore/example-shop",
        "semcore_cluster_count": 42,
        "semcore_landing_mapping": "38 of 42 clusters mapped to a landing page",
    }


def test_semcore_link_is_a_current_value_when_updated(project):
    project_facts(str(project), facts=SEMCORE_FACTS, apply=True)
    updated = [dict(SEMCORE_FACTS[1], value=45)]

    result = project_facts(str(project), facts=updated, apply=True)

    assert [change["action"] for change in result["changes"]] == ["updated"]
    saved = json.loads((project / "project.json").read_text(encoding="utf-8"))["facts"]
    assert [fact["value"] for fact in saved if fact["name"] == "semcore_cluster_count"] == [45]
    assert len(saved) == 3


def test_semcore_link_refuses_to_claim_detection_provenance(project):
    forged = [dict(SEMCORE_FACTS[0], provenance="detected by tech-detect at https://example.test/")]

    with pytest.raises(ValueError, match="detection provenance"):
        project_facts(str(project), facts=forged, apply=True)

    assert json.loads((project / "project.json").read_text(encoding="utf-8"))["facts"] == []
