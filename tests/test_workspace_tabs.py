"""Workspace identity, immutable capture, tab lifetime and keyboard contracts."""

import unittest
from dataclasses import FrozenInstanceError
from unittest.mock import Mock

from PyQt5.QtCore import QByteArray, Qt
from PyQt5.QtGui import QIcon, QKeySequence
from PyQt5.QtTest import QSignalSpy, QTest
from PyQt5.QtWidgets import (
    QApplication,
    QLineEdit,
    QMainWindow,
    QTabBar,
    QVBoxLayout,
    QWidget,
)

from seohead_desktop.ui.workspace_tabs import WorkspaceContext, WorkspaceTabs


class WorkspaceTabsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        cls.app.setStyle("Fusion")

    def setUp(self):
        self.window = QMainWindow()
        central = QWidget()
        self.window.setCentralWidget(central)
        layout = QVBoxLayout(central)
        self.tabs = WorkspaceTabs()
        layout.addWidget(self.tabs)
        self.body = QLineEdit()
        layout.addWidget(self.body)
        self.window.resize(800, 260)

    def tearDown(self):
        self.window.close()
        self.window.deleteLater()
        self.app.processEvents()

    def add(self, id, **kwargs):
        return self.tabs.add(WorkspaceContext(id=id, project_label=id), **kwargs)

    def test_context_deep_freezes_state_and_returns_independent_capture(self):
        raw = {
            "search": "chair",
            "filter": {"http": [200, 301]},
            "offset": 100,
            "selected_url": "https://example.test/a",
            "splitter": QByteArray(b"saved-layout"),
        }
        context = WorkspaceContext(
            id="a",
            project_uuid="p",
            project_root="/project",
            scan_uuid="s",
            view_id="audit",
            state=raw,
        )
        raw["filter"]["http"].append(404)
        raw["splitter"].append(b"mutated")
        self.assertEqual(context.state["filter"]["http"], (200, 301))
        self.assertEqual(context.state["splitter"], b"saved-layout")
        with self.assertRaises(TypeError):
            context.state["offset"] = 0
        with self.assertRaises(TypeError):
            context.state["filter"]["http"] = ()
        with self.assertRaises(FrozenInstanceError):
            context.scan_uuid = "other"
        restored = context.state_dict()
        restored["filter"]["http"].append(500)
        self.assertEqual(context.state["filter"]["http"], (200, 301))

    def test_context_refuses_live_objects_cycles_and_nonfinite_state(self):
        with self.assertRaises(TypeError):
            WorkspaceContext(state={"manager": self.window})
        cycle = {}
        cycle["self"] = cycle
        with self.assertRaises(ValueError):
            WorkspaceContext(state=cycle)
        with self.assertRaises(ValueError):
            WorkspaceContext(state={"offset": float("nan")})

    def test_add_and_same_selection_emit_only_actual_id_changes(self):
        selected = QSignalSpy(self.tabs.selected)
        self.assertEqual(self.add("a"), "a")
        self.add("b", select=False)
        self.assertEqual(self.tabs.current_id, "a")
        self.tabs.select("a")
        self.assertEqual(list(selected), [["a"]])
        self.tabs.select("b")
        self.tabs.select("b")
        self.assertEqual(list(selected), [["a"], ["b"]])

    def test_moving_tabs_preserves_active_identity_and_context_order(self):
        for id in ("a", "b", "c"):
            self.add(id)
        selected = QSignalSpy(self.tabs.selected)
        self.tabs.tabbar.moveTab(2, 0)
        self.assertEqual(
            [context.id for context in self.tabs.contexts()], ["c", "a", "b"]
        )
        self.assertEqual(self.tabs.current_id, "c")
        self.assertEqual(len(selected), 0)

    def test_host_can_revert_refused_switch_without_recursive_selection(self):
        self.add("a")
        self.add("b", select=False)
        selected = QSignalSpy(self.tabs.selected)
        self.tabs.select("b")
        self.tabs.blockSignals(True)
        self.tabs.select("a")
        self.tabs.blockSignals(False)
        self.assertEqual(list(selected), [["b"]])
        self.tabs.select("b")
        self.assertEqual(list(selected), [["b"], ["b"]])

    def test_inactive_removal_does_not_reselect_and_active_removal_selects_neighbor(
        self,
    ):
        for id in ("a", "b", "c"):
            self.add(id)
        selected = QSignalSpy(self.tabs.selected)
        self.assertEqual(self.tabs.remove("a").id, "a")
        self.assertEqual(self.tabs.current_id, "c")
        self.assertEqual(len(selected), 0)
        self.tabs.remove("c")
        self.assertEqual(self.tabs.current_id, "b")
        self.assertEqual(list(selected), [["b"]])
        self.tabs.remove("b")
        self.assertIsNone(self.tabs.current_id)
        self.assertEqual(self.tabs.contexts(), ())
        self.assertEqual(list(selected), [["b"]])
        self.assertIsNone(self.tabs.remove("unknown"))

    def test_close_is_an_intent_and_removal_keeps_host_and_shared_services_alive(self):
        self.add("a")
        self.add("b")
        shared_manager, shared_gateway = Mock(), Mock()
        self.window.scan_manager = shared_manager
        self.window.mcp_gateway = shared_gateway
        self.window.show()
        self.app.processEvents()
        closed = QSignalSpy(self.tabs.closeRequested)
        self.tabs.tabbar.tabCloseRequested.emit(1)
        self.assertEqual(list(closed), [["b"]])
        self.assertEqual(len(self.tabs.contexts()), 2)
        self.tabs.remove("b")
        self.assertTrue(self.window.isVisible())
        self.assertIs(self.window.scan_manager, shared_manager)
        self.assertIs(self.window.mcp_gateway, shared_gateway)
        shared_manager.stop.assert_not_called()
        shared_manager.stop_all_owned.assert_not_called()
        shared_gateway.stop.assert_not_called()

    def test_twelve_context_limit_blocks_new_and_duplicate_without_evicting(self):
        for number in range(12):
            self.add(str(number))
        before = self.tabs.contexts()
        new, duplicate = (
            QSignalSpy(self.tabs.newRequested),
            QSignalSpy(self.tabs.duplicateRequested),
        )
        self.assertFalse(self.tabs.new_button.isEnabled())
        self.tabs.request_new()
        self.tabs.request_duplicate()
        with self.assertRaises(ValueError):
            self.add("overflow")
        self.assertEqual(self.tabs.contexts(), before)
        self.assertEqual((len(new), len(duplicate)), (0, 0))
        self.tabs.remove("0")
        self.assertTrue(self.tabs.new_button.isEnabled())
        self.tabs.request_new()
        self.tabs.request_duplicate("1")
        self.assertEqual(list(new), [[]])
        self.assertEqual(list(duplicate), [["1"]])

    def test_updates_replace_snapshot_without_changing_id_or_selection(self):
        original = WorkspaceContext(
            id="a", project_label="Original", state={"offset": 0}
        )
        self.tabs.add(original)
        selected = QSignalSpy(self.tabs.selected)
        updated = self.tabs.update_context(
            "a", scan_uuid="scan-2", view_id="urls", state={"offset": 100}
        )
        self.assertEqual(updated.id, "a")
        self.assertEqual(original.state["offset"], 0)
        self.assertEqual(updated.state["offset"], 100)
        self.tabs.update_title("a", "Project · scan-2", QIcon())
        self.assertEqual(self.tabs.tabbar.tabText(0), "Project · scan-2")
        close_buttons = [
            self.tabs.tabbar.tabButton(0, side)
            for side in (QTabBar.LeftSide, QTabBar.RightSide)
        ]
        self.assertTrue(
            any(
                button and button.accessibleName() == "Закрыть вкладку Project · scan-2"
                for button in close_buttons
            )
        )
        self.tabs.update_state("a", {"offset": 200})
        self.assertEqual(self.tabs.contexts()[0].state["offset"], 200)
        self.assertEqual(len(selected), 0)
        with self.assertRaises(ValueError):
            self.tabs.update_context("a", id="different")

    def test_duplicate_and_overflow_actions_use_stable_ids(self):
        self.add("a")
        self.add("b")
        duplicate = QSignalSpy(self.tabs.duplicateRequested)
        self.tabs._rebuild_menu()
        self.tabs.menu.actions()[1].trigger()
        self.assertEqual(list(duplicate), [["b"]])
        self.assertEqual(len(self.tabs.contexts()), 2)
        self.tabs.menu.actions()[4].trigger()
        self.assertEqual(self.tabs.current_id, "a")
        self.assertFalse(self.tabs.menu.isEmpty())
        self.assertTrue(self.tabs.tabbar.usesScrollButtons())
        self.assertTrue(self.tabs.tabbar.isMovable())
        self.assertTrue(self.tabs.tabbar.tabsClosable())

    def test_window_shortcuts_work_with_body_focus_and_do_not_close_window(self):
        self.add("a")
        self.add("b")
        self.tabs.install_shortcuts(self.window)
        count = len(self.tabs._shortcuts)
        self.tabs.install_shortcuts(self.window)
        self.assertEqual(len(self.tabs._shortcuts), count)
        self.window.show()
        self.window.activateWindow()
        self.app.processEvents()
        self.body.setFocus()
        new, close = (
            QSignalSpy(self.tabs.newRequested),
            QSignalSpy(self.tabs.closeRequested),
        )
        QTest.keyClick(self.body, Qt.Key_T, Qt.ControlModifier)
        QTest.keyClick(self.body, Qt.Key_W, Qt.ControlModifier)
        self.assertEqual(list(new), [[]])
        self.assertEqual(list(close), [["b"]])
        self.assertTrue(self.window.isVisible())
        QTest.keyClick(self.body, Qt.Key_Tab, Qt.ControlModifier)
        self.assertEqual(self.tabs.current_id, "a")
        previous = next(
            shortcut.key()
            for shortcut in self.tabs._shortcuts
            if "Backtab" in shortcut.key().toString()
            or "Shift+Tab" in shortcut.key().toString()
        )
        combined = previous[0]
        modifiers = Qt.KeyboardModifiers(combined & int(Qt.KeyboardModifierMask))
        key = combined & ~int(Qt.KeyboardModifierMask)
        QTest.keyClick(self.body, key, modifiers)
        self.assertEqual(self.tabs.current_id, "b")
        self.assertEqual(
            QKeySequence("Ctrl+T"),
            next(
                s.key()
                for s in self.tabs._shortcuts
                if s.key() == QKeySequence("Ctrl+T")
            ),
        )


if __name__ == "__main__":
    unittest.main()
