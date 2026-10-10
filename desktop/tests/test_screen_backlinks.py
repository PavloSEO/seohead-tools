import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QLabel

from seohead_desktop import i18n
from seohead_desktop.app import load_theme
from seohead_desktop.qt import app as qt_app
from seohead_desktop.screens.backlinks import STATES, BacklinksScreen
from seohead_desktop.ui.kit import StatePanel
from tests._qt import sweep_widgets
from tests._screens_host import FakeHost


class BacklinksScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        load_theme(cls.app, "light")

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")
        self.screen = BacklinksScreen(FakeHost())
        self.screen.resize(1440, 900)
        self.screen.show()
        self.app.processEvents()

    def tearDown(self):
        self.screen.close()
        self.screen.deleteLater()

    def test_every_state_is_a_waiting_panel_with_the_core_issue(self):
        panels = self.screen.findChildren(StatePanel)
        self.assertEqual(len(panels), len(STATES))
        for panel in panels:
            self.assertEqual(panel.kind, "waiting")
            self.assertEqual(panel.issue_label.property("waiting_issue"), 999)

    def test_no_sample_numbers_or_demo_domains_are_shown(self):
        texts = [w.text() for w in self.screen.findChildren(QLabel)]
        self.assertFalse(any(".example" in text or "$" in text for text in texts))


if __name__ == "__main__":
    unittest.main()
