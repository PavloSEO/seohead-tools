import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QLabel

from seohead_desktop import i18n
from seohead_desktop.app import load_theme
from seohead_desktop.qt import app as qt_app
from seohead_desktop.screens.graph import GraphScreen
from seohead_desktop.ui.kit import StatePanel
from tests._qt import sweep_widgets
from tests._screens_core import fixture
from tests._screens_host import FakeHost


class GraphScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        load_theme(cls.app, "light")

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")
        self.host = FakeHost()
        self.screen = GraphScreen(self.host)
        self.screen.resize(1440, 800)
        self.screen.show()
        self.app.processEvents()

    def tearDown(self):
        self.screen.close()
        self.screen.deleteLater()

    def panel(self):
        return self.screen.findChild(StatePanel)

    def test_without_project_it_shows_the_project_gate(self):
        self.host.project_directory = None
        self.screen.refresh()
        self.assertEqual(self.panel().kind, "empty")
        self.assertEqual(self.panel().action.text(), "Открыть проект…")

    def test_without_a_saved_scan_it_offers_a_new_scan_and_no_graph(self):
        self.host.project_directory = "/project/qa"
        self.host.scan_model.rows = []
        self.host.selected_scan_path = None
        self.screen.refresh()
        panel = self.panel()
        self.assertEqual(panel.kind, "empty")
        self.assertEqual(panel.action.text(), "Новый скан")
        self.assertIn("Графа пока нет", [w.text() for w in self.screen.findChildren(QLabel)])

    def test_with_a_scan_it_waits_for_the_core_and_draws_nothing(self):
        scan = fixture("scan_row.json")
        self.host.project_directory = "/project/qa"
        self.host.scan_model.rows = [scan]
        self.host.selected_scan_path = scan["path"]
        self.screen.refresh()
        panel = self.panel()
        self.assertEqual(panel.kind, "waiting")
        self.assertIsNotNone(panel.issue_label)
        self.assertEqual(panel.issue_label.property("waiting_issue"), 975)
        self.assertIsNone(panel.action)


if __name__ == "__main__":
    unittest.main()
