"""Keep the synthetic Looker blueprint tied to the published BI schema."""

from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree

from seohead.reports.bi import DATASET_SPECS


def test_looker_blueprint_uses_only_published_bi_datasets_and_fields():
    path = Path("examples/reporting-pack/looker-studio-blueprint.json")
    blueprint = json.loads(path.read_text(encoding="utf-8"))
    assert blueprint["format"] == "seohead.looker-studio-blueprint.v1"
    fields = {name: {field.name for field in spec[0]} for name, spec in DATASET_SPECS.items()}
    for page in blueprint["pages"]:
        assert page["datasets"]
        for dataset in page["datasets"]:
            assert dataset in fields
        assert all(
            any(field in fields[dataset] for dataset in page["datasets"])
            for field in page["fields"]
        )
    for control in blueprint["global_controls"]:
        datasets = control.get("datasets") or [control["dataset"]]
        assert all(control["field"] in fields[dataset] for dataset in datasets)


def test_reporting_pack_preview_is_valid_svg():
    path = Path("examples/reporting-pack/layout-preview.svg")
    assert ElementTree.parse(path).getroot().tag.endswith("svg")
    assert "SEOHEAD Evidence Reporting Pack" in path.read_text(encoding="utf-8")
