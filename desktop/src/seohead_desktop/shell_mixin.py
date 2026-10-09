"""Main-window methods: status bar, profile menu, display (agent/simple), settings dialog, preference wiring."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from importlib import metadata

from PyQt5.QtCore import QPoint, QTimer
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

from . import i18n, shortcuts, theming
from .screens.scan_common import parse_time
from .ui.controls import Segmented
from .ui.icons import material_icon as icon
from .ui.presentation import ElidedLabel
from .ui.settings.context import SettingsContext
from .ui.settings.dialog import SettingsDialog

tr, trf, joined = i18n.tr, i18n.trf, i18n.joined

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
        self.project_label = QLabel()
        self.scans_label = QLabel()
        self.mode_label = QLabel()
        self.core_label = QLabel()
        self.observed_label = QLabel()
        for widget in (self.project_label, self.scans_label, self.mode_label, self.core_label, self.observed_label):
            widget.setContentsMargins(8, 0, 8, 0)
        for widget in (self.source_badge, self.project_label, self.scans_label, self.mode_label, self.core_label, self.observed_label):
            bar.addPermanentWidget(widget)
        self.data_changed.connect(lambda _kind: self.update_status_tail())
        self._status_timer = QTimer(self)  # keeps «N с назад» honest between observations
        self._status_timer.setInterval(5000)
        self._status_timer.timeout.connect(self.update_status_tail)
        self._status_timer.start()
        self.update_display_widgets()

    @staticmethod
    def installed_core_version(core_executable):
        """«3.4» when the core executable belongs to this application's environment and its package version is known."""
        if not core_executable or os.path.dirname(core_executable) != os.path.dirname(sys.executable):
            return None
        try:
            parts = metadata.version("seohead-seotools").split(".")
        except metadata.PackageNotFoundError:
            return None
        return ".".join(parts[:2]) if len(parts) >= 2 else None

    def core_state(self):
        """(text, tooltip): the real state of the core. «не найдено» only when nothing answered and no executable is known."""
        answered = bool(self.mcp_ready or self.project_result is not None)
        if not self.core_executable and not answered:
            return tr("Ядро не найдено"), tr("Команда seohead не найдена в PATH")
        version = self.installed_core_version(self.core_executable)
        path = self.core_executable or ""
        if version:
            return trf("Ядро seohead {version}", version=version), path
        return tr("Ядро подключено · версия неизвестна" if answered else "Ядро найдено"), joined("\n", [path, tr("Версию ядро пока не сообщает")]) if path else tr("Версию ядро пока не сообщает")

    def update_status_tail(self):
        """Right side (SHELL-CANON §6): at most three segments — active scans · display mode · core. The project name lives
        in the window centre text and the observation age in the core tooltip."""
        try:
            opened = bool(self.project_directory)
            self.project_label.setVisible(False)
            self.observed_label.setVisible(False)
            self.scans_label.setVisible(opened)
            if opened:
                from .screens.scan_common import build_rows

                self.scans_label.setText(trf("Активных сканов: {n}", n=sum(1 for row in build_rows(self) if row.active)))
            text, tip = self.core_state()
            observed = ""
            if opened:
                stamp = parse_time(self.observed_at) if isinstance(self.observed_at, str) else None
                if stamp is None:
                    observed = tr("Наблюдение: нет данных") + " · " + tr("Недоступно в этой версии ядра")
                else:
                    seconds = max(0, int((datetime.now(timezone.utc) - stamp).total_seconds()))
                    ago = trf("{n} с", n=seconds) if seconds < 60 else trf("{n} мин", n=seconds // 60) if seconds < 3600 else trf("{n} ч", n=seconds // 3600)
                    observed = trf("Наблюдение: {ago} назад", ago=ago)
            key = (self.core_executable, bool(self.mcp_ready or self.project_result is not None))
            if key != getattr(self, "_core_key", None) or "seohead" in text:  # plain texts follow the language by retranslate
                self._core_key = key
                self.core_label.setText(text)
            self.core_label.setToolTip(joined("\n", [tip, observed]))
        except RuntimeError:  # the window was deleted while a timer fired
            pass

    def update_display_widgets(self):
        simple = self.display == "simple"
        self.mode_label.setText(tr("Простой режим · агент и MCP выключены" if simple else "С агентом"))
        self.simple_pill.setVisible(simple)
        self.agent_pill.setVisible(False)  # shown only from a real agent heartbeat (step 7); never claimed here
        self.update_status_tail()

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
        i18n.retranslate(menu)
        return menu

    def show_display_menu(self):
        """Chip «Простой режим ▾» (SHELL-CANON §2b): display switch and crawler entry."""
        menu = QMenu(self)
        group = QActionGroup(menu)
        for mode, title in (("agent", "С агентом"), ("simple", "Простой режим")):
            action = menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(mode == self.display)
            group.addAction(action)
            action.triggered.connect(lambda _c, m=mode: self.set_display(m))
        menu.addSeparator()
        crawler = menu.addAction(icon("travel_explore"), "Быстрый краул без проекта")
        crawler.setEnabled(self.can_open_crawler())
        crawler.setToolTip(UNAVAILABLE)
        i18n.retranslate(menu)
        menu.exec_(self.simple_pill.mapToGlobal(QPoint(0, self.simple_pill.height() + 4)))

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
        sub = menu.addMenu(icon("palette"), trf("Тема · {name}", name=names[current]))
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
        sub = menu.addMenu(icon("translate"), trf("Язык · {name}", name=names[current]))
        group = QActionGroup(sub)
        for value, title in names.items():
            action = sub.addAction(title)
            action.setCheckable(True)
            action.setChecked(value == current)
            group.addAction(action)
            action.triggered.connect(lambda _c, v=value: self.prefs.set("view.language", v))

    # Settings
    def request_providers(self, callback, on_error):
        """Settings → Источники данных: the core's provider-readiness (local, no network), delivered on the UI thread."""
        self._provider_handlers = (callback, on_error)
        if not self.core_executable:
            on_error(tr("CLI ядра seohead не найден"))
            return
        self.start_command("providers", "seo_provider_readiness", {}, self._providers_loaded)

    def _providers_loaded(self, result):
        self._deliver_providers(0, result)

    def providers_failed(self, text):
        self._deliver_providers(1, text)

    def _deliver_providers(self, index, value):
        handlers = getattr(self, "_provider_handlers", None)
        if handlers:
            try:
                handlers[index](value)
            except RuntimeError:
                pass  # the settings dialog was closed before the answer arrived

    def settings_context(self):
        return SettingsContext(core_executable=self.core_executable, project_directory=self.project_directory,
                               actions={"providers": self.request_providers})

    def open_settings(self, section="general"):
        """Settings are a modal window over the application (sheet Settings), never a workspace tab."""
        SettingsDialog(self.prefs, self.settings_context(), self, section).exec_()

    def connect_preferences(self):
        self.prefs.changed.connect(self.apply_preference)
        shortcut = QAction("Настройки…", self)
        shortcut.setShortcut(QKeySequence.Preferences)
        shortcut.triggered.connect(lambda: self.open_settings())
        self.addAction(shortcut)
        i18n.signals.changed.connect(self.apply_language)
        for key in ("view.language", "view.theme", "view.density", "view.reduce_motion"):
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
        if key == "view.language":
            i18n.set_language(value)
        elif key == "view.theme":
            self.apply_theme(value)
        elif key == "view.density":
            self.set_density(value)
        elif key == "view.reduce_motion":
            self.set_reduced_motion(bool(value) or self.system_reduced_motion)

    def apply_language(self, _language=None):
        """The shell, the settings and every open child window follow the language; selection and data stay as they are."""
        try:
            i18n.retranslate(self)
            for combo, button in ((self.project_picker, self.project_button), (self.scan_picker, self.scan_button)):
                self.sync_picker(combo, button)
            self.update_display_widgets()
        except RuntimeError:  # the window was deleted while a test or shutdown was running
            pass

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

