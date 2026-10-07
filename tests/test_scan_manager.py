"""Actual bounded multi-run acceptance on one owned loopback origin."""

import json
import os
from http.server import ThreadingHTTPServer
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import unittest
import uuid
from datetime import datetime, timezone

from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from seohead_desktop.scan_manager import LocalScanManager
from tests.test_scan_runner import _SlowSite


class LocalScanManagerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_three_owned_projects_stop_one_and_resume_it(self):
        core = os.environ.get("SEOHEAD_DESKTOP_CORE_CLI")
        if not core or not Path(core).is_file():
            self.skipTest("set SEOHEAD_DESKTOP_CORE_CLI to run the multi-run native gate")
        source_root = Path(core).resolve().parents[2]
        source_status = subprocess.run(
            ["git", "-C", str(source_root), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        if source_status.returncode or source_status.stdout:
            self.skipTest("the external development core must be a clean checkout for provenance")
        old_allow = os.environ.get("SEOHEAD_ALLOW_PRIVATE_HOSTS")
        os.environ["SEOHEAD_ALLOW_PRIVATE_HOSTS"] = "crawl.localhost,127.0.0.1"
        server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowSite)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        scratch_root = Path(__file__).resolve().parents[1] / ".build"
        scratch_root.mkdir(exist_ok=True)

        def wait_for(predicate, message, timeout=15):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                self.app.processEvents()
                if predicate():
                    return
                QTest.qWait(30)
            self.fail(message() if callable(message) else message)

        try:
            _SlowSite.delay = 0.05
            with tempfile.TemporaryDirectory(prefix="multi-run-", dir=scratch_root) as temporary:
                root = Path(temporary)
                projects = []
                for index in range(3):
                    project = root / f"project-{index}"
                    target = f"http://crawl.localhost:{server.server_port}/"
                    (project / "scans").mkdir(parents=True)
                    (project / "reports").mkdir()
                    (project / "log.md").write_text("# Owned fixture\n", encoding="utf-8")
                    document = {
                        "format": "seohead.project.v1",
                        "version": 1,
                        "project_uuid": str(uuid.uuid4()),
                        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                        "site": {"host": "crawl.localhost", "target": target, "label": "Owned multi-run fixture"},
                        "facts": [],
                        "template_references": [],
                        "profile_references": [],
                        "artifact_directories": {"scans": "scans", "reports": "reports", "log": "log.md"},
                    }
                    (project / "project.json").write_text(json.dumps(document), encoding="utf-8")
                    projects.append((project, document))

                manager = LocalScanManager(core, max_parallel=3)
                ids = [
                    manager.submit(
                        project=str(project),
                        project_uuid=document["project_uuid"],
                        max_urls=8,
                        rendering_mode="raw",
                        overrides=(("limits.max_requests", 100), ("limits.max_crawl_seconds", 60)),
                        approve_large_crawl=False,
                        max_urls_per_second=2.0,
                    )
                    for project, document in projects
                ]
                wait_for(lambda: manager.active_count == 3, "three owned local captures did not start")
                wait_for(
                    lambda: all(any((project / "scans").glob("*.sqlite")) for project, _ in projects),
                    lambda: "one of the owned captures did not retain its own artifact: " + json.dumps(
                        [manager.detail(item) for item in ids], ensure_ascii=False
                    ),
                )
                first_scan = next((projects[0][0] / "scans").glob("*.sqlite"))
                self.assertTrue(manager.stop(ids[0]))
                wait_for(
                    lambda: manager.detail(ids[0])["state"] in {"interrupted", "finished", "failed"},
                    "selected owned capture did not reach a terminal state",
                )
                self.assertGreaterEqual(manager.active_count, 1, "stopping one run stopped every capture")

                resumed = manager.resume(
                    project=str(projects[0][0]),
                    project_uuid=projects[0][1]["project_uuid"],
                    artifact=str(first_scan),
                )
                wait_for(
                    lambda: manager.detail(resumed)["state"] in {"running", "finished", "failed", "interrupted"},
                    "explicit resume was not admitted to the bounded queue",
                )
                wait_for(
                    lambda: all(
                        item["state"] in {"finished", "failed", "interrupted"}
                        for item in manager.snapshot()
                    ),
                    "owned queue did not reach terminal states",
                    timeout=15,
                )
                project_ids = {item["project_uuid"] for item in manager.snapshot()}
                self.assertEqual(project_ids, {document["project_uuid"] for _, document in projects})
        finally:
            if old_allow is None:
                os.environ.pop("SEOHEAD_ALLOW_PRIVATE_HOSTS", None)
            else:
                os.environ["SEOHEAD_ALLOW_PRIVATE_HOSTS"] = old_allow
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
