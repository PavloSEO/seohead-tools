"""Contracts for the owned loopback SEO QA site; no crawler runs here."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import tempfile
import threading
import unittest


SOURCE = Path(__file__).resolve().parents[1] / "examples" / "qa-site" / "qa_site.py"
SPEC = importlib.util.spec_from_file_location("seohead_qa_site", SOURCE)
assert SPEC and SPEC.loader
qa = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(qa)


class QaSiteTests(unittest.TestCase):
    def test_broken_profile_exposes_concrete_statuses_redirects_and_headers(self):
        for path, expected in {"/missing": 404, "/gone": 410, "/server-error": 503, "/optional/rate-limited": 429}.items():
            with self.subTest(path=path):
                status, _headers, _body = qa._body("broken", path)
                self.assertEqual(status, expected)
        status, headers, _body = qa._body("broken", "/redirect-one")
        self.assertEqual((status, headers["Location"]), (301, "/redirect-two"))
        status, headers, _body = qa._body("broken", "/loop-a")
        self.assertEqual((status, headers["Location"]), (302, "/loop-b"))
        status, headers, body = qa._body("broken", "/optional/slow")
        body = body.decode()
        self.assertEqual(status, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(headers["X-Content-Type-Options"], "nosniff")
        self.assertIn("Optional slow fixture", body)

    def test_catalogue_is_explicit_about_js_prerequisite_and_known_facts(self):
        scenarios = {item["id"]: item for item in qa.catalogue("broken")["scenarios"]}
        self.assertEqual(scenarios["http-statuses"]["expected"]["/gone"], 410)
        self.assertEqual(scenarios["content"]["expected"]["duplicate_pair"], ["/broken/duplicate-a", "/broken/duplicate-b"])
        self.assertIn("renderer", scenarios["rendering"]["prerequisite"])
        self.assertIn("unavailable", scenarios["rendering"]["unavailable_without"])

    def test_clean_profile_removes_broken_internal_links_and_sitemap_missing_entry(self):
        status, _headers, body = qa._body("clean", "/")
        self.assertEqual(status, 200)
        self.assertIn("/clean/alpha", body.decode())
        self.assertNotIn("/missing", body.decode())
        sitemap = {item["id"]: item for item in qa.catalogue("clean")["scenarios"]}["robots-and-sitemap"]
        self.assertIsNone(sitemap["expected"]["sitemap_missing"])

    def test_manifest_and_catalogue_are_repeatable_and_do_not_embed_machine_path(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            manifest = root / "manifest.json"
            catalogue = root / "catalogue.json"
            self.assertEqual(qa.main(["manifest", "--profile", "broken", "--output", str(manifest)]), 0)
            self.assertEqual(qa.main(["catalogue", "--profile", "broken", "--output", str(catalogue)]), 0)
            first = json.loads(manifest.read_text())
            self.assertEqual(qa.main(["manifest", "--profile", "broken", "--output", str(manifest)]), 0)
            self.assertEqual(first, json.loads(manifest.read_text()))
            self.assertEqual(json.loads(catalogue.read_text())["profile"], "broken")
            self.assertEqual(first["source"], "qa_site.py")
            self.assertNotIn(str(SOURCE.parent), manifest.read_text())

    def test_non_loopback_binding_is_rejected(self):
        with self.assertRaises(SystemExit):
            qa.main(["serve", "--host", "0.0.0.0"])

    def test_http_server_is_loopback_owned_when_socket_binding_is_available(self):
        try:
            server = qa.QaSiteServer(("127.0.0.1", 0), "broken", 0)
        except PermissionError as exc:
            self.skipTest(f"loopback socket blocked by this execution sandbox: {exc}")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            self.assertEqual(server.server_address[0], "127.0.0.1")
            self.assertGreater(server.server_port, 0)
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
