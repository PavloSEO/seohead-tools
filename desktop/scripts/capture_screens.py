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

from PyQt5.QtWidgets import QApplication  # noqa: E402

from seohead_desktop import theming  # noqa: E402
from seohead_desktop.app import load_theme  # noqa: E402
from seohead_desktop.settings_store import AppSettings  # noqa: E402
from seohead_desktop.ui.settings import full_schema  # noqa: E402
from seohead_desktop.ui.settings.context import SettingsContext  # noqa: E402
from seohead_desktop.ui.settings.dialog import SettingsDialog  # noqa: E402


def build(name, width, height, store):
    kind, _, arg = name.partition(":")
    if kind == "settings":
        dialog = SettingsDialog(store, SettingsContext(), section=arg or "general")
        dialog.resize(width, height)
        return dialog
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
            widget = build(name, width, height, store)
            widget.show()
            app.processEvents()
            path = args.out_dir / f"{name.replace(':', '-')}-{args.theme}-{size}.png"
            widget.grab().save(str(path))
            print(path)
            widget.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
