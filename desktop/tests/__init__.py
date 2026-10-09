"""Test package. One QApplication for the whole run; Python never tears Qt objects down at exit."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import sip
from PyQt5.QtWidgets import QApplication

sip.setdestroyonexit(False)  # otherwise dealloc_QApplication crashes the interpreter on exit (SIGSEGV)
APP = QApplication.instance() or QApplication([])
