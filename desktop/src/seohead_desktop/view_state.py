"""Main-window methods: view state."""

from __future__ import annotations

from PyQt5.QtCore import (
    QEvent,
    Qt,
)
from PyQt5.QtWidgets import (
    QApplication,
    QLineEdit,
    QTableView,
    QWidget,
)

from .common import (  # noqa: F401
    CONSUMER_ID,
    PAGE_LIMIT,
    ROOT,
    configure_table,
    plain,
    scan_request_key,
)
from .ui.presentation import (
    content_spacing,
    theme_tokens,
)


class ViewStateMixin:
    def set_reduced_motion(self, enabled):
        self.reduced_motion = bool(enabled) or self.system_reduced_motion
        self.work_monitor.set_reduced_motion(self.reduced_motion)
        if self.monitor is not None:
            self.monitor.work_monitor.set_reduced_motion(self.reduced_motion)
        if self.reduced_motion:
            self._navigation_animation.stop()
            self.set_navigation_compact(bool(self.navigation.property("compact")))
        if self.settings:
            self.settings.setValue("reduced_motion", self.reduced_motion)

    def set_navigation_compact(self, compact, animate=False):
        width = theme_tokens()["layout"]["navigation_rail" if compact else "navigation_width"]
        self._navigation_animation.stop()
        if animate and not self.reduced_motion:
            self._navigation_animation.setStartValue(self.navigation.width())
            self._navigation_animation.setEndValue(width)
            self._navigation_animation.start()
        else:
            self.navigation.setFixedWidth(width)
        self.navigation.set_rail(compact)

    def toggle_navigation(self):
        self.set_panel_visible("Навигация", True)
        self.navigation.show()
        self._navigation_compact_intent = not bool(self.navigation.property("compact"))
        self.set_navigation_compact(self._navigation_compact_intent, animate=True)

    def panel_action_changed(self, name, widget, shown):
        if not self._syncing_panel:
            self._panel_intent[name] = bool(shown)
        widget.setVisible(shown)

    def set_panel_visible(self, name, visible, remember=True):
        if remember:
            self._panel_intent[name] = bool(visible)
        action = self.panel_actions.get(name)
        if action is not None:
            self._syncing_panel = True
            action.setChecked(visible)
            {"Навигация": self.navigation, "Сводка": self.overview, "Инспектор URL": self.inspector}[name].setVisible(visible)
            self._syncing_panel = False

    def sync_workspace_width(self):
        if not hasattr(self, "panel_actions"):
            return
        margin, section = content_spacing(self.pages.width())
        for index in range(self.pages.count()):
            page = self.pages.widget(index)
            if page.property("spaciousPage") and page.property("contentMargin") != margin:
                page.layout().setContentsMargins(margin, section, margin, section)
                page.layout().setSpacing(section)
                page.setProperty("contentMargin", margin)
        width = self.width()
        narrow = width <= 960
        if narrow != self._narrow_chrome:
            self._narrow_chrome = narrow
            self.action_finder_button.setText("" if narrow else "Действия и переходы")
            self.finder_hint.setVisible(not narrow)
            self.action_finder_button.setFixedWidth(40 if narrow else 220)
            for picker in (self.project_button, self.scan_button):
                picker.set_compact(narrow)
                picker.setMinimumWidth(120 if narrow else 250 if picker is self.scan_button else 180)
            self.source_badge.setVisible(not narrow)
            self.update_status_tail()
            self.cancel_button.setText("" if narrow else "Остановить " + self.selected_managed_run_id[:8] if self.selected_managed_run_id else "Отменить чтение")
        compact = self.centralWidget().width() < theme_tokens()["layout"]["compact_breakpoint"] and self.prefs.get("view.rail_when_narrow")
        if compact != self._compact:
            self._compact = compact
            self.set_navigation_compact(compact if self._navigation_compact_intent is None else self._navigation_compact_intent)
            wanted = self._panel_intent["Сводка"]
            self.set_panel_visible("Сводка", (not compact if wanted is None else wanted) and not self._focus_mode, remember=False)
            self.audit_workspace.right.setVisible(not compact and not self._focus_mode)

    def eventFilter(self, watched, event):
        if event.type() == QEvent.EnabledChange:
            for combo, button in ((self.project_picker, self.project_button), (self.scan_picker, self.scan_button)):
                if watched is combo:
                    self.sync_picker(combo, button)
        if hasattr(self, "table") and watched is self.table.viewport() and event.type() == QEvent.Resize:
            self.fit_url_columns()
        if watched is self.centralWidget() and event.type() == QEvent.Resize:
            self.sync_workspace_width()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        self.sync_workspace_width()
        super().resizeEvent(event)

    def restore_panels(self):
        self._focus_mode = False
        for action in self.panel_actions.values():
            action.setChecked(True)
        for widget in (self.navigation, self.overview, self.inspector):
            widget.show()
        self.horizontal.setSizes([1000, 280])
        self.vertical.setSizes([440, 230])
        self.audit_workspace.restore_panels()

    def toggle_focus_mode(self):
        self._focus_mode = not self._focus_mode
        if self._focus_mode:
            self._panel_visibility = {name: action.isChecked() for name, action in self.panel_actions.items()}
            for name in ("Сводка", "Инспектор URL"):
                self.set_panel_visible(name, False, remember=False)
            self._audit_visibility = (not self.audit_workspace.detail.isHidden(), not self.audit_workspace.right.isHidden())
            self.audit_workspace.detail.hide()
            self.audit_workspace.right.hide()
        else:
            for name, visible in self._panel_visibility.items():
                self.set_panel_visible(name, visible, remember=False)
            self._compact = None
            self.sync_workspace_width()
            for widget, visible in zip((self.audit_workspace.detail, self.audit_workspace.right), self._audit_visibility):
                widget.setVisible(visible)

    def set_density(self, density):
        self._density = density
        QApplication.instance().setProperty("seohead.density", density)
        height = theme_tokens()["density"][density]
        for table in self.findChildren(QTableView):
            table.verticalHeader().setDefaultSectionSize(height)

    def focus_search(self):
        current = self.pages.currentWidget()
        search = next((field for field in current.findChildren(QLineEdit) if field.isVisible() and field.isEnabled() and ("Поиск" in field.accessibleName() or "Поиск" in field.placeholderText())), None)
        if search:
            search.setFocus()
            search.selectAll()

    def focus_next_region(self):
        areas = [self.navigation, self.pages.currentWidget()]
        if self.pages.currentIndex() == 1:
            areas = [self.navigation, self.table, self.inspector, self.overview]
        current = QApplication.focusWidget()
        index = next((index for index, area in enumerate(areas) if current is area or area.isAncestorOf(current)), -1) if current else -1
        for step in range(1, len(areas) + 1):
            area = areas[(index + step) % len(areas)]
            target = next((widget for widget in [area, *area.findChildren(QWidget)] if widget.isVisible() and widget.isEnabled() and widget.focusPolicy() & Qt.TabFocus), None)
            if target:
                target.setFocus(Qt.ShortcutFocusReason)
                return
