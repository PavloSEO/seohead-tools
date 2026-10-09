"""Offline behavior, threading, redaction and keyboard contracts for the source slice."""

import ast
import json
import os
import re
import subprocess
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtCore import QCoreApplication, QEvent, Qt, QThread
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton

from seohead_desktop import i18n
from seohead_desktop.app import MainWindow
from seohead_desktop.settings_store import AppSettings
from seohead_desktop.source_service import (
    SourceService,
    load_sources,
    read_cli,
    redacted_providers,
    redacted_spend,
)
from seohead_desktop.ui.controls import Switch
from seohead_desktop.ui.settings import full_schema
from seohead_desktop.ui.settings.context import SettingsContext
from seohead_desktop.ui.settings.dialog import SettingsDialog
from seohead_desktop.ui.settings.source_details import _TableModel
from seohead_desktop.ui.settings.sources import SourcesPage
from tests._qt import sweep_widgets

SNAPSHOT = {"registry": {"gsc": {"credential_components": ["oauth_bearer"]}, "arsenkin": {"credential_components": ["api_token"]}},
            "readiness": {"gsc": {"readiness_state": "missing", "credential_components": {"oauth_bearer": False}},
                          "arsenkin": {"readiness_state": "configured_unverified", "credential_components": {"api_token": True},
                                       "credential_sources": {"api_token": {"source_reference": "config:arsenkin/token"}}}},
            "spend": {"calls": 0}, "status": [], "errors": []}


class AdapterTests(unittest.TestCase):
    def test_all_read_contracts_and_doctor_are_explicit(self):
        calls = []

        def call(_exe, args):
            calls.append(args)
            return {"providers": {}, "provider_status": {}, "sources": []}

        load_sources("seohead", "snapshot", "/synthetic-project", call=call)
        self.assertEqual([c[0] for c in calls], ["provider-registry", "provider-readiness", "spend-report", "sources-status"])
        self.assertEqual(calls[-1], ["sources-status", "--project", "/synthetic-project"])
        load_sources("seohead", "doctor", call=call)
        self.assertEqual(calls[-1], ["sources-doctor"])
        load_sources("seohead", "auth-status", provider="gsc", call=call)
        self.assertEqual(calls[-1], ["provider-auth", "--provider", "gsc", "--action", "status"])

    def test_no_paid_sync_credential_or_custom_oauth_operation(self):
        for operation, provider in (("sync", "gsc"), ("connect", "gsc"), ("save-key", "arsenkin"),
                                    ("verify", "arsenkin"), ("verify", "dataforseo_backlinks")):
            with self.subTest(operation=operation, provider=provider), self.assertRaises(ValueError):
                load_sources("seohead", operation, provider=provider, call=lambda *a: self.fail("CLI called"))

    def test_missing_data_is_partial_and_raw_error_is_not_forwarded(self):
        def refused(_exe, _args):
            raise ValueError("FAKE-SECRET")

        result = load_sources("seohead", "snapshot", call=refused)
        self.assertEqual(result["errors"], ["registry", "readiness", "spend"])
        self.assertNotIn("FAKE-SECRET", json.dumps(result))

    def test_secrets_and_uncertain_cost_never_cross_the_adapter(self):
        providers = redacted_providers({"gsc": {"readiness_state": "configured_unverified", "token": "FAKE-TOKEN",
                                               "credential_sources": {"oauth_bearer": {"source_reference": "FAKE-TOKEN", "accepted_source_references": ["config:gsc/access_token", "FAKE-TOKEN"]}}}})
        self.assertNotIn("FAKE-TOKEN", json.dumps(providers))
        report = redacted_spend({"calls": 2, "by_source": {"arsenkin": {"limits": 0}},
                                "uncertain": [{"token": "FAKE-TOKEN", "cost": 99}], "log": "/secret-path"})
        self.assertEqual(report["by_source"]["arsenkin"]["limits"], 0)
        self.assertEqual(report["uncertain_count"], 1)
        self.assertNotIn("FAKE-TOKEN", json.dumps(report))
        self.assertNotIn("secret-path", json.dumps(report))

    def test_stderr_cannot_leak_and_timeout_is_bounded(self):
        with patch("seohead_desktop.source_service.subprocess.run", return_value=subprocess.CompletedProcess([], 1, "", "FAKE-TOKEN")) as run:
            with self.assertRaisesRegex(ValueError, "unavailable"):
                read_cli("seohead", ["provider-readiness"])
            self.assertEqual(run.call_args.kwargs["timeout"], 45)


class SourceUiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")
        self.calls = []
        self.pending = []

        def request(**kwargs):
            self.calls.append(kwargs["operation"])
            self.pending.append(kwargs)

        self.page = SourcesPage(SettingsContext(actions={"sources": request}))
        self.pending.pop()["callback"](SNAPSHOT.copy())

    def tearDown(self):
        i18n.set_language("ru")
        sweep_widgets()

    def texts(self):
        return " | ".join(label.text() for label in self.page._body.findChildren(QLabel))

    def test_doctor_shows_each_result_without_claiming_connected(self):
        self.page.doctor()
        self.assertEqual(self.calls[-1], "doctor")
        self.pending.pop()["callback"]({"providers": SNAPSHOT["readiness"]})
        self.assertIn("РЕЗУЛЬТАТ ПРОВЕРКИ КОНФИГУРАЦИИ", self.texts())
        self.assertIn("ключ задан · не проверен", self.texts())

    def test_key_is_masked_unreadable_and_paid_consent_locked(self):
        self.page.navigate("detail", "arsenkin")
        key = next(f for f in self.page.findChildren(QLineEdit) if f.accessibleName() == "Новый API-ключ")
        self.assertEqual(key.echoMode(), QLineEdit.Password)
        self.assertFalse(key.isEnabled())
        self.assertEqual(key.text(), "")
        switch = self.page.findChild(Switch)
        self.assertTrue(switch.isChecked())
        self.assertFalse(switch.isEnabled())
        self.assertIn("config:arsenkin/token", self.texts())
        self.assertIn("#965", self.texts())
        self.assertIn("#949", self.texts())

    def test_oauth_only_calls_core_status_and_has_honest_browser_gap(self):
        self.page.navigate("detail", "gsc")
        self.assertEqual(self.calls[-1], "auth-status")
        self.pending.pop()["callback"]({"configured": True, "access_verified": False})
        self.assertIn("#964", self.texts())
        self.assertNotIn("Подключено", self.texts())
        connect = next(b for b in self.page.findChildren(QPushButton) if b.text() == "Подключить через браузер")
        self.assertFalse(connect.isEnabled())

    def test_late_detail_result_cannot_replace_another_view(self):
        self.page.navigate("detail", "gsc")
        pending = self.pending.pop()
        self.page.navigate("spend")
        pending["callback"]({"configured": True})
        self.assertEqual(self.page.auth, {})
        self.assertIn("Нет данных", self.texts())
        self.assertNotIn("0", self.texts())

    def test_agent_area_permissions_are_not_falsely_saved(self):
        self.page.navigate("agent")
        self.assertIn("#966", self.texts())
        switches = self.page.findChildren(Switch)
        self.assertEqual(len(switches), 6)
        self.assertTrue(all(not s.isEnabled() and not s.isChecked() for s in switches))
        i18n.set_language("en")
        i18n.retranslate(self.page)
        self.assertIn("waiting for #966", self.texts())
        self.assertNotIn("ждёт", self.texts())

    def test_compact_source_sheets_do_not_require_horizontal_scrolling(self):
        def request(**kwargs):
            kwargs["callback"](SNAPSHOT.copy() if kwargs["operation"] == "snapshot" else {})

        dialog = SettingsDialog(AppSettings(schema=full_schema()), SettingsContext(actions={"sources": request}), section="sources")
        dialog.resize(736, 690)  # 800 px main window minus its 64 px navigation rail
        dialog.show()
        page = dialog._pages["sources"][1]
        scroll = dialog._pages["sources"][0]
        for view, provider in (("list", None), ("detail", "gsc"), ("detail", "arsenkin"), ("spend", None), ("agent", None)):
            page.navigate(view, provider)
            for _ in range(8):
                self.app.processEvents()
            QTest.qWait(100)
            with self.subTest(view=view, provider=provider):
                self.assertEqual(scroll.horizontalScrollBar().maximum(), 0)
                for field in page.findChildren(QLineEdit):
                    self.assertTrue(field.isVisibleTo(page))
        dialog.close()

    def test_late_doctor_result_does_not_change_another_settings_header(self):
        pending = []
        def request(**kwargs):
            if kwargs["operation"] == "snapshot":
                kwargs["callback"](SNAPSHOT.copy())
            else:
                pending.append(kwargs)
        dialog = SettingsDialog(AppSettings(schema=full_schema()), SettingsContext(actions={"sources": request}), section="sources")
        dialog.show()
        self.app.processEvents()
        page = dialog._pages["sources"][1]
        page.doctor()
        dialog.show_section("general")
        pending.pop()["callback"]({"providers": SNAPSHOT["readiness"]})
        self.app.processEvents()
        self.assertEqual(dialog.section_title.text(), "Общие")
        self.assertFalse(page._header_toolbar.isVisible())
        dialog.close()

    def test_spend_preserves_measured_zero_and_provider_units(self):
        self.page.snapshot["spend"] = {"calls": 2, "since": "2026-10-01", "by_source": {"arsenkin": {"limits": 0}}, "by_operation": {"arsenkin.test": {"limits": 0}}, "uncertain_count": 1}
        self.page.navigate("spend")
        model = self.page.findChild(_TableModel)
        self.assertIn("0,00 limits", model.data(model.index(0, 1)))
        self.assertNotIn("₽", model.data(model.index(0, 1)))
        i18n.set_language("en")
        i18n.retranslate(self.page)
        self.assertEqual(model.headerData(0, Qt.Horizontal), "Provider")
        self.assertNotIn("Расходы", self.texts())

    def test_worker_cli_is_off_ui_and_deleted_owner_is_ignored(self):
        seen = []
        service = SourceService("seohead")
        gate = threading.Event()

        def load(*args):
            seen.append(QThread.currentThread() != self.app.thread())
            gate.wait(2)
            return {}

        with patch("seohead_desktop.source_service.load_sources", side_effect=load):
            service.request("doctor", seen.append, seen.append, self.page)
            self.page.deleteLater()
            QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
            gate.set()
            for _ in range(100):
                self.app.processEvents()
                if not service._pending:
                    break
                QTest.qWait(10)
        self.assertEqual(seen, [True])
        self.assertFalse(service._pending)

    def test_theme_callback_after_layout_disposal_is_ignored(self):
        self.page._layout.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        self.page._theme_changed("dark")

    def test_new_strings_have_dictionary_entries(self):
        package = Path(__file__).resolve().parents[1] / "src/seohead_desktop"
        dictionary = i18n._dictionary()
        paths = [package / "ui/profile_menu.py", package / "source_service.py", package / "ui/settings/source_details.py"]
        for path in paths:
            for node in ast.walk(ast.parse(path.read_text())):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) and re.search("[Ѐ-ӿ]", node.value):
                    self.assertTrue(node.value in dictionary, f"Missing translation: {node.value}")


class ProfileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_same_menu_from_rail_keyboard_selection_and_escape_restores_focus(self):
        window = MainWindow(persistent=False)
        window.show()
        self.app.processEvents()
        for rail in (False, True):
            window.set_navigation_compact(rail)
            menu = window.build_profile_menu()
            actions = {a.text(): a for a in menu.actions()}
            for title in ("Настройки", "Справка", "MCP-сервер · нет данных"):
                self.assertIn(title, actions)
            for removed in ("Источники данных", "Горячие клавиши", "О программе"):
                self.assertNotIn(removed, actions)
            self.assertEqual(menu.width(), 264)
            self.assertIsNotNone(actions["MCP-сервер · нет данных"].menu())
            menu.popup(window.navigation.profile.mapToGlobal(window.navigation.profile.rect().topRight()))
            self.app.processEvents()
            menu.setActiveAction(actions["Простой"])
            QTest.keyClick(menu, Qt.Key_Return)
            self.assertEqual(window.display, "simple")
            menu.deleteLater()
            window.set_display("agent")
            from PyQt5.QtCore import QTimer

            QTimer.singleShot(20, lambda: QTest.keyClick(self.app.activePopupWidget(), Qt.Key_Escape))
            window.show_profile_menu()
            self.assertTrue(window.navigation.profile.hasFocus())
            self.assertIsNone(self.app.activePopupWidget())
        self.assertEqual(window.navigation.profile.name.text(), "Павел")
        window.close()
        window.deleteLater()

    def test_sources_open_in_the_modal_and_header_has_two_actions(self):
        from PyQt5.QtCore import QTimer

        window = MainWindow(persistent=False)
        def request(**kwargs):
            kwargs["callback"](SNAPSHOT.copy())
        seen = []
        before = tuple(c.id for c in window.workspace_tabs.contexts())

        def inspect():
            dialog = self.app.activeModalWidget()
            source = dialog._pages["sources"][1]
            seen.append((dialog.isModal(), dialog.current_section(),
                         [b.text() for b in source._header_toolbar.findChildren(QPushButton)],
                         source._header_toolbar.parentWidget() is dialog))
            QTest.keyClick(dialog, Qt.Key_Escape)

        with patch.object(window, "settings_context", return_value=SettingsContext(actions={"sources": request})):
            QTimer.singleShot(20, inspect)
            window.open_settings("sources")
        self.assertEqual(seen, [(True, "sources", ["Расходы", "Проверить все"], True)])
        self.assertEqual(tuple(c.id for c in window.workspace_tabs.contexts()), before)
        window.close()
        window.deleteLater()


if __name__ == "__main__":
    unittest.main()
