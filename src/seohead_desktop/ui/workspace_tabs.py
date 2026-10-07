"""Bounded native workspace tabs containing immutable view contexts only.

Controllers, gateways and scan processes belong to the hosting window. A close
button emits an intent; removing its descriptor never stops a scan or closes the
window. The host decides what to show after the final context is removed.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from uuid import uuid4

from PyQt5.QtCore import QByteArray, Qt, pyqtSignal
from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QHBoxLayout, QMenu, QShortcut, QTabBar, QToolButton, QWidget

from .icons import material_icon

MAX_WORKSPACE_TABS = 12


def _freeze(value, depth=0):
    if depth > 12:
        raise ValueError("Workspace state nesting is too deep")
    if value is None or type(value) in {str, int, bool, bytes}:
        return value
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError("Workspace state numbers must be finite")
        return value
    if isinstance(value, (bytearray, QByteArray)):
        return bytes(value)
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise TypeError("Workspace state keys must be strings")
        return MappingProxyType(
            {key: _freeze(item, depth + 1) for key, item in value.items()}
        )
    if isinstance(value, (tuple, list)):
        return tuple(_freeze(item, depth + 1) for item in value)
    raise TypeError("Workspace state accepts data only, never controllers or widgets")


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class WorkspaceContext:
    """Identity and a deep-frozen snapshot; state keys remain owned by the host.

    Typical state keys are search, filter, offset, selected_url, selection and
    splitter bytes. No page records or live service objects are needed here.
    state_dict() returns an independent mutable copy for capture/restore code.
    """

    id: str = field(default_factory=lambda: uuid4().hex)
    project_uuid: str | None = None
    project_root: str | None = None
    project_label: str = "Новая вкладка"
    scan_uuid: str | None = None
    view_id: str = "work"
    state: Mapping[str, object] = field(default_factory=dict, hash=False)

    def __post_init__(self):
        for name in ("id", "project_label", "view_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Workspace {name} must be a non-empty string")
        for name in ("project_uuid", "project_root", "scan_uuid"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise TypeError(f"Workspace {name} must be a string or None")
        if not isinstance(self.state, Mapping):
            raise TypeError("Workspace state must be a mapping")
        object.__setattr__(self, "state", _freeze(self.state))

    def state_dict(self) -> dict[str, object]:
        return _thaw(self.state)


class WorkspaceTabs(QWidget):
    """A native strip; selected emits only when the active context ID changes."""

    selected = pyqtSignal(str)
    closeRequested = pyqtSignal(str)
    newRequested = pyqtSignal()
    duplicateRequested = pyqtSignal(str)

    def __init__(self, parent=None, *, max_tabs=MAX_WORKSPACE_TABS):
        super().__init__(parent)
        if type(max_tabs) is not int or not 1 <= max_tabs <= MAX_WORKSPACE_TABS:
            raise ValueError(
                f"Workspace tab limit must be between 1 and {MAX_WORKSPACE_TABS}"
            )
        self.max_tabs = max_tabs
        self._contexts = {}
        self._titles = {}
        self._custom_titles = set()
        self._active_id = None
        self._shortcuts = []
        self._shortcut_owner = None
        self.setObjectName("workspaceTabs")
        self.setAccessibleName("Рабочие вкладки SEOHEAD")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        self.tabbar = QTabBar()
        self.tabbar.setObjectName("workspaceTabBar")
        self.tabbar.setAccessibleName("Проекты, сканы и представления")
        self.tabbar.setUsesScrollButtons(True)
        self.tabbar.setExpanding(False)
        self.tabbar.setMovable(True)
        self.tabbar.setTabsClosable(True)
        self.tabbar.setElideMode(Qt.ElideRight)
        self.tabbar.setSelectionBehaviorOnRemove(QTabBar.SelectLeftTab)
        self.tabbar.setDrawBase(False)
        layout.addWidget(self.tabbar, 1)
        self.new_button = QToolButton()
        self.new_button.setObjectName("workspaceNewTab")
        self.new_button.setIcon(material_icon("add_circle"))
        self.new_button.setAccessibleName("Новая рабочая вкладка")
        self.new_button.setToolTip("Новая рабочая вкладка")
        self.new_button.clicked.connect(self.request_new)
        layout.addWidget(self.new_button)
        self.overflow_button = QToolButton()
        self.overflow_button.setObjectName("workspaceOverflow")
        self.overflow_button.setIcon(material_icon("more_horiz"))
        self.overflow_button.setAccessibleName("Все рабочие вкладки и действия")
        self.overflow_button.setToolTip("Все рабочие вкладки и действия")
        self.overflow_button.setPopupMode(QToolButton.InstantPopup)
        self.menu = QMenu(self.overflow_button)
        self.overflow_button.setMenu(self.menu)
        self.menu.aboutToShow.connect(self._rebuild_menu)
        layout.addWidget(self.overflow_button)
        self.tabbar.currentChanged.connect(self._sync_selected)
        self.tabbar.tabCloseRequested.connect(self._close_index)
        self.tabbar.tabMoved.connect(self._tab_moved)
        self._update_capacity()
        self._rebuild_menu()

    @property
    def current_id(self) -> str | None:
        index = self.tabbar.currentIndex()
        return self.tabbar.tabData(index) if index >= 0 else None

    def contexts(self) -> tuple[WorkspaceContext, ...]:
        return tuple(
            self._contexts[self.tabbar.tabData(index)]
            for index in range(self.tabbar.count())
        )

    def _index(self, id):
        return next(
            (
                index
                for index in range(self.tabbar.count())
                if self.tabbar.tabData(index) == id
            ),
            -1,
        )

    def add(self, context: WorkspaceContext, title=None, icon=None, select=True):
        if not isinstance(context, WorkspaceContext):
            raise TypeError("add requires a WorkspaceContext")
        if context.id in self._contexts:
            raise ValueError("Workspace context ID already exists")
        if len(self._contexts) >= self.max_tabs:
            raise ValueError(f"At most {self.max_tabs} workspaces may be open")
        label = context.project_label if title is None else self._checked_title(title)
        self._contexts[context.id] = context
        self._titles[context.id] = label
        if title is not None:
            self._custom_titles.add(context.id)
        blocked = self.tabbar.blockSignals(True)
        index = self.tabbar.addTab(icon or material_icon("folder_open"), label)
        self.tabbar.setTabData(index, context.id)
        self.tabbar.setTabToolTip(index, label)
        self._label_close_button(index, label)
        if select:
            self.tabbar.setCurrentIndex(index)
        self.tabbar.blockSignals(blocked)
        self._update_capacity()
        self._sync_selected()
        return context.id

    def remove(self, id) -> WorkspaceContext | None:
        context = self._contexts.get(id)
        if context is None:
            return None
        blocked = self.tabbar.blockSignals(True)
        self.tabbar.removeTab(self._index(id))
        self._contexts.pop(id)
        self._titles.pop(id)
        self._custom_titles.discard(id)
        self.tabbar.blockSignals(blocked)
        self._update_capacity()
        self._sync_selected()
        return context

    def select(self, id):
        if id not in self._contexts:
            raise KeyError(id)
        self.tabbar.setCurrentIndex(self._index(id))
        self._sync_selected()

    def update_context(self, context_id, **fields) -> WorkspaceContext:
        if "id" in fields:
            raise ValueError("Workspace identity cannot be changed")
        updated = replace(self._contexts[context_id], **fields)
        self._contexts[context_id] = updated
        if "project_label" in fields and context_id not in self._custom_titles:
            self._titles[context_id] = updated.project_label
            self.tabbar.setTabText(self._index(context_id), updated.project_label)
            self.tabbar.setTabToolTip(self._index(context_id), updated.project_label)
            self._label_close_button(self._index(context_id), updated.project_label)
        return updated

    def update_title(self, id, title, icon=None):
        if id not in self._contexts:
            raise KeyError(id)
        title = self._checked_title(title)
        self._custom_titles.add(id)
        self._titles[id] = title
        index = self._index(id)
        self.tabbar.setTabText(index, title)
        self.tabbar.setTabToolTip(index, title)
        self._label_close_button(index, title)
        if icon is not None:
            self.tabbar.setTabIcon(index, icon)

    def update_state(self, id, state) -> WorkspaceContext:
        return self.update_context(id, state=state)

    def _label_close_button(self, index, title):
        for side in (QTabBar.LeftSide, QTabBar.RightSide):
            button = self.tabbar.tabButton(index, side)
            if button is not None:
                button.setAccessibleName("Закрыть вкладку " + title)
                button.setToolTip("Закрыть вкладку " + title)

    @staticmethod
    def _checked_title(title):
        if not isinstance(title, str) or not title.strip():
            raise ValueError("Workspace title must be a non-empty string")
        return title

    def request_new(self):
        if len(self._contexts) < self.max_tabs:
            self.newRequested.emit()

    def request_close(self, id=None):
        id = self.current_id if id is None else id
        if id in self._contexts:
            self.closeRequested.emit(id)

    def request_duplicate(self, id=None):
        id = self.current_id if id is None else id
        if id in self._contexts and len(self._contexts) < self.max_tabs:
            self.duplicateRequested.emit(id)

    def select_next(self):
        if self.tabbar.count():
            self.tabbar.setCurrentIndex(
                (self.tabbar.currentIndex() + 1) % self.tabbar.count()
            )

    def select_previous(self):
        if self.tabbar.count():
            self.tabbar.setCurrentIndex(
                (self.tabbar.currentIndex() - 1) % self.tabbar.count()
            )

    def _sync_selected(self, *_args):
        current = self.current_id
        if current == self._active_id:
            return
        self._active_id = current
        if current is not None:
            self.selected.emit(current)

    def _close_index(self, index):
        if 0 <= index < self.tabbar.count():
            self.request_close(self.tabbar.tabData(index))

    def _tab_moved(self, *_args):
        self._sync_selected()

    def _update_capacity(self):
        available = len(self._contexts) < self.max_tabs
        self.new_button.setEnabled(available)
        self.new_button.setToolTip(
            "Новая рабочая вкладка"
            if available
            else f"Открыто максимум вкладок: {self.max_tabs}"
        )

    def _rebuild_menu(self):
        self.menu.clear()
        create = self.menu.addAction(material_icon("add_circle"), "Новая вкладка")
        create.setEnabled(len(self._contexts) < self.max_tabs)
        create.triggered.connect(self.request_new)
        duplicate = self.menu.addAction(
            material_icon("content_copy"), "Дублировать текущую вкладку"
        )
        duplicate.setEnabled(
            self.current_id is not None and len(self._contexts) < self.max_tabs
        )
        duplicate.triggered.connect(lambda: self.request_duplicate())
        close = self.menu.addAction(material_icon("close"), "Закрыть текущую вкладку")
        close.setEnabled(self.current_id is not None)
        close.triggered.connect(lambda: self.request_close())
        self.menu.addSeparator()
        for context in self.contexts():
            action = self.menu.addAction(self._titles[context.id])
            action.setCheckable(True)
            action.setChecked(context.id == self.current_id)
            action.triggered.connect(
                lambda checked=False, id=context.id: self.select(id)
            )

    def install_shortcuts(self, owner):
        """Opt in once per window; never install duplicate bindings in the host."""
        if self._shortcut_owner is owner:
            return
        if self._shortcut_owner is not None:
            raise ValueError(
                "Workspace shortcuts are already attached to another window"
            )
        if not isinstance(owner, QWidget):
            raise TypeError("Shortcut owner must be a QWidget")
        self._shortcut_owner = owner
        bindings = (
            (QKeySequence.AddTab, self.request_new, False),
            (QKeySequence.Close, lambda: self.request_close(), False),
            (QKeySequence.NextChild, self.select_next, True),
            (QKeySequence.PreviousChild, self.select_previous, True),
        )
        seen = set()
        for standard, callback, tab_only in bindings:
            for sequence in QKeySequence.keyBindings(standard):
                text = sequence.toString(QKeySequence.PortableText)
                if (
                    text in seen
                    or tab_only
                    and text.split("+")[-1] not in {"Tab", "Backtab"}
                ):
                    continue
                seen.add(text)
                shortcut = QShortcut(sequence, owner)
                shortcut.setContext(Qt.WindowShortcut)
                shortcut.activated.connect(callback)
                self.destroyed.connect(shortcut.deleteLater)
                self._shortcuts.append(shortcut)
