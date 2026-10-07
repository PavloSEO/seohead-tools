"""MainWindow acceptance for three real retained scans in one project."""

import os
import json
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from seohead_desktop.app import MainWindow, load_theme
from tests.test_scan_runner import _SlowSite


class MultiRunAppTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])
        load_theme(cls.app)

    def test_one_project_three_runs_use_real_mcp_selector(self):
        core = os.environ.get("SEOHEAD_DESKTOP_CORE_CLI")
        if not core or not Path(core).is_file():
            self.skipTest("set SEOHEAD_DESKTOP_CORE_CLI to run the one-project GUI gate")
        core_root = Path(core).resolve().parents[2]
        if subprocess.run(["git", "-C", str(core_root), "status", "--porcelain"], capture_output=True, text=True).stdout:
            self.skipTest("the external development core must be clean for provenance")
        previous = os.environ.get("SEOHEAD_ALLOW_PRIVATE_HOSTS")
        os.environ["SEOHEAD_ALLOW_PRIVATE_HOSTS"] = "crawl.localhost,127.0.0.1"
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowSite)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        scratch = Path(__file__).resolve().parents[1] / ".build"
        scratch.mkdir(exist_ok=True)
        window = None

        def wait_for(predicate, message, timeout=25):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.app.processEvents()
                if predicate():
                    return
                QTest.qWait(30)
            self.fail(message)

        try:
            _SlowSite.delay = 0.1
            with tempfile.TemporaryDirectory(prefix="one-project-ui-", dir=scratch) as temporary:
                project = Path(temporary) / "project"
                subprocess.run(
                    [core, "project-new", "--directory", str(project), "--target", f"http://crawl.localhost:{server.server_port}/"],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                window = MainWindow(persistent=False, core_executable=core)
                window.show()
                window.read_project(str(project))
                wait_for(lambda: window.project_directory and window.crawl_descriptor and not window.requests, "project or core descriptor did not load")

                for max_urls in (8, 10, 12):
                    window.launch_scan(max_urls, "raw", 100, 60, False)
                wait_for(lambda: window.scan_manager and window.scan_manager.active_count == 3, "three UI launches did not start")
                wait_for(
                    lambda: len([row for row in window.scan_manager.snapshot() if row["core_run_id"]]) == 3,
                    lambda: "observer did not bind three core run IDs: " + json.dumps(window.scan_manager.snapshot()),
                )

                selected_run = window.scan_manager.snapshot()[0]["id"]
                window.selected_managed_run_id = selected_run
                window.cancel_active_work()
                wait_for(lambda: window.scan_manager.detail(selected_run)["state"] in {"awaiting_core_status", "finished", "failed"}, "selected run did not stop")
                self.assertGreaterEqual(window.scan_manager.active_count, 1, "stop leaked to other runs")
                wait_for(lambda: window.scan_manager.active_count == 0, "owned child processes remained active")

                window.refresh_project()
                wait_for(lambda: window.scan_model.rowCount() >= 2 and not window.requests, "retained scans did not load through MCP")
                window.scan_table.selectRow(0)
                wait_for(lambda: window.model.rowCount() > 0, "first retained scan did not load rows")
                first_uuid = window.selected_scan_uuid
                window.audit_workspace.panel("internal").table.selectRow(0)
                wait_for(lambda: window.audit_workspace.panel("url_details").state in {"ready", "unavailable"}, "first retained URL detail did not resolve")

                window.scan_table.selectRow(1)
                self.assertEqual(window.model.rowCount(), 0, "old URL rows survived scan switch")
                wait_for(lambda: window.model.rowCount() > 0, "second retained scan did not load rows")
                self.assertNotEqual(window.selected_scan_uuid, first_uuid)
                self.assertIn(window.selected_scan_uuid, window.audit_workspace.panel("internal").source_label.text())
                window.close()
        finally:
            if window is not None:
                window.close()
            if previous is None:
                os.environ.pop("SEOHEAD_ALLOW_PRIVATE_HOSTS", None)
            else:
                os.environ["SEOHEAD_ALLOW_PRIVATE_HOSTS"] = previous
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
