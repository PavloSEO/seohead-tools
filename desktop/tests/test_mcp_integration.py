"""Consent and background-only CLI contracts with temporary fake configurations."""
import threading
import time
import unittest

from PyQt5.QtWidgets import QApplication

from seohead_desktop.mcp_integration import IntegrationPanel, PermissionDialog

APP = QApplication.instance() or QApplication([])


def wait_for(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline and not predicate():
        APP.processEvents()
        time.sleep(0.005)
    assert predicate()


class IntegrationTests(unittest.TestCase):
    def test_cancel_approval_and_worker(self):
        calls, threads = [], []
        plan = {"ok": True, "file": "/temporary/config.toml", "block": '[mcp_servers.seohead]\ncommand = "/opt/seohead"', "backup": "/temporary/config.bak", "sha256": "abc", "changed": True}
        def runner(_exe, arguments):
            calls.append(arguments)
            threads.append(threading.get_ident())
            return plan
        first = PermissionDialog("/opt/seohead", "codex", runner=runner)
        wait_for(lambda: first.plan is not None)
        self.assertIn(plan["block"], first.preview.toPlainText())
        first.reject()
        self.assertEqual(len(calls), 1)
        self.assertIn("--dry-run", calls[0])
        second = PermissionDialog("/opt/seohead", "codex", runner=runner)
        wait_for(lambda: second.plan is not None)
        second.allow.click()
        wait_for(lambda: second.allow.isHidden())
        self.assertEqual(len(calls), 3)
        self.assertIn("--yes", calls[-1])
        self.assertIn("--expected-sha256", calls[-1])
        self.assertTrue(all(t != threading.get_ident() for t in threads))
        second.close()

    def test_switch_tracks_core(self):
        state = {"ok": True, "enabled": True, "profile": "full", "tools": 3, "file": "/temporary/mcp.json", "by": "CLI", "clients": {"codex": {"registered": False}}}
        calls = []
        def runner(_exe, arguments):
            calls.append(arguments)
            if arguments[0] == "disable":
                state["enabled"] = False
            return dict(state)
        panel = IntegrationPanel("/opt/seohead", runner=runner, polling=False)
        panel.show()
        wait_for(lambda: panel.state is not None)
        self.assertTrue(panel.switch.isChecked())
        panel.switch.click()
        wait_for(lambda: not panel.busy)
        self.assertFalse(panel.switch.isChecked())
        self.assertEqual(calls[-1], ["disable", "--actor", "SEOHEAD Desktop"])
        state["enabled"] = True
        panel.show()
        panel.refresh()
        wait_for(lambda: not panel.busy)
        self.assertTrue(panel.switch.isChecked())
        panel.close()

    def test_error_never_writes(self):
        calls = []
        def runner(_exe, arguments):
            calls.append(arguments)
            raise ValueError("Core unavailable")
        dialog = PermissionDialog("/opt/seohead", "cursor", runner=runner)
        wait_for(lambda: not dialog.busy)
        self.assertFalse(dialog.allow.isEnabled())
        self.assertIn("Core unavailable", dialog.result_label.text())
        self.assertEqual(len(calls), 1)
        dialog.close()
