"""Presentation correctness: unknown evidence, selected identity and native layout."""

import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from seohead_desktop.app import MainWindow, load_theme
from seohead_desktop.ui.presentation import run_projection, value_text


class PresentationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        load_theme(cls.app)

    def setUp(self):
        self.window = MainWindow(persistent=False, core_executable="")
        self.window.start_command = lambda *_args: None
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)

    def test_retained_and_stale_rates_never_become_current(self):
        run = {"state": "partial", "counters": {"rate_per_second": 999}, "telemetry": {"state": "retained", "current_rate_per_second": 888}}
        self.assertEqual(run_projection(run)["rate"], "Нет текущей")
        run["telemetry"]["state"] = "stale"
        self.assertEqual(run_projection(run)["rate"], "Устарело")
        run["telemetry"].update(state="fresh", unit="pages", current_rate_per_second=0)
        self.assertEqual(run_projection(run)["rate"], "0 стр./с")
        self.assertIsNone(run_projection({})["fetched"])
        self.assertIsNone(run_projection({})["rate_limit"])

    def test_missing_counts_are_not_zero_or_success(self):
        self.window.load_progress({"state": "not_initialized", "counts": {"complete": None}})
        text = self.window.progress_text.toPlainText()
        self.assertIn("Завершено: Не измерено", text)
        self.assertNotIn("100%", text)
        self.window.load_scan_status({"frontier": {"state": "available", "counts": {"done": 7, "queued": None, "inflight": 0}}, "source": {"lifecycle": "partial"}})
        self.assertTrue(self.window.scan_progress.isHidden())
        self.assertEqual(self.window.scan_state_badge.text(), "Частичный результат")
        self.assertNotEqual(value_text(None), value_text(0))
        self.assertNotEqual(value_text(None), value_text(""))

    def test_progress_uses_measured_frontier_not_configured_limit(self):
        self.window.load_scan_status({"source": {"lifecycle": "interrupted", "max_urls": 5000}, "frontier": {"state": "available", "counts": {"done": 2, "queued": 5, "inflight": 1}}})
        self.assertEqual(self.window.scan_progress.value(), 250)
        self.assertIn("2 из 8", self.window.scan_progress_label.text())
        self.assertNotIn("5000", self.window.scan_progress_label.text())

    def test_scan_picker_uses_identity_and_observer_keeps_selection(self):
        scans = [{"uuid": "first", "path": "/scans/first.sqlite"}, {"uuid": "second", "path": "/scans/second.sqlite"}]
        self.window.load_scans({"items": scans, "total": 2})
        self.window.scan_picker.setCurrentIndex(1)
        self.assertEqual(self.window.selected_scan_path, scans[1]["path"])
        self.assertEqual(self.window.scan_table.currentIndex().row(), 1)
        self.window.load_scans({"items": scans, "total": 2})
        self.assertEqual(self.window.scan_picker.currentData()["uuid"], "second")
        self.assertIsNone(self.window.scan_model.rows[0]["partial"])

    def test_run_selection_and_journal_remain_bounded(self):
        runs = [{"id": f"run-{index}", "state": "partial", "events": [{"at": "2026-10-07T00:00:00Z", "phase": "collection", "code": "progress"}] * 40} for index in range(60)]
        self.window.present_observed_runs(runs, None)
        self.assertEqual(self.window.activity_model.rowCount(), 50)
        self.assertEqual(self.window.journal_model.rowCount(), 200)
        self.window.activity_table.selectRow(3)
        self.window.present_observed_runs(runs, None)
        self.assertIn("run-3", self.window.activity_text.toPlainText())

    def test_compact_layout_density_and_panel_restore(self):
        self.window.resize(1024, 720)
        self.app.processEvents()
        self.assertEqual(self.window.width(), 1024)
        self.assertTrue(self.window.navigation.property("compact"))
        self.assertFalse(self.window.overview.isVisible())
        self.assertGreater(self.window.table.width(), 650)
        self.window.set_density("compact")
        self.assertEqual(self.window.table.verticalHeader().defaultSectionSize(), 28)
        self.window.toggle_focus_mode()
        self.assertFalse(self.window.inspector.isVisible())
        self.window.toggle_focus_mode()
        self.assertTrue(self.window.inspector.isVisible())
        self.window.restore_panels()
        self.assertTrue(self.window.overview.isVisible())
        self.window.set_density("standard")

    def test_find_shortcut_and_table_copy_preserve_literal_url(self):
        self.window.focus_search()
        self.assertIs(self.app.focusWidget(), self.window.search)
        self.window.search.setText("chair")
        self.window.table.selectRow(0)
        self.window.copy_url_selection()
        self.assertIn("/catalog/chair/", QApplication.clipboard().text())
        self.window.navigation.setFocus()
        QTest.keyClick(self.window.navigation, Qt.Key_Down)
        self.assertEqual(self.window.pages.currentIndex(), self.window.navigation.currentRow())

    def test_headers_are_supplied_only_and_sensitive_values_are_masked(self):
        self.window.selected_scan_path = "scan"
        self.window.selected_url = "https://fixture.test/"
        self.window.load_url_detail({"state": "available", "page": {"url": "https://fixture.test/", "title": "<b>literal</b>"}, "responses": {"items": [{"response_id": 1, "request_headers": [["Authorization", "test-secret"]], "response_headers": [["Content-Type", "text/html"]]}]}}, "scan", "https://fixture.test/")
        self.assertNotIn("test-secret", self.window.headers_detail.toPlainText())
        self.assertIn("[скрыто]", self.window.headers_detail.toPlainText())
        self.assertIn("<b>literal</b>", self.window.detail.toPlainText())
        self.assertEqual(self.window.audit_workspace.panel("http_headers").model.rowCount(), 2)
        self.window.clear_scan_selection("Other scan")
        self.assertNotIn("Content-Type", self.window.headers_detail.toPlainText())


if __name__ == "__main__":
    unittest.main()
