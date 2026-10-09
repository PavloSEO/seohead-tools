#!/usr/bin/env python3
"""Render native screens offscreen to PNG: capture_screens.py OUT_DIR NAME [NAME ...] [--theme light] [--sizes 1440x900,800x800].

NAME: settings:<section id> | shell | gallery. Synthetic in-memory settings only; no core, network or scans.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt5.QtCore import Qt
from PyQt5.QtGui import QPixmap
from PyQt5.QtWidgets import QApplication

from seohead_desktop import theming
from seohead_desktop.app import load_theme
from seohead_desktop.settings_store import AppSettings
from seohead_desktop.ui.settings import full_schema
from seohead_desktop.ui.settings.context import SettingsContext
from seohead_desktop.ui.settings.dialog import SettingsDialog


def build(name, width, height, store, theme="light"):
    kind, _, arg = name.partition(":")
    if kind == "settings":
        dialog = SettingsDialog(store, SettingsContext(), section=arg or "general")
        dialog.resize(width, height)
        return dialog
    if kind == "shell":
        from seohead_desktop.app import MainWindow

        window = MainWindow(persistent=False)
        window.prefs.set("view.theme", theme)
        if arg == "simple":
            window.set_display("simple", remember=False)
        return window
    if kind == "gallery":
        from seohead_desktop.ui.theme_gallery import build_board
        return build_board(width, height)
    raise SystemExit(f"unknown screen: {name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("names", nargs="+")
    parser.add_argument("--theme", default="light", choices=theming.THEMES)
    parser.add_argument("--sizes", default="1440x900,800x800")
    args = parser.parse_args(argv)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    load_theme(app, args.theme)
    store = AppSettings(schema=full_schema())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name in args.names:
        for size in args.sizes.split(","):
            width, height = (int(v) for v in size.split("x"))
            widget = build(name, width, height, store, args.theme)
            path = args.out_dir / f"{name.replace(':', '-')}-{args.theme}-{size}.png"
            if name.startswith("shell"):
                # The offscreen screen is 800x600 and clamps top-level windows; render at the requested size instead.
                widget.setAttribute(Qt.WA_DontShowOnScreen, True)
                widget.resize(width, height)
                widget.show()
                app.processEvents()
                widget.resize(width, height)
                app.processEvents()
                image = QPixmap(width, height)
                widget.render(image)
                image.save(str(path))
            else:
                widget.show()
                app.processEvents()
                widget.grab().save(str(path))
            print(path)
            widget.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
