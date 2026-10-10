import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from seohead_desktop import i18n
from seohead_desktop.app import MainWindow, load_theme
from seohead_desktop.qt import app as qt_app
from seohead_desktop.screens.project_export import UNAVAILABLE, ProjExportScreen
from tests._qt import sweep_widgets
from tests._screens_core import fixture
from tests._screens_host import FakeHost


class ProjExportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()
        load_theme(cls.app, "light")

    def setUp(self):
        sweep_widgets()
        i18n.set_language("ru")
        self.host = FakeHost("/project/qa")
        self.row = fixture("scan_row.json")
        self.host.scan_model.rows = [self.row]
        self.host.selected_scan_path = self.row["path"]

    def tearDown(self):
        sweep_widgets()

    def screen(self, host=None):
        screen = ProjExportScreen(host or self.host)
        screen.resize(1440, 900)
        screen.show()
        return screen

    def test_no_project_shows_open_project_state(self):
        host = FakeHost(None)
        host.scan_model.rows = []
        host.selected_scan_path = None
        screen = self.screen(host)
        self.assertIs(screen.stack.currentWidget(), screen.no_project)

    def test_project_without_selected_scan_shows_empty_state(self):
        self.host.selected_scan_path = "/project/qa/scans/missing.sqlite"
        screen = self.screen()
        self.assertIs(screen.stack.currentWidget(), screen.no_scan)

    def test_datasets_follow_what_the_core_exports(self):
        screen = self.screen()
        self.assertEqual(screen.stack.currentIndex(), 0)
        self.assertTrue(screen.dataset_buttons["url"].isEnabled())
        self.assertTrue(screen.dataset_buttons["url"].isChecked())
        self.assertTrue(screen.dataset_buttons["issues"].isEnabled())
        for key in ("compare", "tasks"):
            self.assertFalse(screen.dataset_buttons[key].isEnabled())
            self.assertEqual(screen.dataset_buttons[key].toolTip(), i18n.tr(UNAVAILABLE))

    def test_scan_meta_and_file_name_come_from_the_selected_scan(self):
        screen = self.screen()
        self.assertIn("20 URL", screen.scan_meta.text())
        self.assertIn(".xlsx", screen.file_name.text())
        screen.pick_format("json")
        self.assertIn(".json", screen.file_name.text())
        self.assertTrue(screen.folder.text().endswith("/exports"))

    def test_export_is_enabled_for_a_selected_scan_and_disabled_without_one(self):
        screen = self.screen()
        self.assertTrue(screen.run.isEnabled())
        self.assertNotIn("pdf", screen.format_buttons)  # PDF report is not in scan_export.v1
        self.host.selected_scan_path = None
        screen.refresh()
        self.assertFalse(screen.run.isEnabled())

    def test_click_emits_the_dataset_and_format_and_never_touches_the_core(self):
        screen = self.screen()
        requests = []
        screen.export_requested.connect(lambda dataset, fmt: requests.append((dataset, fmt)))
        screen.pick_format("csv")
        screen.run.click()
        self.assertEqual(requests, [("url", "csv")])

    def test_running_state_disables_export_and_shows_progress(self):
        screen = self.screen()
        screen.export_started("url_r-abcd.csv")
        self.assertFalse(screen.run.isEnabled())
        self.assertFalse(screen.progress.isHidden())
        self.assertIn("url_r-abcd.csv", screen.result.text())
        screen.run.click()  # a second click while running is ignored
        screen.export_done({"files": ["/p/exports/url_r-abcd.csv"], "fmt": "csv"})
        self.assertTrue(screen.run.isEnabled())
        self.assertTrue(screen.progress.isHidden())
        self.assertIn("/p/exports/url_r-abcd.csv", screen.result.text())

    def test_core_failure_is_shown_with_its_reason(self):
        screen = self.screen()
        screen.export_started("url_r-abcd.csv")
        screen.export_failed("output already exists: /p/exports/url_r-abcd.csv")
        self.assertTrue(screen.run.isEnabled())
        self.assertIn("output already exists", screen.result.text())

    def test_window_runs_the_export_through_the_core_worker_and_shows_the_path(self):
        import json
        import tempfile
        import time

        from PyQt5.QtCore import QThreadPool

        from seohead_desktop.export_service import ExportService
        from seohead_desktop.ui.presentation import short_run_id

        calls = []

        def core(argv):
            calls.append(argv)
            return json.dumps({"ok": True, "fmt": "csv", "files": [argv[argv.index("--out") + 1]]})

        with tempfile.TemporaryDirectory() as project:
            window = MainWindow(persistent=False, core_executable="seohead-test")
            try:
                window.export_service = ExportService("seohead-test", run=core)
                window.project_directory = project
                window.scan_model.rows = [self.row]
                window.selected_scan_path = self.row["path"]
                screen = window.screens["reports"]
                screen.refresh()
                screen.pick_format("csv")
                screen.run.click()
                deadline = time.monotonic() + 5
                while screen.busy and time.monotonic() < deadline:
                    QThreadPool.globalInstance().waitForDone(50)
                    self.app.processEvents()
                self.assertFalse(screen.busy)
                expected = f"{project}/exports/url_{short_run_id(self.row['uuid'])}.csv"
                self.assertIn(expected, screen.result.text())
                self.assertEqual(calls[0][1:3], ["scan-export", "--scan"])
                self.assertEqual(calls[0][calls[0].index("--records") + 1], "pages")
            finally:
                window.close()

    def test_window_hosts_the_export_screen_in_the_reports_slot(self):
        window = MainWindow(persistent=False)
        try:
            self.assertIsInstance(window.screens["reports"], ProjExportScreen)
        finally:
            window.close()


if __name__ == "__main__":
    unittest.main()
