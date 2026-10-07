"""Owned-loopback integration gate for explicit local crawl process control."""

import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from seohead_desktop.scan_runner import LocalScanProcess


class _SlowSite(BaseHTTPRequestHandler):
    delay = 0.25

    def do_GET(self):
        if self.path == "/":
            body = "<html><body>" + "".join(
                f'<a href="/page-{number}">{number}</a>' for number in range(6)
            ) + "</body></html>"
        else:
            time.sleep(self.delay)
            body = "<html><body>retained fixture</body></html>"
        encoded = body.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *_args):
        pass


class LocalScanRunnerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_owned_loopback_capture_interrupt_and_resume(self):
        core = os.environ.get("SEOHEAD_DESKTOP_CORE_CLI")
        if not core or not Path(core).is_file():
            self.skipTest("set SEOHEAD_DESKTOP_CORE_CLI to run the local native-crawl gate")
        previous = os.environ.get("SEOHEAD_ALLOW_PRIVATE_HOSTS")
        os.environ["SEOHEAD_ALLOW_PRIVATE_HOSTS"] = "crawl.localhost,127.0.0.1"
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowSite)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()

        def wait_for(predicate, message, timeout=20):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.app.processEvents()
                if predicate():
                    return
                QTest.qWait(30)
            self.fail(message)

        try:
            with tempfile.TemporaryDirectory(prefix="seohead-desktop-crawl-") as temporary:
                project = Path(temporary) / "project"
                target = f"http://crawl.localhost:{server.server_port}/"
                subprocess.run(
                    [core, "project-new", "--directory", str(project), "--target", target],
                    check=True,
                    capture_output=True,
                    text=True,
                )
                runner = LocalScanProcess(core)
                finished = []
                runner.finished.connect(lambda code, state: finished.append((code, state)))
                runner.start(
                    str(project),
                    20,
                    "raw",
                    (("limits.max_requests", 100), ("limits.max_crawl_seconds", 60)),
                )
                wait_for(lambda: runner.active, "local crawl did not start")
                wait_for(
                    lambda: any((project / "scans").glob("*.sqlite")),
                    "local crawl did not create its retained artifact",
                )
                QTest.qWait(700)
                runner.request_stop()
                wait_for(lambda: not runner.active, "owned local crawl did not stop")
                self.assertTrue(finished)
                scans = sorted((project / "scans").glob("*.sqlite"))
                self.assertTrue(scans, "interrupted crawl did not retain a scan artifact")

                _SlowSite.delay = 0
                runner.resume(str(scans[0]), str(project))
                wait_for(lambda: runner.active, "retained scan did not begin its explicit resume")
                wait_for(lambda: not runner.active, "retained scan did not finish its explicit resume")
                status = subprocess.run(
                    [core, "scan-status", "--scan", str(scans[0])],
                    check=True,
                    capture_output=True,
                    text=True,
                ).stdout
                self.assertIn('"lifecycle": "finished"', status)
        finally:
            if previous is None:
                os.environ.pop("SEOHEAD_ALLOW_PRIVATE_HOSTS", None)
            else:
                os.environ["SEOHEAD_ALLOW_PRIVATE_HOSTS"] = previous
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
