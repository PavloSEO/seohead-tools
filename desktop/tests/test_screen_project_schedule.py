import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QLabel, QPushButton, QTabBar

from seohead_desktop import i18n
from seohead_desktop.app import load_theme
from seohead_desktop.qt import app as qt_app
from seohead_desktop.screens.project_schedule_page import ISSUE, SchedulePage
from seohead_desktop.screens.project_sources_page import ProjectSettingsDialog
from seohead_desktop.ui.kit import StatePanel
from tests._qt import sweep_widgets
from tests.test_screen_project_sources import Host

SAMPLE_NAMES = ("Полный обход", "Главные разделы", "Проверка 4xx", "Перепроверка задач")


class ScheduleTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        load_theme(cls.app, "light")

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")
        self.addCleanup(i18n.set_language, "ru")
        self.host = Host()
        self.dialog = ProjectSettingsDialog(self.host)
        self.dialog.resize(940, 800)
        self.dialog.show()
        self.app.processEvents()

    def tearDown(self):
        self.dialog.close()
        self.dialog.deleteLater()
        self.app.processEvents()

    def test_dialog_has_connections_and_schedule_tabs(self):
        tabs = self.dialog.findChild(QTabBar, "settingsTabs")
        self.assertEqual([tabs.tabText(i) for i in range(tabs.count())], ["Источники данных", "Расписание"])
        self.assertIs(self.dialog.pages.currentWidget(), self.dialog.page)
        tabs.setCurrentIndex(1)
        self.assertIs(self.dialog.pages.currentWidget(), self.dialog.schedule)

    def test_schedule_page_shows_no_sample_schedules_and_an_honest_waiting_state(self):
        page = SchedulePage(self.host)
        page.resize(900, 640)
        page.show()
        self.app.processEvents()
        visible = " ".join(label.text() for label in page.findChildren(QLabel))
        for name in SAMPLE_NAMES:
            self.assertNotIn(name, visible)
        panels = [w for w in page.findChildren(StatePanel) if w.kind == "waiting"]
        self.assertEqual(len(panels), 1)
        badges = [w for w in page.findChildren(QLabel) if w.property("waiting_issue") == ISSUE]
        self.assertEqual(len(badges), 3)  # the banner, the schedule list, the run history
        self.assertFalse(page.add_button.isEnabled())
        self.assertIn("Недоступно в этой версии ядра", page.add_button.toolTip())
        self.assertNotIn(str(ISSUE), visible)
        page.close()

    def test_no_schedule_action_starts_or_writes_anything(self):
        buttons = [b.text() for b in self.dialog.schedule.findChildren(QPushButton) if b.isEnabled()]
        self.assertEqual(buttons, [])


if __name__ == "__main__":
    unittest.main()
