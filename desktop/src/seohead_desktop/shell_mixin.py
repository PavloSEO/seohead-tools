"""Main-window methods: status bar, profile menu, display (agent/simple), settings dialog, preference wiring."""

from __future__ import annotations

from PyQt5.QtCore import QPoint, Qt
from PyQt5.QtGui import QKeySequence, QPalette
from PyQt5.QtWidgets import (
    QAction,
    QActionGroup,
    QApplication,
    QLabel,
    QMenu,
    QShortcut,
    QSizePolicy,
    QWidgetAction,
)

from . import i18n, shortcuts, theming
from .ui.icons import material_icon as icon
from .ui.presentation import ElidedLabel
from .ui.settings.context import SettingsContext
from .ui.settings.dialog import SettingsDialog

tr, trf = i18n.tr, i18n.trf

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
        self.core_label.setText(tr("Ядро найдено" if self.core_executable else "Ядро не найдено"))
        self.core_label.setToolTip(self.core_executable or tr("Команда seohead не найдена в PATH"))
        self.update_display_widgets()

    def update_display_widgets(self):
        from .ui.profile_menu import update_profile

        update_profile(self)
        simple = self.display == "simple"
        self.mode_label.setText(tr("Простой режим · агент и MCP выключены" if simple else "С агентом"))
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
        try:
            menu.exec_(self.navigation.profile.mapToGlobal(QPoint(0, -menu.sizeHint().height() - 6)))
        finally:
            self.navigation.profile.setFocus(Qt.PopupFocusReason)
            menu.deleteLater()

    def build_profile_menu(self):
        from .ui.profile_menu import build_profile_menu

        return build_profile_menu(self)

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
        from .source_service import SourceService

        if not hasattr(self, "_source_service"):
            self._source_service = SourceService(self.core_executable, self)
        self._source_service.executable = self.core_executable
        return SettingsContext(core_executable=self.core_executable, project_directory=self.project_directory,
                               actions={"providers": self.request_providers,
                                        "sources": lambda **kwargs: self._source_service.request(project=self.project_directory, **kwargs)})

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

