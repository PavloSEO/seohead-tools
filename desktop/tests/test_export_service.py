import json
import os
import tempfile
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from seohead_desktop import export_service as es
from seohead_desktop.qt import app as qt_app


class Fake:
    """Stands in for the core: records argv, returns the stdout it is given."""

    def __init__(self, stdout):
        self.stdout = stdout
        self.argv = None

    def __call__(self, argv):
        self.argv = argv
        return self.stdout


OK = json.dumps(
    {"ok": True, "format": "scan_export.v1", "fmt": "csv", "files": ["/p/exports/url_r-abcd.csv"]}
)


class BuildArgvTests(unittest.TestCase):
    def test_argv_uses_fixed_flags_for_one_record_type(self):
        argv = es.build_argv(
            "/bin/seohead", "/p/scans/a.sqlite", "/p/exports/url_r-abcd.xlsx", "xlsx", "pages"
        )
        self.assertEqual(
            argv,
            [
                "/bin/seohead",
                "scan-export",
                "--scan",
                "/p/scans/a.sqlite",
                "--out",
                "/p/exports/url_r-abcd.xlsx",
                "--format",
                "xlsx",
                "--records",
                "pages",
            ],
        )

    def test_unknown_format_or_record_is_refused_before_running(self):
        with self.assertRaises(ValueError):
            es.build_argv("/bin/seohead", "s", "o", "pdf", "pages")
        with self.assertRaises(ValueError):
            es.build_argv("/bin/seohead", "s", "o", "csv", "links")

    def test_dataset_ids_map_to_core_record_types(self):
        self.assertEqual(es.RECORDS, {"url": "pages", "issues": "findings"})
        self.assertEqual(es.output_name("url", "r-abcd", "csv"), "url_r-abcd.csv")


class ExportScanTests(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.folder = os.path.join(self.dir.name, "exports")

    def tearDown(self):
        self.dir.cleanup()

    def run_export(self, fake):
        return es.export_scan(
            "/bin/seohead",
            "/p/scans/a.sqlite",
            self.folder,
            "url_r-abcd.csv",
            "csv",
            "pages",
            run=fake,
        )

    def test_success_creates_the_folder_and_returns_the_core_files(self):
        fake = Fake(OK)
        outcome = self.run_export(fake)
        self.assertTrue(os.path.isdir(self.folder))
        self.assertEqual(outcome, {"files": ["/p/exports/url_r-abcd.csv"], "fmt": "csv"})
        self.assertEqual(fake.argv[-2:], ["--records", "pages"])
        self.assertEqual(
            fake.argv[fake.argv.index("--out") + 1], os.path.join(self.folder, "url_r-abcd.csv")
        )

    def test_core_refusal_is_passed_on_as_the_reason(self):
        fake = Fake(
            json.dumps({"ok": False, "error": "output already exists: /p/exports/url_r-abcd.csv"})
        )
        with self.assertRaises(ValueError) as caught:
            self.run_export(fake)
        self.assertEqual(str(caught.exception), "output already exists: /p/exports/url_r-abcd.csv")

    def test_unreadable_core_output_is_an_honest_failure(self):
        for stdout in ("", "not json", json.dumps([1]), json.dumps({"ok": True, "files": []})):
            with self.subTest(stdout=stdout), self.assertRaises(ValueError) as caught:
                self.run_export(Fake(stdout))
            self.assertEqual(str(caught.exception), es.UNREADABLE)

    def test_missing_core_refuses_without_touching_the_disk(self):
        with self.assertRaises(ValueError) as caught:
            es.export_scan(None, "s", self.folder, "n.csv", "csv", "pages", run=Fake(OK))
        self.assertEqual(str(caught.exception), es.MISSING_CORE)
        self.assertFalse(os.path.exists(self.folder))


class WorkerTests(unittest.TestCase):
    """Runs the QRunnable in this thread so the signals are delivered synchronously."""

    @classmethod
    def setUpClass(cls):
        cls.app = qt_app()

    def test_worker_reports_done_or_failed_through_its_signals(self):
        results = []
        with tempfile.TemporaryDirectory() as directory:
            folder = os.path.join(directory, "exports")
            worker = es._Export(("/bin/seohead", "s", folder, "n.csv", "csv", "pages"), Fake(OK))
            worker.signals.done.connect(lambda value: results.append(("done", value["fmt"])))
            worker.signals.failed.connect(lambda message: results.append(("failed", message)))
            worker.run()
        self.assertEqual(results, [("done", "csv")])

        failures = []
        worker = es._Export(
            ("/bin/seohead", "s", "/nonexistent-root/x", "n.csv", "csv", "pages"), Fake(OK)
        )
        worker.signals.failed.connect(failures.append)
        worker.run()
        self.assertEqual(failures, [es.NO_FOLDER])


if __name__ == "__main__":
    unittest.main()
