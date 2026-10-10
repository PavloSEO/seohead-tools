import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtWidgets import QToolButton

from seohead_desktop import i18n
from seohead_desktop.app import MainWindow, load_theme
from seohead_desktop.qt import app as qt_app
from seohead_desktop.screens.reports import ISSUE, PAGES
from seohead_desktop.screens.base import SLOTS
from seohead_desktop.ui.kit import Gate, UnavailableBadge
from tests._qt import sweep_widgets
from tests._screens_core import open_qa


class ReportsScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        load_theme(cls.app, "light")

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")
        self.window = MainWindow(persistent=False)
        self.screen = self.window.screens["reports"]

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)

    def test_reports_slot_holds_the_rebuilt_screen(self):
        self.assertIs(self.window.pages.widget(SLOTS["reports"]), self.screen)

    def test_without_project_the_page_asks_to_open_one(self):
        self.window.project_directory = None
        self.screen.refresh()
        self.assertIs(self.screen.gate.currentWidget(), self.screen.gate.open_panel)

    def test_with_project_the_page_is_an_honest_waiting_state_without_sample_numbers(self):
        open_qa(self.window, tasks=False, inbox=False, scans=False)
        self.screen.refresh()
        self.assertIsInstance(self.screen.gate, Gate)
        self.assertIs(self.screen.gate.currentWidget(), self.screen.gate.content)
        self.assertFalse(self.screen.export.isEnabled())
        self.assertFalse(self.screen.language.isEnabled())

    def test_rail_pages_follow_the_sheet_and_stay_disabled(self):
        thumbs = [b for b in self.screen.findChildren(QToolButton) if b.property("report_thumb")]
        self.assertEqual(len(thumbs), len(PAGES))
        self.assertTrue(all(not b.isEnabled() for b in thumbs))

    def test_waiting_badge_names_core_issue_without_showing_its_number(self):
        badges = [w for w in self.screen.findChildren(UnavailableBadge) if w.property("waiting_issue") == ISSUE]
        self.assertTrue(badges)
        self.assertNotIn(str(ISSUE), badges[0].text())


if __name__ == "__main__":
    unittest.main()
