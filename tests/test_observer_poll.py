"""The single project observer keeps independent agent work visible."""

import unittest

from PyQt5.QtWidgets import QApplication

from seohead_desktop.app import MainWindow


class ObserverPollTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.window = MainWindow(persistent=False)
        self.addCleanup(self.window.close)
        self.window.project_directory = "/owned/project"
        self.window.current_project_uuid = "project-A"
        self.calls = []
        self.window.start_command = lambda *args: self.calls.append(args)

    def test_idle_project_polls_without_a_local_scan_manager(self):
        self.assertIsNone(self.window.scan_manager)
        self.window.poll_active_scan()
        self.assertEqual(self.calls[0][1], "seo_project_observe")
        self.assertEqual(self.calls[0][2]["directory"], "/owned/project")
        self.assertEqual(self.window.scan_poll_timer.interval(), 2000)

    def test_running_external_observation_changes_cadence_without_ownership(self):
        self.window.observed_runs = [{"id": "external", "state": "running"}]
        self.window.poll_active_scan()
        self.assertEqual(self.window.scan_poll_timer.interval(), 500)
        self.assertIsNone(self.window.selected_managed_run_id)
        self.assertFalse(self.window.stop_run_button.isEnabled())
        self.window.observed_runs = [{"id": "external", "state": "finished"}]
        self.window.poll_active_scan()
        self.assertEqual(self.window.scan_poll_timer.interval(), 2000)

    def test_inflight_project_transition_and_pending_write_do_not_queue_reads(self):
        for change in ({"_project_loading": True}, {"_pending_note": {"id": "write"}}, {"active_commands": {"observer": "observer"}}, {"_close_waiting": True}):
            old = {key: getattr(self.window, key) for key in change}
            for key, value in change.items():
                setattr(self.window, key, value)
            self.window.poll_active_scan()
            self.assertEqual(self.calls, [])
            self.assertEqual(self.window.pending_commands, {})
            for key, value in old.items():
                setattr(self.window, key, value)


if __name__ == "__main__":
    unittest.main()
