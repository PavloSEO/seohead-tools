import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QApplication

from seohead_desktop import theming
from seohead_desktop.app import MainWindow, load_theme
from seohead_desktop.ui.settings.dialog import SettingsDialog
from seohead_desktop.ui.shell import ROLE_COUNT, ROLE_DOT, ROLE_ID
from tests._qt import sweep_widgets


def section_ids(nav):
    lst = nav.list
    return [lst.item(r).data(ROLE_ID) for r in range(lst.count()) if lst.item(r).data(ROLE_ID)]


def headers(nav):
    lst = nav.list
    return [lst.item(r).text() for r in range(lst.count()) if not lst.item(r).data(ROLE_ID)]


class ShellV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_theme(cls.app, "light")

    def setUp(self):
        sweep_widgets()
        self.window = MainWindow(persistent=False)

    def tearDown(self):
        self.window.close()
        self.app.processEvents()
        self.window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        if theming.active_theme() != "light":
            load_theme(self.app, "light")

    def test_agent_display_sections_follow_the_canvas(self):
        nav = self.window.navigation
        self.assertEqual(section_ids(nav), ["work", "inbox", "scans", "url", "issues", "compare", "search", "reports", "log"])
        self.assertEqual(headers(nav), ["Данные", "Результат"])

    def test_simple_display_has_no_agent_sections_and_is_remembered(self):
        self.window.set_display("simple")
        nav = self.window.navigation
        ids = section_ids(nav)
        self.assertEqual(ids[0], "work")  # SHELL-CANON §3: same labels, the agent items are only hidden
        self.assertNotIn("inbox", ids)
        self.window.navigation.select_section("work")
        self.assertEqual(self.window.pages.currentIndex(), 4)  # «Работа» is the simple task list in Simple
        self.assertTrue(self.window.simple_pill.isVisibleTo(self.window))
        self.assertEqual(self.window.prefs.get("shell.display"), "simple")
        self.assertEqual(self.window.mode_label.text(), "Простой режим · агент и MCP выключены")
        self.window.set_display("agent")
        self.assertEqual(section_ids(nav)[:2], ["work", "inbox"])
        self.assertEqual(self.window.pages.currentIndex(), 0)
        with self.assertRaises(ValueError):
            self.window.set_display("both")

    def test_agent_pill_is_never_claimed_without_a_heartbeat(self):
        self.assertFalse(self.window.agent_pill.isVisibleTo(self.window))

    def test_rail_and_wide_navigation_widths_come_from_tokens(self):
        layout = theming.metrics()["layout"]
        self.window.set_navigation_compact(True)
        self.assertEqual(self.window.navigation.width(), layout["rail"])
        self.assertTrue(self.window.navigation.property("compact"))
        self.assertFalse(self.window.navigation.profile._texts.isVisibleTo(self.window.navigation))
        self.window.set_navigation_compact(False)
        self.assertEqual(self.window.navigation.width(), layout["navigation"])
        self.assertTrue(self.window.navigation.profile._texts.isVisibleTo(self.window.navigation))

    def test_top_bar_height_and_labels(self):
        top = self.window.findChild(type(self.window.centralWidget()), "topbar")
        self.assertEqual(top.height(), theming.metrics()["layout"]["topbar"])
        self.assertEqual(self.window.project_button.title.text(), "Проект не открыт")
        self.assertEqual(self.window.scan_button.title.text(), "Нет сохранённых сканов")
        self.assertFalse(self.window.scan_button.isEnabled())
        self.assertFalse(self.window.scan_state_badge.isVisibleTo(self.window))

    def test_pickers_mirror_their_combo_boxes(self):
        self.window.scan_picker.clear()
        self.window.scan_picker.addItem("Скан №2", {"uuid": "x"})
        self.window.scan_picker.setEnabled(True)
        self.assertEqual(self.window.scan_button.title.text(), "Скан №2")
        self.assertTrue(self.window.scan_button.isEnabled())
        self.window.scan_picker.setEnabled(False)
        self.window.show()
        self.app.processEvents()
        self.assertFalse(self.window.scan_button.isEnabled())

    def test_picker_menu_choice_activates_the_combo(self):
        self.window.scan_picker.addItem("Скан №3", {"uuid": "y"})
        self.window.scan_picker.setEnabled(True)
        seen = []
        self.window.scan_picker.activated.connect(seen.append)
        self.window._pick(self.window.scan_picker, 1)
        self.assertEqual(seen, [1])
        self.assertEqual(self.window.scan_button.title.text(), "Скан №3")

    def test_profile_menu_contents_and_unavailable_actions(self):
        menu = self.window.build_profile_menu()
        actions = {a.text(): a for a in menu.actions() if a.text()}
        for title in ("Быстрый краул без проекта", "Настройки", "MCP-сервер", "Командная строка", "Справка"):
            self.assertIn(title, actions)
        self.assertFalse(actions["Быстрый краул без проекта"].isEnabled())
        self.assertFalse(actions["Командная строка"].isEnabled())
        self.assertEqual(actions["Командная строка"].toolTip(), "Недоступно в этой сборке")
        self.assertTrue(any(t.startswith("Тема · ") for t in actions))
        self.assertTrue(any(t.startswith("Язык · ") for t in actions))

    def test_profile_menu_opens_at_the_profile_button(self):
        shown = []
        with patch("seohead_desktop.shell_mixin.QMenu.exec_", lambda menu, pos=None: shown.append(pos)):
            self.window.show_profile_menu()
        self.assertEqual(len(shown), 1)

    def test_settings_open_as_a_workspace_tab_and_keep_the_project_tab(self):
        before = self.window._active_workspace_id
        self.window.show()
        self.window.open_settings("view")
        self.app.processEvents()
        settings_tabs = [c for c in self.window.workspace_tabs.contexts() if c.view_id == "settings"]
        self.assertEqual(len(settings_tabs), 1)
        self.assertEqual(self.window._active_workspace_id, settings_tabs[0].id)
        self.assertTrue(self.window.settings_view.isVisible())
        self.assertFalse(self.window.pages.isVisible())
        self.assertEqual(self.window.settings_view.current_section(), "view")
        self.window.open_settings("mcp")  # reuses the same tab
        self.assertEqual(len([c for c in self.window.workspace_tabs.contexts() if c.view_id == "settings"]), 1)
        self.assertEqual(self.window.settings_view.current_section(), "mcp")
        self.window.workspace_tabs.select(before)
        self.app.processEvents()
        self.assertFalse(self.window.settings_view.isVisible())
        self.assertTrue(self.window.pages.isVisible())
        self.assertEqual(self.window._active_workspace_id, before)

    def test_done_closes_the_settings_tab_and_nav_click_leaves_settings(self):
        self.window.show()
        self.window.open_settings()
        self.app.processEvents()
        self.window.settings_view.accept()
        self.app.processEvents()
        self.assertFalse([c for c in self.window.workspace_tabs.contexts() if c.view_id == "settings"])
        self.assertTrue(self.window.pages.isVisible())
        self.window.open_settings()
        self.app.processEvents()
        self.window.navigation.select_section("scans")
        self.app.processEvents()
        self.assertFalse(self.window.settings_view.isVisible())
        self.assertEqual(self.window.pages.currentIndex(), 5)

    def test_settings_dialog_is_the_fallback_when_the_tab_strip_is_full(self):
        opened = []
        self.window.workspace_tabs.max_tabs = len(self.window.workspace_tabs.contexts())
        with patch.object(SettingsDialog, "exec_", lambda dialog: opened.append(dialog.current_section())):
            self.window.open_settings("view")
        self.assertEqual(opened, ["view"])

    def test_preferences_drive_theme_density_and_motion(self):
        self.window.prefs.set("view.theme", "dark")
        self.assertEqual(theming.active_theme(), "dark")
        self.assertIn(theming.roles("dark")["surface"].lower(), self.app.styleSheet().lower())
        self.window.prefs.set("view.density", "compact")
        self.assertEqual(self.window._density, "compact")
        self.window.prefs.set("view.reduce_motion", True)
        self.assertTrue(self.window.reduced_motion)

    def test_system_theme_choice_resolves_to_a_concrete_theme(self):
        with patch.object(theming, "system_prefers_high_contrast", return_value=True):
            self.window.prefs.set("view.theme", "system")
        self.assertEqual(theming.active_theme(), "hc")

    def test_user_shortcut_rebinds_the_palette_action(self):
        self.window.prefs.set("keys.palette", "Ctrl+J")
        self.assertEqual(self.window.action_finder_action.shortcut(), QKeySequence("Ctrl+J"))
        self.window.prefs.reset("keys.")
        self.assertEqual(self.window.action_finder_action.shortcut(), QKeySequence("Ctrl+K"))

    def test_ctrl_digit_selects_the_nth_visible_section(self):
        self.assertTrue(self.window.navigation.select_nth(3))
        self.assertEqual(self.window.navigation.current_section(), "scans")
        self.assertFalse(self.window.navigation.select_nth(99))

    def test_counts_and_unread_dot_are_only_shown_when_measured(self):
        self.window.load_unread({"count": 2})
        item = self.window.navigation.list._items["inbox"]
        self.assertEqual((item.data(ROLE_COUNT), item.data(ROLE_DOT)), (2, True))
        self.window.load_unread({"count": None})
        self.assertEqual(item.data(ROLE_COUNT), "")

    def test_no_demo_wording_in_real_mode_chrome(self):
        texts = [self.window.windowTitle(), self.window.project_button.title.text(), self.window.scan_button.title.text(),
                 self.window.source_badge.text(), self.window.mode_label.text(), self.window.core_label.text()]
        texts += [self.window.navigation.list.item(r).text() for r in range(self.window.navigation.list.count())]
        self.assertFalse([t for t in texts if "Демо" in t])


if __name__ == "__main__":
    unittest.main()
