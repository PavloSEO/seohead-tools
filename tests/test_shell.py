"""Owned-widget smoke tests; no target requests or scan launch."""

import unittest

from PyQt5.QtCore import Qt, QTimer
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QDialog

from seohead_desktop.app import MainWindow, load_theme


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

        def close_preview():
            dialog = self.app.activeModalWidget()
            self.assertIsInstance(dialog, QDialog)
            seen.append(dialog.windowTitle())
            dialog.reject()

        QTimer.singleShot(100, close_preview)
        QTest.mouseClick(self.window.new_scan, Qt.LeftButton)
        self.assertEqual(len(seen), 1)
        self.assertIn("preview", seen[0])

    def test_real_metadata_clears_demo_rows(self):
        self.window.read_generation = 1
        self.window.project_loaded({"project": {"label": "Synthetic core project"}}, 1)
        self.assertEqual(self.window.model.rowCount(), 0)
        self.assertFalse(self.window.search.isEnabled())
        self.assertNotIn("5 демо", self.window.url_caption.text())
        self.window.project_loaded({"wrong": True}, 0)
        self.assertNotIn("wrong", self.window.detail.toPlainText())


if __name__ == "__main__":
    unittest.main()
