import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QSize
from PyQt5.QtGui import QResizeEvent
from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QPushButton, QToolButton

from seohead_desktop import i18n
from seohead_desktop.app import load_theme
from seohead_desktop.qt import app as qt_app
from seohead_desktop.screens.crawler import CHECK_TABS, OVERVIEW, WAITING_ISSUE, CrawlerScreen
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


    def test_temporary_results_banner_is_hidden_until_results_exist(self):
        banner = next(f for f in self.screen.findChildren(QFrame) if f.property("note") == "info")
        self.assertTrue(banner.isHidden())
        self.screen.set_temporary_results(True, "https://crawl.localhost/")
        self.assertFalse(banner.isHidden())
        texts = [label.text() for label in self.screen.findChildren(QLabel)]
        self.assertIn("Выбрано: https://crawl.localhost/", texts)
        self.assertIn("Без проекта · данные во временной папке до закрытия окна", texts)
        self.screen.set_temporary_results(False)
        self.assertTrue(banner.isHidden())
        self.assertIn("Сведения появятся, когда будет выбран адрес краула", [label.text() for label in self.screen.findChildren(QLabel)])

    def test_narrow_toolbar_moves_clear_and_save_into_overflow(self):
        clear = next(b for b in self.screen.findChildren(QPushButton) if b.text() == "Очистить")
        save = next(b for b in self.screen.findChildren(QPushButton) if b.text() == "Сохранить как проект")
        self.screen.resize(800, 800)
        QApplication.sendEvent(self.screen, QResizeEvent(QSize(800, 800), QSize(640, 480)))
        self.assertTrue(clear.isHidden() and save.isHidden())
        self.screen.resize(1200, 800)
        QApplication.sendEvent(self.screen, QResizeEvent(QSize(1200, 800), QSize(800, 800)))
        self.assertFalse(clear.isHidden() or save.isHidden())


if __name__ == "__main__":
    unittest.main()
