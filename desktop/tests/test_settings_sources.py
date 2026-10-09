import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QFrame, QLabel, QPushButton

from seohead_desktop.settings_store import AppSettings
from seohead_desktop.ui.settings import SECTION_IDS, full_schema, sources
from seohead_desktop.ui.settings.context import SettingsContext
from seohead_desktop.ui.settings.dialog import SettingsDialog

READINESS = {
    "ok": True, "verification_performed": False,
    "providers": {
        "gsc": {"readiness_state": "configured_unverified", "credential_components": {"oauth_bearer": True, "service_account": True},
                "credential_sources": {"service_account": {"source_reference": "config:gsc/service-account.json"}}},
        "bing_webmaster": {"readiness_state": "missing", "credential_components": {"api_key": False}},
        "wayback": {"readiness_state": "not_required", "credential_components": {}},
        "arsenkin": {"readiness_state": "verified", "verified": True, "credential_components": {"api_token": True},
                     "quota_mode": "paid limit credits"},
        "newcomer": {"readiness_state": "weird_state", "credential_components": {}},
    },
}


def texts(widget):
    return [label.text() for label in widget.findChildren(QLabel)]


class SourcesSectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def page(self, hook):
        actions = {"providers": hook} if hook else {}
        return sources.build_page(AppSettings(schema=full_schema()), SettingsContext(actions=actions))

    def test_section_is_twelfth_and_follows_mcp(self):
        self.assertEqual(len(SECTION_IDS), 12)
        self.assertEqual(SECTION_IDS[SECTION_IDS.index("mcp") + 1], "sources")

    def test_without_a_core_hook_nothing_is_invented(self):
        self.assertIn("Нет данных", texts(self.page(None)))

    def test_waiting_state_then_states_from_the_core(self):
        holder = {}
        page = self.page(lambda callback, on_error: holder.update(cb=callback, err=on_error))
        self.assertTrue(any("Читаю" in t for t in texts(page)))
        holder["cb"](READINESS)
        labels = texts(page)
        joined = " | ".join(labels)
        for expected in ("Google Search Console", "ключ задан · не проверен", "нужен ключ", "без ключа", "подключено", "newcomer"):
            self.assertIn(expected, joined)
        self.assertIn("хранится: config:gsc/service-account.json", joined)

    def test_configured_is_never_reported_as_connected(self):
        holder = {}
        page = self.page(lambda callback, on_error: holder.update(cb=callback))
        holder["cb"]({"providers": {"gsc": READINESS["providers"]["gsc"]}})
        joined = " | ".join(texts(page))
        self.assertNotIn("подключено", joined.replace("подключено и проверено", ""))
        self.assertIn("ключ задан · не проверен", joined)

    def test_kpis_count_only_what_was_reported_and_spend_is_not_measured(self):
        holder = {}
        page = self.page(lambda callback, on_error: holder.update(cb=callback))
        holder["cb"](READINESS)
        kpis = {frame.findChildren(QLabel)[0].text(): frame.findChildren(QLabel)[1].text() for frame in page.findChildren(QFrame) if frame.property("kpi")}
        self.assertEqual(kpis["подключено и проверено из 5"], "1")
        self.assertEqual(kpis["ключ задан, не проверен"], "1")
        self.assertEqual(kpis["нужен ключ"], "1")
        self.assertEqual(kpis["платные API · расходы за месяц"], "Нет данных")

    def test_actions_without_backend_are_disabled_with_the_reason(self):
        holder = {}
        page = self.page(lambda callback, on_error: holder.update(cb=callback))
        holder["cb"](READINESS)
        buttons = [b for b in page.findChildren(QPushButton)]
        self.assertTrue(buttons)
        for button in buttons:
            self.assertFalse(button.isEnabled(), button.text())
            self.assertEqual(button.toolTip(), "Недоступно в этой сборке")

    def test_error_is_shown_in_place(self):
        holder = {}
        page = self.page(lambda callback, on_error: holder.update(err=on_error))
        holder["err"]("CLI ядра seohead не найден")
        self.assertTrue(any("CLI ядра seohead не найден" in t for t in texts(page)))

    def test_secret_values_never_appear(self):
        holder = {}
        page = self.page(lambda callback, on_error: holder.update(cb=callback))
        leaking = {"providers": {"gsc": {**READINESS["providers"]["gsc"], "api_key": "SECRET-123", "token": "SECRET-456"}}}
        holder["cb"](leaking)
        self.assertNotIn("SECRET", " ".join(texts(page)))

    def test_dialog_lists_the_section_with_its_title(self):
        dialog = SettingsDialog(AppSettings(schema=full_schema()), SettingsContext(), section="sources")
        self.assertEqual(dialog.section_title.text(), "Источники данных")

    def test_core_missing_is_reported_and_late_answers_to_a_closed_dialog_are_ignored(self):
        from seohead_desktop.app import MainWindow

        window = MainWindow(persistent=False)
        window.core_executable = None
        seen = []
        window.request_providers(seen.append, seen.append)
        self.assertEqual(seen, ["CLI ядра seohead не найден"])

        def gone(_value):
            raise RuntimeError("wrapped C/C++ object has been deleted")

        window._provider_handlers = (gone, gone)
        window._providers_loaded({"providers": {}})  # must not raise
        window.providers_failed("late")
        window.close()


if __name__ == "__main__":
    unittest.main()
