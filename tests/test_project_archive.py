"""Portable project archive contracts for #996 (first slice: local stores only, synthetic data)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile

import pytest

from seohead.projects import archive
from seohead.projects.archive import archive_project, restore_project
from seohead.projects.workspace import create_project


def _project(tmp_path):
    project = tmp_path / "src"
    create_project(project, "https://example.test/", label="Synthetic")
    with sqlite3.connect(project / "scans" / "scan.sqlite") as db:
        db.execute("create table pages(url text, status integer)")
        db.executemany(
            "insert into pages values (?, ?)",
            [("https://example.test/a", 200), ("https://example.test/b", 404)],
        )
    (project / "scans" / "api_token.txt").write_text("synthetic-value", encoding="utf-8")
    return project


def _rows(path):
    with sqlite3.connect(path) as db:
        return db.execute("select url, status from pages order by url").fetchall()


def _zip_with(tmp_path, members: dict[str, bytes], manifest_files=None):
    """Build an archive by hand so tests can break one property at a time."""
    listed = (
        manifest_files
        if manifest_files is not None
        else [
            {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in members.items()
        ]
    )
    manifest = {
        "format": archive.ARCHIVE_FORMAT,
        "archive_version": archive.ARCHIVE_VERSION,
        "project_format": "seohead.project.v1",
        "directories": ["scans", "reports"],
        "files": listed,
    }
    path = tmp_path / "crafted.zip"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        for name, data in members.items():
            zf.writestr(name, data)
    return path


def test_round_trip_keeps_scan_query_results_and_drops_credential_names(tmp_path):
    project = _project(tmp_path)
    out = tmp_path / "project.zip"
    result = archive_project(project, out)
    assert result["ok"] and out.is_file()
    assert "scans/api_token.txt" not in result["files"]
    assert {item["path"] for item in result["skipped"]} >= {"scans/api_token.txt"}

    with zipfile.ZipFile(out) as zf:
        names = zf.namelist()
    assert not [name for name in names if archive._credential_name(name)]

    restored = restore_project(out, tmp_path / "restored")
    assert restored["ok"]
    assert _rows(tmp_path / "restored" / "scans" / "scan.sqlite") == _rows(
        project / "scans" / "scan.sqlite"
    )
    assert not (tmp_path / "restored" / "scans" / "api_token.txt").exists()


def test_dry_run_reports_plan_and_writes_nothing(tmp_path):
    project = _project(tmp_path)
    out = tmp_path / "planned.zip"
    plan = archive_project(project, out, dry_run=True)
    assert plan["dry_run"] is True
    assert plan["estimated_bytes"] > 0
    assert not out.exists()


def test_archive_refuses_to_overwrite_existing_output(tmp_path):
    project = _project(tmp_path)
    out = tmp_path / "exists.zip"
    out.write_bytes(b"prior")
    with pytest.raises(ValueError, match="already exists"):
        archive_project(project, out)
    assert out.read_bytes() == b"prior"


def test_restore_refuses_existing_target_and_leaves_it_untouched(tmp_path):
    project = _project(tmp_path)
    out = tmp_path / "project.zip"
    archive_project(project, out)
    target = tmp_path / "occupied"
    target.mkdir()
    (target / "keep.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(ValueError, match="new path"):
        restore_project(out, target)
    assert (target / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_tampered_member_fails_verification_and_publishes_nothing(tmp_path):
    project = _project(tmp_path)
    out = tmp_path / "project.zip"
    archive_project(project, out)
    with zipfile.ZipFile(out) as zf:
        members = {name: zf.read(name) for name in zf.namelist()}
    members["scans/scan.sqlite"] = members["scans/scan.sqlite"][:-1] + b"X"
    # Keep the original manifest so the tampered member no longer matches its recorded hash.
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w") as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    target = tmp_path / "bad-restore"
    with pytest.raises(ValueError, match="failed verification"):
        restore_project(tampered, target)
    assert not target.exists()
    assert not [p for p in tmp_path.iterdir() if p.name.startswith(".seohead-restore-")]


@pytest.mark.parametrize("member", ["../evil.txt", "/abs.txt", "scans/../../escape.txt"])
def test_zip_slip_members_are_refused(tmp_path, member):
    data = b"x"
    crafted = _zip_with(tmp_path, {member: data})
    with pytest.raises(ValueError, match="unsafe"):
        restore_project(crafted, tmp_path / "slip")
    assert not (tmp_path / "evil.txt").exists()


def test_symlink_member_is_refused(tmp_path):
    path = tmp_path / "link.zip"
    manifest = json.dumps(
        {
            "format": archive.ARCHIVE_FORMAT,
            "archive_version": archive.ARCHIVE_VERSION,
            "project_format": "seohead.project.v1",
            "directories": ["scans", "reports"],
            "files": [],
        }
    )
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr("manifest.json", manifest)
        info = zipfile.ZipInfo("scans/link")
        info.external_attr = (0o120777) << 16
        zf.writestr(info, "/etc/passwd")
    with pytest.raises(ValueError, match=r"symlink|unsafe|do not match"):
        restore_project(path, tmp_path / "linked")


def test_members_missing_from_manifest_are_refused(tmp_path):
    path = _zip_with(tmp_path, {"log.md": b"# log\n", "extra.txt": b"x"}, manifest_files=[])
    with pytest.raises(ValueError, match="do not match the manifest"):
        restore_project(path, tmp_path / "extra")


def test_cli_archive_and_restore_round_trip(tmp_path, capsys):
    from seohead import cli

    project = _project(tmp_path)
    out = tmp_path / "cli.zip"
    assert cli.main(["project", "archive", "--project", str(project), "--out", str(out)]) == 0
    capsys.readouterr()
    assert cli.main(["project", "restore", str(out), "--to", str(tmp_path / "cli-restored")]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert cli.main(["project", "restore", str(out), "--to", str(tmp_path / "cli-restored")]) == 1
    assert json.loads(capsys.readouterr().out)["ok"] is False
