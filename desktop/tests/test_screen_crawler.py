import os
import unittest
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QLabel, QPushButton, QTabWidget, QToolButton

from seohead_desktop import i18n
from seohead_desktop.app import load_theme
from seohead_desktop.qt import app as qt_app
from seohead_desktop.screens.crawler import CHECK_TABS, DETAIL_TABS, OVERVIEW, WAITING_ISSUE, CrawlerScreen
from seohead_desktop.ui.kit import StatePanel, UnavailableBadge
from tests._qt import sweep_widgets


class CrawlerScreenTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        load_theme(cls.app, "light")

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")
        self.screen = CrawlerScreen()

    def tearDown(self):
        self.screen.deleteLater()
        sweep_widgets()

    def test_start_and_actions_are_waiting_and_say_why(self):
        start = next(b for b in self.screen.findChildren(QPushButton) if b.text() == "Старт")
        self.assertFalse(start.isEnabled())
        self.assertIn("в этой версии ядра", start.toolTip())
        badges = self.screen.findChildren(UnavailableBadge)
        self.assertEqual([b.property("waiting_issue") for b in badges], [WAITING_ISSUE])
        self.assertFalse(any(b.isEnabled() for b in self.screen.findChildren(QToolButton)))

    def test_waiting_state_panel_is_the_only_table_content(self):
        panels = self.screen.findChildren(StatePanel)
        self.assertEqual(len(panels), 1)
        self.assertEqual(panels[0].kind, "waiting")
        self.assertEqual(panels[0].title.text(), "Краул без проекта пока не запускается")

    def test_no_numbers_or_sample_addresses_are_shown(self):
        texts = [label.text() for label in self.screen.findChildren(QLabel)]
        texts += [button.text() for button in self.screen.findChildren(QPushButton)]
        joined = "\n".join(texts)
        self.assertNotIn("shop.example", joined)
        self.assertNotRegex(joined, r"#\d")

    def test_check_tabs_and_overview_counts_are_empty_dashes(self):
        self.assertEqual(len(CHECK_TABS), 15)
        self.assertEqual(len(OVERVIEW), 18)
        dashes = [label for label in self.screen.findChildren(QLabel) if label.text() == "—"]
        self.assertEqual(len(dashes), len(OVERVIEW))

    def test_every_overview_leaf_has_an_icon_asset(self):
        icons_dir = Path(__file__).resolve().parents[1] / "src" / "seohead_desktop" / "assets" / "icons"
        missing = [name for name, _text, _depth in OVERVIEW if not (icons_dir / f"{name}.svg").is_file()]
        self.assertEqual(missing, [])

    def test_detail_tabs_are_empty_until_a_row_is_selected(self):
        tabs = self.screen.findChild(QTabWidget)
        self.assertEqual(tabs.count(), len(DETAIL_TABS))
        self.assertFalse(tabs.isEnabled())
        self.assertIn("Сведения появятся", " ".join(label.text() for label in self.screen.findChildren(QLabel)))

    def test_tune_action_is_present_and_waiting(self):
        tune = next(b for b in self.screen.findChildren(QToolButton) if b.toolTip() == "Настройки скана")
        self.assertFalse(tune.isEnabled())

    def test_narrow_width_moves_actions_to_second_row(self):
        self.screen.resize(800, 800)
        self.screen._reflow_actions(True)
        self.assertFalse(self.screen._second.isHidden())
        self.assertIs(self.screen._actions.parent(), self.screen._second)
        self.screen._reflow_actions(False)
        self.assertIsNot(self.screen._actions.parent(), self.screen._second)


if __name__ == "__main__":
    unittest.main()
