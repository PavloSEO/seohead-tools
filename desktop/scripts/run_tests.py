#!/usr/bin/env python3
"""Run the desktop test-suite and leave without interpreter finalisation (PyQt crashes there with SIGSEGV).

Usage (from desktop/): python scripts/run_tests.py [unittest-discover arguments, e.g. -p "test_screen_*.py"]
"""

import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path[:0] = [str(ROOT), str(ROOT / "src")]

from seohead_desktop import qt  # noqa: E402


def main(argv):
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), **_options(argv))
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    return 0 if result.wasSuccessful() else 1


def _options(argv):
    options = {}
    if "-p" in argv:
        options["pattern"] = argv[argv.index("-p") + 1]
    return options


if __name__ == "__main__":
    qt.exit_now(main(sys.argv[1:]))
