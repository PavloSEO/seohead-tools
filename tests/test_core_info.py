"""Issue #979: `seohead version --json` reports core identity without paths or secrets."""

from __future__ import annotations

import json

from seohead import cli
from seohead.core import core_info as core_info_module
from seohead.core.core_info import CORE_INFO_FORMAT, core_info


def test_core_info_shape_and_types():
    info = core_info()
    assert info["format"] == CORE_INFO_FORMAT
    assert isinstance(info["package_version"], str) and info["package_version"]
    assert info["revision"] is None or len(info["revision"]) == 40
    assert set(info) == {
        "format",
        "package_version",
        "revision",
        "project",
        "ledger",
        "scan_formats",
        "commands",
    }
    assert info["scan_formats"] == ["scan.v1", "scan.v2"]
    assert "parse" in info["commands"]


def test_core_info_has_no_absolute_paths():
    text = json.dumps(core_info())
    assert "/Users/" not in text and "/home/" not in text and "C:\\" not in text


def test_revision_is_none_without_manifest(monkeypatch):
    def missing(*_a, **_k):
        raise FileNotFoundError("no manifest")

    monkeypatch.setattr("seohead._build.provenance.packaged_provenance", missing)
    assert core_info_module._revision() is None


def test_version_json_command(capsys):
    assert cli.main(["version", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["format"] == CORE_INFO_FORMAT


def test_version_text_command(capsys):
    assert cli.main(["version"]) == 0
    assert capsys.readouterr().out.startswith("seohead ")
