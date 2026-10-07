"""Packaging contracts use temporary files and never open a site or project."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seohead_desktop.bundle import bundled_core_cli, bundled_core_manifest, package_arguments


def load_script(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


MANIFEST = load_script("bundle_manifest.py")


class PackagingTests(unittest.TestCase):
    def test_macos_relative_core_resolution(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "SEOHEAD Desktop.app"
            executable = app / "Contents" / "MacOS" / "SEOHEAD Desktop"
            resources = app / "Contents" / "Resources"
            core = resources / "core" / "seohead" / "seohead"
            executable.parent.mkdir(parents=True)
            core.parent.mkdir(parents=True)
            executable.touch()
            core.touch()
            (resources / "core-manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "seohead.desktop.core-manifest.v1",
                        "core": {"cli_relpath": "core/seohead/seohead"},
                    }
                ),
                encoding="utf-8",
            )
            found = bundled_core_manifest(executable)
            self.assertIsNotNone(found)
            self.assertEqual(bundled_core_cli(executable), core.resolve())

    def test_core_path_cannot_escape_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            app = Path(directory) / "SEOHEAD Desktop.app"
            executable = app / "Contents" / "MacOS" / "SEOHEAD Desktop"
            resources = app / "Contents" / "Resources"
            executable.parent.mkdir(parents=True)
            resources.mkdir(parents=True)
            executable.touch()
            (resources / "core-manifest.json").write_text(
                json.dumps(
                    {
                        "schema": "seohead.desktop.core-manifest.v1",
                        "core": {"cli_relpath": "../../outside"},
                    }
                ),
                encoding="utf-8",
            )
            self.assertIsNone(bundled_core_cli(executable))

    def test_explicit_core_override_wins_over_packaged_default(self):
        packaged = Path("/bundle/core/seohead")
        self.assertEqual(
            package_arguments(["--capture", "preview.png"], packaged),
            ["--core-cli", str(packaged), "--capture", "preview.png"],
        )
        self.assertEqual(
            package_arguments(["--core-cli", "/diagnostic/seohead"], packaged),
            ["--core-cli", "/diagnostic/seohead"],
        )

    def test_manifest_pins_clean_git_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "core"
            source.mkdir()
            (source / "pyproject.toml").write_text(
                '[project]\nname = "seohead-seotools"\nversion = "3.0.0"\n',
                encoding="utf-8",
            )
            subprocess.run(["git", "init"], cwd=source, check=True, capture_output=True)
            subprocess.run(
                ["git", "remote", "add", "origin", "https://github.com/PavloSEO/seohead-tools.git"],
                cwd=source,
                check=True,
            )
            subprocess.run(["git", "add", "pyproject.toml"], cwd=source, check=True)
            subprocess.run(
                [
                    "git",
                    "-c",
                    "user.name=Test",
                    "-c",
                    "user.email=test@example.test",
                    "commit",
                    "-m",
                    "fixture",
                ],
                cwd=source,
                check=True,
                capture_output=True,
            )
            manifest_path = Path(directory) / "core-manifest.json"
            payload = MANIFEST.create_manifest(source, manifest_path, "macos")
            self.assertEqual(payload["core"]["version"], "3.0.0")
            self.assertEqual(len(payload["core"]["commit"]), 40)
            self.assertEqual(len(payload["core"]["source_archive_sha256"]), 64)
            self.assertNotIn(str(source), manifest_path.read_text(encoding="utf-8"))

    def test_bundle_spec_requires_every_platform_layout(self):
        spec = json.loads((ROOT / "packaging" / "bundle-spec.json").read_text())
        self.assertEqual(spec["core"]["delivery"], "same-artifact")
        self.assertEqual(set(spec["platforms"]), {"macos", "linux", "windows"})
        for platform in spec["platforms"].values():
            self.assertTrue(platform["core_cli"].startswith("core/"))


if __name__ == "__main__":
    unittest.main()
