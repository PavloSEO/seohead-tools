"""Main-window methods: status bar, profile menu, display (agent/simple), settings dialog, preference wiring."""

from __future__ import annotations

from PyQt5.QtCore import QPoint
from PyQt5.QtGui import QKeySequence, QPalette
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QApplication,
    QLabel,
    QMenu,
    QShortcut,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
    QWidgetAction,
)

from . import shortcuts, theming
from .ui.controls import Segmented
from .ui.icons import material_icon as icon
from .ui.presentation import ElidedLabel
from .ui.settings.context import SettingsContext
from .ui.settings.dialog import SettingsDialog

UNAVAILABLE = "Недоступно в этой сборке"


class ShellMixin:
    def build_status_bar(self):
        """26 px bar: transient messages on the left, source / display mode / core on the right."""
        bar = self.statusBar()
        bar.setSizeGripEnabled(False)
        self.source_badge = ElidedLabel("Проект не открыт")
        self.source_badge.setObjectName("sourceBadge")
        self.source_badge.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)
        self.source_badge.setMinimumWidth(240)
        self.mode_label = QLabel()
        self.core_label = QLabel()
        for widget in (self.source_badge, self.mode_label, self.core_label):
            bar.addPermanentWidget(widget)
        self.core_label.setText("Ядро найдено" if self.core_executable else "Ядро не найдено")
        self.core_label.setToolTip(self.core_executable or "Команда seohead не найдена в PATH")
        self.update_display_widgets()

    def update_display_widgets(self):
        simple = self.display == "simple"
        self.mode_label.setText("Простой режим" if simple else "С агентом")
        self.simple_pill.setVisible(simple)
        self.agent_pill.setVisible(False)  # shown only from a real agent heartbeat (step 7); never claimed here

    def set_display(self, mode, remember=True):
        if mode not in ("agent", "simple"):
            raise ValueError(mode)
        self.display = mode
        self.navigation.set_display(mode == "simple")
        if self.navigation.current_section() is None:
            self.navigation.select_section("url")
        self.update_display_widgets()
        if remember:
            self.prefs.set("shell.display", mode)

    # Profile menu (bottom-left)
    def show_profile_menu(self):
        menu = self.build_profile_menu()
        menu.exec_(self.navigation.profile.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6)))

    def build_profile_menu(self):
        button = self.navigation.profile
        menu = QMenu(self)
        header = QWidget()
        header_layout = QVBoxLayout(header)
        header_layout.setContentsMargins(10, 8, 10, 6)
        header_layout.setSpacing(2)
        name = QLabel(button.name.text())
        name.setProperty("text_style", "control")
        where = QLabel("локально · этот компьютер")
        where.setProperty("text_style", "meta")
        header_layout.addWidget(name)
        header_layout.addWidget(where)
        self._menu_widget(menu, header)
        menu.addSeparator()
        box = QWidget()
        box_layout = QVBoxLayout(box)
        box_layout.setContentsMargins(10, 4, 10, 8)
        label = QLabel("ОТОБРАЖЕНИЕ")
        label.setProperty("text_style", "overline")
        segmented = Segmented([("agent", "С агентом"), ("simple", "Простой")], self.display, "Отображение")
        segmented.changed.connect(lambda mode: (self.set_display(mode), menu.close()))
        box_layout.addWidget(label)
        box_layout.addWidget(segmented)
        self._menu_widget(menu, box)
        crawler = menu.addAction(icon("travel_explore"), "Быстрый краул без проекта")
        crawler.setEnabled(self.can_open_crawler())
        crawler.setToolTip(UNAVAILABLE)
        settings = menu.addAction(icon("settings"), "Настройки")
        settings.setShortcut(QKeySequence.Preferences)
        settings.triggered.connect(lambda: self.open_settings())
        self._theme_menu(menu)
        self._language_menu(menu)
        mcp = menu.addAction(icon("hub"), "MCP-сервер")
        mcp.triggered.connect(lambda: self.open_settings("mcp"))
        menu.addSeparator()
        cli = menu.addAction(icon("terminal"), "Командная строка")
        cli.setEnabled(False)
        cli.setToolTip(UNAVAILABLE)
        menu.addAction(icon("help"), "Справка", self.show_help, "F1")
        menu.setMinimumWidth(264)
        return menu

    def can_open_crawler(self):
        return hasattr(self, "open_crawler")

    @staticmethod
    def _menu_widget(menu, widget):
        action = QWidgetAction(menu)
        action.setDefaultWidget(widget)
        menu.addAction(action)

    def _theme_menu(self, menu):
        names = {"light": "Светлая", "dark": "Тёмная", "hc": "Высокий контраст", "system": "Как в системе"}
        current = self.prefs.get("view.theme")
        sub = menu.addMenu(icon("palette"), "Тема · " + names[current])
        group = QActionGroup(sub)
        for value, title in names.items():
            action = sub.addAction(title)
            action.setCheckable(True)
            action.setChecked(value == current)
            group.addAction(action)
            action.triggered.connect(lambda _c, v=value: self.prefs.set("view.theme", v))

    def _language_menu(self, menu):
        names = {"ru": "Русский", "en": "English"}
        current = self.prefs.get("view.language")
        sub = menu.addMenu(icon("translate"), "Язык · " + names[current])
        group = QActionGroup(sub)
        for value, title in names.items():
            action = sub.addAction(title)
            action.setCheckable(True)
            action.setChecked(value == current)
            group.addAction(action)
            action.triggered.connect(lambda _c, v=value: self.prefs.set("view.language", v))

    # Settings
    def open_settings(self, section="general"):
        context = SettingsContext(core_executable=self.core_executable, project_directory=self.project_directory)
        dialog = SettingsDialog(self.prefs, context, self, section)
        dialog.exec_()

    def connect_preferences(self):
        self.prefs.changed.connect(self.apply_preference)
        shortcut = QAction("Настройки…", self)
        shortcut.setShortcut(QKeySequence.Preferences)
        shortcut.triggered.connect(lambda: self.open_settings())
        self.addAction(shortcut)
        for key in ("view.theme", "view.density", "view.reduce_motion"):
            self.apply_preference(key)

    def apply_shortcuts(self):
        """Attach the user's bindings (settings → Горячие клавиши) to the existing actions and a few QShortcuts."""
        seqs = shortcuts.sequences(self.prefs)
        none = QKeySequence()
        self.action_finder_action.setShortcut(seqs.get("palette", none))
        self.help_action.setShortcut(seqs.get("help", none))
        self.expand_action.setShortcut(seqs.get("expand_table", none))
        self.find_shortcut.setKey(seqs.get("find_in_table", none))
        handlers = {"new_scan": self.scan_preview, "settings": self.open_settings, "stop_scan": self.cancel_active_work,
                    "copy_url": self.copy_url_selection}
        for old in getattr(self, "_bound_shortcuts", ()):
            old.setEnabled(False)
            old.deleteLater()
        self._bound_shortcuts = []
        for action_id, handler in handlers.items():
            if action_id in seqs:
                shortcut = QShortcut(seqs[action_id], self)
                shortcut.activated.connect(lambda h=handler: h())
                self._bound_shortcuts.append(shortcut)
        for number in range(1, 10):  # fixed: Ctrl+1…9 select the n-th visible section
            shortcut = QShortcut(QKeySequence(f"Ctrl+{number}"), self)
            shortcut.activated.connect(lambda n=number: self.navigation.select_nth(n))
            self._bound_shortcuts.append(shortcut)

    def apply_preference(self, key):
        if key.startswith("keys."):
            self.apply_shortcuts()
            return
        value = self.prefs.get(key)
        if key == "view.theme":
            self.apply_theme(value)
        elif key == "view.density":
            self.set_density(value)
        elif key == "view.reduce_motion":
            self.set_reduced_motion(bool(value) or self.system_reduced_motion)

    def apply_theme(self, choice):
        name = choice
        if choice == "system":
            if theming.system_prefers_high_contrast():
                name = "hc"
            else:
                window = QApplication.instance().palette().color(QPalette.Window)
                name = "dark" if window.lightness() < 128 else "light"
        if name == theming.active_theme() and QApplication.instance().styleSheet():
            return
        from .app import load_theme

        load_theme(QApplication.instance(), name)
        self.retheme()

    def retheme(self):
        """Re-tint icons that were created with a fixed colour (everything else follows the QSS)."""
        r = theming.roles()
        self.nav_toggle.setIcon(icon("side_navigation"))
        self.refresh_button.setIcon(icon("refresh"))
        self.action_finder_button.setIcon(icon("search"))
        self.cancel_button.setIcon(icon("stop", r["error"]))
        self.new_scan.setIcon(icon("play_arrow", r["on_primary"]))
        self.navigation.set_display(self.display == "simple")
        for button, combo in ((self.project_button, self.project_picker), (self.scan_button, self.scan_picker)):
            self.sync_picker(combo, button)
        self.update_display_widgets()

