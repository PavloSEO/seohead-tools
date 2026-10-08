"""Small-window Work browsing keeps a useful table and explicit inspectors."""

import unittest

from PyQt5.QtCore import Qt
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from seohead_desktop.app import MainWindow, load_theme


class WorkTableSpaceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")
        load_theme(cls.app)

    def setUp(self):
        self.window = MainWindow(persistent=False, core_executable="")
        self.window.project_directory = "/synthetic-project"
        self.window.update_work_project_state()
        self.window.navigation.setCurrentRow(0)
        self.window.work_views.setCurrentIndex(1)
        self.window.present_observed_runs(
            [{"id": f"run-{index}", "state": "partial"} for index in range(20)], None
        )
        self.window.resize(800, 720)
        self.window.show()
        self.app.processEvents()
        self.addCleanup(self.window.close)

    def test_default_table_has_space_and_inspectors_preserve_live_data(self):
        w = self.window
        row_height = w.activity_table.verticalHeader().defaultSectionSize()
        self.assertGreaterEqual(w.activity_table.viewport().height() // row_height, 7)
        self.assertTrue(w.work_inspector.isHidden())
        w.load_progress({"state": "not_initialized", "counts": {"complete": None}})
        self.assertIn("План не настроен", w.work_plan_summary.text())
        w.work_plan_toggle.setFocus()
        QTest.keyClick(w.work_plan_toggle, Qt.Key_Space)
        self.assertTrue(w.work_inspector.isVisible())
        self.assertIs(w.work_inspector.currentWidget(), w.progress_text)
        self.assertIn("Не измерено", w.progress_text.toPlainText())
        w.activity_table.selectRow(5)
        identity = w.selected_observed_run_id
        QTest.mouseClick(w.work_detail_toggle, Qt.LeftButton)
        self.assertFalse(w.work_plan_toggle.isChecked())
        self.assertIs(w.work_inspector.currentWidget(), w.activity_text)
        self.assertEqual(w.selected_observed_run_id, identity)
        self.assertIn("run-5", w.activity_text.toPlainText())
        w.present_observed_runs(w.observed_runs, None)
        self.assertTrue(w.work_detail_toggle.isChecked())
        QTest.mouseClick(w.work_detail_toggle, Qt.LeftButton)
        self.app.processEvents()
        self.assertTrue(w.work_inspector.isHidden())
        self.assertGreaterEqual(w.activity_table.viewport().height() // row_height, 7)

    def test_measured_plan_updates_compact_summary_without_opening_inspector(self):
        self.window.load_progress({"state": "available", "audit_task_completion": {
            "state": "measured", "numerator": 3, "denominator": 12,
        }})
        self.assertIn("3 из 12", self.window.work_plan_summary.text())
        self.assertTrue(self.window.work_inspector.isHidden())
        monitor = self.window.ensure_monitor()
        self.window.progress_text.setPlainText("Updated retained plan")
        self.window.activity_text.setPlainText("Updated retained run")
        self.assertEqual(monitor.progress.toPlainText(), "Updated retained plan")
        self.assertEqual(monitor.detail.toPlainText(), "Updated retained run")
