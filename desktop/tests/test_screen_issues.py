import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5.QtWidgets import QApplication, QLabel, QPushButton

from seohead_desktop import i18n
from seohead_desktop.app import load_theme
from seohead_desktop.screens.issues import IssuesScreen, sample_checks
from seohead_desktop.ui.kit import StatePanel
from tests._qt import sweep_widgets
from tests._screens_core import fixture
from tests._screens_host import FakeHost


class IssuesBase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_theme(cls.app, "light")

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")
        self.host = FakeHost()
        self.host.opened = []
        self.host.open_url_filtered = lambda label, filters: self.host.opened.append((label, filters))
        self.host.resume_selected_scan = lambda: None
        self.scan = fixture("scan_row_shop_findings.json")
        self.host.scan_model.rows = [self.scan]
        self.host.selected_scan_path = self.scan["path"]
        self.screen = IssuesScreen(self.host)
        self.screen.resize(1440, 800)
        self.screen.show()
        self.app.processEvents()

    def tearDown(self):
        self.screen.close()
        self.screen.deleteLater()

    def texts(self):
        return [w.text() for w in self.screen.findChildren(QLabel)] + [b.text() for b in self.screen.findChildren(QPushButton)]


class IssuesTests(IssuesBase):
    def test_severity_totals_come_from_the_core_row_not_from_the_sample(self):
        findings = self.scan["evidence"]["findings"]
        self.assertTrue(findings["truncated"])
        pill, _label = self.screen.pills["critical"]
        self.assertIn(str(findings["by_severity"]["critical"]), pill.text())
        self.assertIn("4 272", self.screen.pills["all"][0].text().replace("\u202f", " ").replace("\xa0", " "))
        self.assertTrue(any("ждёт" in t and "932" in t for t in self.texts()) or self.screen.list_waiting.text().endswith("932"))

    def test_list_holds_only_checks_of_the_sample_and_filters_by_severity(self):
        checks = sample_checks(self.scan["evidence"]["findings"])
        self.assertEqual(set(self.screen.row_buttons), set(checks))
        self.screen.pills["critical"][0].click()
        self.assertTrue(all(checks[c]["severity"] == "critical" for c in self.screen.row_buttons))

    def test_check_detail_lists_its_sampled_urls_and_opens_them_in_url(self):
        check = next(iter(self.screen.row_buttons))
        self.screen.row_buttons[check].click()
        entry = self.screen.checks[check]
        self.assertEqual(self.screen.model.rowCount(), len(entry["items"]))
        self.assertTrue(self.screen.open_url.isEnabled())
        self.screen.open_url.click()
        _label, filters = self.host.opened[-1]
        self.assertEqual(filters[0]["column"], "url")
        self.assertEqual(filters[0]["op"], "in")
        self.assertEqual(filters[0]["value"], [i["target_url"] for i in entry["items"]])
        self.assertFalse(self.screen.to_task.isEnabled())

    def test_skipped_checks_are_listed_not_counted_as_zero(self):
        for item in self.scan["evidence"]["skipped_checks"]:
            self.assertIn(item["id"], self.screen.skipped.text())

    def test_no_findings_state_is_an_honest_panel(self):
        self.scan["evidence"]["findings"] = {"state": "unavailable", "reason": "нет аудита"}
        self.screen.refresh()
        self.assertEqual(self.screen.stack.currentIndex(), 1)
        panel = self.screen.state_holder.findChild(StatePanel)
        self.assertEqual(panel.kind, "partial")

    def test_measured_zero_is_a_good_outcome(self):
        self.scan["evidence"]["findings"] = {"state": "available", "total": 0, "by_severity": {}, "items": []}
        self.screen.refresh()
        self.assertEqual(self.screen.state_holder.findChild(StatePanel).title.text(), "Проблем не найдено")

    def test_no_project_and_no_scan(self):
        self.host.project_directory = None
        self.screen.refresh()
        self.assertEqual(self.screen.panel_state, "none")
        self.host.project_directory = "/project/shop"
        self.host.selected_scan_path = None
        self.screen.refresh()
        self.assertEqual(self.screen.panel_state, "noscan")

    def test_english(self):
        i18n.set_language("en")
        self.app.processEvents()
        i18n.retranslate(self.screen)
        self.assertEqual(self.screen.open_url.text(), "Open in URL")


if __name__ == "__main__":
    unittest.main()
