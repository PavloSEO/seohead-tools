import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from seohead_desktop import core_discovery


class _Prefs(dict):
    def get(self, key, default=None):
        return dict.get(self, key, default)


def _cli(directory):
    path = Path(directory) / "seohead"
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)
    return str(path)


class CoreDiscoveryTests(unittest.TestCase):
    def test_enabled_custom_path_wins_over_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            custom = _cli(tmp)
            prefs = _Prefs({"core.custom": True, "core.custom_path": custom})
            with mock.patch.object(core_discovery.shutil, "which", return_value="/elsewhere/seohead"):
                self.assertEqual(core_discovery.discover_core(prefs), custom)

    def test_disabled_custom_path_is_ignored(self):
        with tempfile.TemporaryDirectory() as tmp:
            prefs = _Prefs({"core.custom": False, "core.custom_path": _cli(tmp)})
            with mock.patch.object(core_discovery.shutil, "which", return_value=None), \
                    mock.patch.object(core_discovery, "SOURCE_VENV_CLI", Path(tmp) / "missing"), \
                    mock.patch.object(core_discovery, "COMMON_DIRECTORIES", ()), \
                    mock.patch.object(core_discovery.bundle, "bundled_core_cli", return_value=None), \
                    mock.patch.object(core_discovery.sys, "executable", str(Path(tmp) / "python")):
                self.assertIsNone(core_discovery.discover_core(prefs))

    def test_common_install_directory_is_found_without_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            cli = _cli(tmp)
            with mock.patch.object(core_discovery.shutil, "which", return_value=None), \
                    mock.patch.object(core_discovery, "SOURCE_VENV_CLI", Path(tmp) / "missing"), \
                    mock.patch.object(core_discovery, "COMMON_DIRECTORIES", (tmp,)), \
                    mock.patch.object(core_discovery.bundle, "bundled_core_cli", return_value=None), \
                    mock.patch.object(core_discovery.sys, "executable", str(Path(tmp) / "python")):
                self.assertEqual(core_discovery.discover_core(None), cli)

    def test_non_executable_candidate_is_skipped(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seohead"
            path.write_text("data", encoding="utf-8")
            path.chmod(0o644)
            self.assertIsNone(core_discovery._usable(path))
            os.chmod(path, 0o755)
            self.assertEqual(core_discovery._usable(path), str(path))


if __name__ == "__main__":
    unittest.main()
