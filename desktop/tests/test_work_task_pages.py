import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from seohead_desktop.common import TASK_PAGE_LIMIT
from seohead_desktop.scans_urls import ScansUrlsMixin


class _Host:
    """Only what load_task_page touches: the project, the command starter and the single-list loader."""

    load_task_page = ScansUrlsMixin.load_task_page

    def __init__(self):
        self.project_directory = "/projects/owner"
        self.started = []
        self.loaded = []

    def start_command(self, operation, tool, arguments, handler):
        self.started.append((operation, tool, arguments, handler))

    def load_tasks(self, result):
        self.loaded.append(result)


def _page(offset, count, total, next_offset):
    items = [{"id": f"check:{n}", "title": f"Task {n}", "kind": "check", "display_state": "remaining"} for n in range(offset, offset + count)]
    return {"items": items, "pagination": {"total": total, "offset": offset, "limit": TASK_PAGE_LIMIT, "next_offset": next_offset}}


class WorkTaskPagesTests(unittest.TestCase):
    def test_every_page_is_read_and_shown_as_one_list(self):
        host = _Host()
        load = ScansUrlsMixin.load_task_page
        load(host, _page(0, 100, 277, 100))
        self.assertEqual(len(host.started), 1)
        operation, tool, arguments, handler = host.started[0]
        self.assertEqual((operation, tool), ("tasks", "seo_project_checklist_page"))
        self.assertEqual(arguments, {"directory": "/projects/owner", "limit": TASK_PAGE_LIMIT, "offset": 100})
        self.assertEqual(host.loaded, [])
        handler(_page(100, 100, 277, 200))
        self.assertEqual(host.started[-1][2]["offset"], 200)
        host.started[-1][3](_page(200, 77, 277, None))
        self.assertEqual(len(host.loaded), 1)
        merged = host.loaded[0]
        self.assertEqual(len(merged["items"]), 277)
        self.assertEqual(merged["items"][-1]["id"], "check:276")
        self.assertEqual(merged["pagination"]["total"], 277)
        self.assertIsNone(merged["pagination"]["next_offset"])
        self.assertEqual(merged["pagination"]["offset"], 0)

    def test_single_page_is_shown_without_another_request(self):
        host = _Host()
        ScansUrlsMixin.load_task_page(host, _page(0, 3, 3, None))
        self.assertEqual(host.started, [])
        self.assertEqual([len(result["items"]) for result in host.loaded], [3])

    def test_a_new_first_page_discards_pages_of_an_earlier_read(self):
        host = _Host()
        ScansUrlsMixin.load_task_page(host, _page(0, 100, 150, 100))
        ScansUrlsMixin.load_task_page(host, _page(0, 2, 2, None))
        self.assertEqual([len(result["items"]) for result in host.loaded], [2])


if __name__ == "__main__":
    unittest.main()
