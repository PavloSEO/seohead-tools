"""Owned-widget smoke tests; no target requests or scan launch."""

import os
from pathlib import Path
import hashlib
import json
import subprocess
import tempfile
import time
import unittest

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from seohead_desktop.app import MainWindow, load_theme
from seohead_desktop.bundle import verified_bundled_core_identity
from seohead_desktop.mcp_gateway import TOOL_ALLOWLIST, payload
from seohead_desktop.scan_runner import crawl_arguments, resume_arguments


class ShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        load_theme(cls.app)

    def setUp(self):
        self.window = MainWindow(persistent=False)
        self.window.show()
        self.app.processEvents()

    def tearDown(self):
        self.window.close()
        self.app.processEvents()

    def test_filter_selection_and_navigation(self):
        self.window.search.setText("chair")
        self.app.processEvents()
        self.assertEqual(self.window.proxy.rowCount(), 1)
        self.window.table.selectRow(0)
        self.app.processEvents()
        self.assertIn("/catalog/chair/", self.window.detail.toPlainText())
        self.window.navigation.setCurrentRow(0)
        self.assertEqual(self.window.pages.currentIndex(), 0)
        self.window.navigation.setCurrentRow(1)
        self.assertEqual(self.window.pages.currentIndex(), 1)

    def test_panels_restore_and_compact_window(self):
        self.window.overview.hide()
        self.window.inspector.hide()
        self.window.restore_panels()
        self.app.processEvents()
        self.assertTrue(self.window.overview.isVisible())
        self.assertTrue(self.window.inspector.isVisible())
        self.window.resize(1024, 720)
        self.app.processEvents()
        self.assertGreater(self.window.table.width(), 200)

    def test_scan_preview_cannot_dispatch(self):
        seen = []
        with tempfile.TemporaryDirectory(prefix="seohead-desktop-preview-") as temporary:
            project = Path(temporary)
            (project / "project.json").write_text("{}", encoding="utf-8")
            self.window.project_directory = str(project)
            self.window.project_result = {"project": {"site": {"target": "https://example.test/"}}}

            def close_preview():
                dialog = self.app.activeModalWidget()
                self.assertIsInstance(dialog, QDialog)
                seen.append(dialog.windowTitle())
                dialog.reject()

            QTimer.singleShot(100, close_preview)
            QTest.mouseClick(self.window.new_scan, Qt.LeftButton)
        self.assertEqual(len(seen), 1)
        self.assertIn("явный план", seen[0])

    def test_real_metadata_clears_demo_rows(self):
        self.window.read_generation = 1
        self.window.project_loaded({"project": {"label": "Synthetic core project"}}, 1)
        self.assertEqual(self.window.model.rowCount(), 0)
        self.assertFalse(self.window.search.isEnabled())
        self.assertNotIn("5 демо", self.window.url_caption.text())
        self.window.project_loaded({"wrong": True}, 0)
        self.assertNotIn("wrong", self.window.detail.toPlainText())

    def test_declared_mcp_adapter_excludes_crawl_dispatch(self):
        self.assertIn("seo_project_observe", TOOL_ALLOWLIST)
        self.assertNotIn("seo_crawl_site", TOOL_ALLOWLIST)

        class Result:
            isError = False
            structuredContent = {"result": {"ok": True}}

        self.assertEqual(payload(Result()), {"ok": True})

        class Refused:
            isError = False
            structuredContent = {"result": {"ok": False, "reason": "revision conflict"}}

        with self.assertRaisesRegex(ValueError, "revision conflict"):
            payload(Refused())

    def test_refused_inbox_write_preserves_draft(self):
        self.window.note_input.setText("Do not lose this note")
        self.window.note_submit.setEnabled(False)
        self.window.command_failed("inbox-submit", "inbox revision conflict", self.window.read_generation)
        self.assertEqual(self.window.note_input.text(), "Do not lose this note")
        self.assertTrue(self.window.note_submit.isEnabled())

    def test_stale_scan_result_cannot_replace_selected_scan(self):
        self.window.selected_scan_path = "/retained/current.sqlite"
        before = self.window.model.rowCount()
        self.window.load_urls(
            {"rows": [{"url": "https://old.example.test/", "status_code": 200}]},
            "/retained/old.sqlite",
        )
        self.assertEqual(self.window.model.rowCount(), before)

    def test_scan_switch_clears_previous_url_evidence_before_next_page(self):
        self.window.model.replace([{"url": "https://first.example.test/"}])
        self.window.select_project_scan(
            {"uuid": "next-scan", "path": "/retained/next.sqlite", "lifecycle": "finished"}
        )
        self.assertEqual(self.window.model.rowCount(), 0)
        self.assertEqual(self.window.selected_scan_uuid, "next-scan")
        self.assertEqual(self.window.audit_workspace.panel("internal").state, "loading")

    def test_retained_projection_pages_replace_demo_models(self):
        self.window.load_tasks(
            {
                "items": [
                    {
                        "id": "check:demo",
                        "title": "Retained task",
                        "kind": "check",
                        "display_state": "remaining",
                        "reason": "not attempted",
                    }
                ],
                "pagination": {"total": 1},
            }
        )
        self.window.load_scans(
            {
                "total": 1,
                "items": [
                    {
                        "uuid": "retained-scan",
                        "start_url": "https://example.test/",
                        "lifecycle": "finished",
                        "source_kind": "native",
                        "finished_at": "2026-10-07T00:00:00Z",
                        "crawl_partial": False,
                        "corpus_partial": False,
                    }
                ],
            }
        )
        self.window.load_inbox(
            {
                "revision": 3,
                "entries": [
                    {
                        "id": "inbox:retained",
                        "kind": "note",
                        "text": "Retained handoff",
                        "author_role": "specialist",
                        "goal_state": None,
                        "created_at": "2026-10-07T00:00:00Z",
                    }
                ],
                "pagination": {"total": 1},
            }
        )
        self.assertEqual(self.window.task_model.rows[0]["id"], "check:demo")
        self.assertEqual(self.window.scan_model.rows[0]["partial"], "нет")
        self.assertEqual(self.window.inbox_revision, 3)
        self.assertEqual(self.window.inbox_model.rows[0]["text"], "Retained handoff")

    def test_explicit_scan_arguments_and_verified_bundle_identity(self):
        with tempfile.TemporaryDirectory(prefix="seohead-desktop-bundle-") as temporary:
            root = Path(temporary)
            project = root / "project"
            scans = project / "scans"
            scans.mkdir(parents=True)
            (project / "project.json").write_text("{}", encoding="utf-8")
            desktop = root / "bin" / "SEOHEAD Desktop"
            desktop.parent.mkdir()
            desktop.write_bytes(b"desktop")
            resources = desktop.parent / "Resources"
            cli = resources / "core" / "seohead"
            cli.parent.mkdir(parents=True)
            cli.write_bytes(b"synthetic core")
            manifest = resources / "core-manifest.json"
            manifest.write_text(
                json.dumps(
                    {
                        "schema": "seohead.desktop.core-manifest.v1",
                        "core": {
                            "commit": "a" * 40,
                            "cli_relpath": "core/seohead",
                            "cli_sha256": hashlib.sha256(cli.read_bytes()).hexdigest(),
                        },
                    }
                ),
                encoding="utf-8",
            )
            self.assertEqual(verified_bundled_core_identity(desktop)["commit"], "a" * 40)
            arguments = crawl_arguments(str(project), 25, "raw", "a" * 40)
            self.assertEqual(arguments[:2], ["crawl-site", "--project"])
            self.assertNotIn("--url", arguments)
            self.assertIn("--producer-build", arguments)
            scan = scans / "retained.sqlite"
            scan.write_bytes(b"retained")
            self.assertEqual(
                resume_arguments(str(scan), str(project)),
                ["crawl-site", "--project", str(project.resolve()), "--resume", str(scan.resolve())],
            )
            cli.write_bytes(b"tampered")
            self.assertIsNone(verified_bundled_core_identity(desktop))

    def test_local_core_project_and_explicit_note(self):
        configured = os.environ.get("SEOHEAD_DESKTOP_CORE_CLI")
        if not configured:
            self.skipTest("set SEOHEAD_DESKTOP_CORE_CLI to run the local-core integration gate")
        candidate = Path(configured)
        if not candidate.is_file():
            self.skipTest("local SEOHEAD core CLI is not installed")

        def wait_for(predicate, message, timeout=20):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.app.processEvents()
                if predicate():
                    return
                QTest.qWait(30)
            self.fail(message)

        with tempfile.TemporaryDirectory(prefix="seohead-desktop-test-") as temporary:
            project = Path(temporary) / "project"
            subprocess.run(
                [
                    str(candidate),
                    "project-new",
                    "--directory",
                    str(project),
                    "--target",
                    "https://example.test/",
                    "--label",
                    "Desktop test project",
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            self.window.core_executable = str(candidate)
            self.window.read_project(str(project))
            wait_for(
                lambda: self.window.project_directory is not None
                and self.window.refresh_button.isEnabled()
                and not self.window.requests,
                "desktop did not load local core projections",
            )
            self.assertEqual(Path(self.window.project_directory).resolve(), project.resolve())
            self.assertIn("Локальный проект", self.window.source_badge.text())
            self.assertEqual(self.window.scan_model.rowCount(), 0)
            self.assertEqual(self.window.task_model.rowCount(), 0)

            self.window.note_input.setText("Persist this local handoff")
            QTest.mouseClick(self.window.note_submit, Qt.LeftButton)
            wait_for(
                lambda: self.window.inbox_model.rowCount() == 1 and not self.window.requests,
                "explicit note was not persisted through the declared core adapter",
            )
            self.assertEqual(self.window.inbox_model.rows[0]["text"], "Persist this local handoff")


if __name__ == "__main__":
    unittest.main()
