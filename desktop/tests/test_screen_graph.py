"""Link graph screen (GraphFilters sheet): honest waiting state, inert filters, no invented numbers."""

import os
import re
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import Qt
from PyQt5.QtWidgets import QCheckBox, QLabel

from seohead_desktop import i18n
from seohead_desktop.qt import app as qt_app
from seohead_desktop.screens.graph import GRAPH_ISSUE, GraphScreen
from seohead_desktop.ui.kit import StatePanel
from tests._qt import sweep_widgets
from tests._screens_host import FakeHost

COUNT = re.compile(r"\d{2,}|\d\s\d")  # counts; «3xx», «до 4» are labels, not data


class GraphScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")

    def test_open_project_shows_the_waiting_state_for_the_core_issue(self):
        screen = GraphScreen(FakeHost())
        self.assertIs(screen.states.currentWidget(), screen.waiting)
        self.assertIsInstance(screen.waiting, StatePanel)
        self.assertEqual(screen.waiting.issue_label.property("waiting_issue"), GRAPH_ISSUE)

    def test_no_project_asks_to_open_one(self):
        screen = GraphScreen(FakeHost(project=None))
        self.assertIs(screen.states.currentWidget(), screen.no_project)

    def test_filters_are_inert_and_show_the_sheet_defaults(self):
        screen = GraphScreen(FakeHost())
        boxes = {box.text(): box for box in screen.findChildren(QCheckBox)}
        self.assertTrue(boxes["Контент"].isChecked())
        self.assertTrue(boxes["Сироты"].isChecked())
        self.assertFalse(boxes["Подвал"].isChecked())
        for box in boxes.values():
            self.assertTrue(box.testAttribute(Qt.WA_TransparentForMouseEvents))
            self.assertEqual(box.focusPolicy(), Qt.NoFocus)

    def test_no_numbers_are_drawn_without_core_data(self):
        screen = GraphScreen(FakeHost())
        texts = [label.text() for label in screen.findChildren(QLabel)]
        texts += [box.text() for box in screen.findChildren(QCheckBox)]
        self.assertEqual([text for text in texts if COUNT.search(text)], [])


if __name__ == "__main__":
    unittest.main()
