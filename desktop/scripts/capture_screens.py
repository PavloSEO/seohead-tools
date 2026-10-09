#!/usr/bin/env python3
"""Render native screens offscreen to PNG: capture_screens.py OUT_DIR NAME [NAME ...] [--theme light] [--lang ru|en] [--sizes 1440x900,800x800].

NAME: settings:<section id> | shell[:<section>] | newscan[:state] | scanset:<page> | quickscan[:state] | menu | gallery (the three scan kinds: capture_scan_dialog.py). In-memory settings; scans only through --project.
Options for shell: --project DIR opens an existing project through the core CLI (read-only; e.g. the QA project) and
--display simple switches the display; ``shell:scans`` selects a navigation section after the project has loaded.
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

from seohead_desktop import i18n, qt, theming
from seohead_desktop.app import load_theme
from seohead_desktop.settings_store import AppSettings
from seohead_desktop.ui.settings import full_schema
from seohead_desktop.ui.settings.context import SettingsContext
from seohead_desktop.ui.settings.dialog import SettingsDialog

OPTIONS = {}


def open_project(window, directory, timeout=60):
    """Read an existing project through the core and wait (event loop running) until the window is idle."""
    import time

    from PyQt5.QtWidgets import QApplication

    window.core_executable = OPTIONS.get("core") or window.core_executable
    window.read_project(str(Path(directory).resolve()))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if window.project_result is not None and not window._project_loading and not window.requests and window._workspace_restore is None:
            break
        time.sleep(0.05)
    for _ in range(40):  # let queued follow-up reads (scans, tasks, observer) land
        QApplication.processEvents()
        time.sleep(0.05)


def render_modal(dialog, width, height, theme, lang):
    """Settings are a modal window: paint the dialog centred over the dimmed application window, as in the canvas."""
    from PyQt5.QtGui import QColor, QPainter

    from seohead_desktop.app import MainWindow

    window = MainWindow(persistent=False)
    window.prefs.set("view.theme", theme)
    window.prefs.set("view.language", lang)
    window.show_startup_workspace()
    if OPTIONS.get("project"):
        open_project(window, OPTIONS["project"])
    window.setAttribute(Qt.WA_DontShowOnScreen, True)
    window.resize(width, height)
    window.show()
    QApplication.processEvents()
    window.resize(width, height)  # the offscreen screen is 800x600 and clamps the first resize
    QApplication.processEvents()
    image = QPixmap(width, height)
    window.render(image)
    size = dialog.size().boundedTo(image.size() * 0.92)
    dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
    cap = getattr(dialog, "capture_max", (920, 640))
    dialog.resize(min(size.width(), cap[0]), min(size.height(), cap[1]))
    dialog.show()
    QApplication.processEvents()
    painter = QPainter(image)
    painter.fillRect(image.rect(), QColor(0, 0, 0, 100))
    shot = dialog.grab()
    painter.drawPixmap((width - shot.width()) // 2, (height - shot.height()) // 2, shot)
    painter.end()
    window.close()
    return image


def projsources_dialog(state, theme, lang):
    """«Настройки проекта» over a QA-project window; the readiness answers are the saved real core answer (tests/core_fixtures).

    States: ready (as the core answered) | connected (verified access, as provider-verify would report) | key (nothing
    configured) | error (unreadable keys) | failed (the request failed) | partial (empty list) | loading | noproject.
    """
    import copy

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from seohead_desktop.app import MainWindow
    from seohead_desktop.screens.project_sources_page import ProjectSettingsDialog
    from tests._screens_core import fixture, open_qa

    window = MainWindow(persistent=False)
    window.prefs.set("view.theme", theme)
    window.prefs.set("view.language", lang)
    if state != "noproject":
        open_qa(window)
    data = copy.deepcopy(fixture("provider_readiness.json"))
    for provider in data["providers"].values():
        if state == "connected" and provider["readiness_state"] == "configured_unverified":
            provider["readiness_state"] = "verified"
        elif state == "key" and provider["readiness_state"] != "not_required":
            provider["readiness_state"] = "missing"
        elif state == "error" and provider["readiness_state"] == "configured_unverified":
            provider["readiness_state"] = "invalid"
    if state == "partial":
        data["providers"] = {}

    def request(callback, on_error):
        if state == "failed":
            on_error("CLI ядра seohead не найден")
        elif state != "loading":
            callback(data)

    window.request_providers = request
    dialog = ProjectSettingsDialog(window, window)
    dialog._owner_window = window
    dialog.capture_max = (960, 820)
    return dialog


def build(name, width, height, store, theme="light", lang="ru"):
    kind, _, arg = name.partition(":")
    i18n.set_language(lang)
    if kind == "settings":
        dialog = SettingsDialog(store, SettingsContext(), section=arg or "general")
        dialog.resize(width, height)
        return dialog
    if kind == "shell":
        from seohead_desktop.app import MainWindow

        window = MainWindow(persistent=False)
        window.prefs.set("view.theme", theme)
        window.prefs.set("view.language", lang)
        if arg == "simple" or OPTIONS.get("display") == "simple":
            window.set_display("simple", remember=False)
        window.show_startup_workspace()
        if OPTIONS.get("project"):
            open_project(window, OPTIONS["project"])
        if arg and arg != "simple":
            window.navigation.select_section(arg)
        return window
    if kind in ("newscan", "scanset", "quickscan"):
        from capture_scan_dialog import Job

        return Job(name, OPTIONS, open_project)
    if kind == "projsources":
        return projsources_dialog(arg or "ready", theme, lang)
    if kind == "sources":
        from capture_sources import dialog

        return dialog(arg or "list")
    if kind == "menu":
        from seohead_desktop.app import MainWindow

        window = MainWindow(persistent=False)
        window.prefs.set("view.theme", theme)
        window.prefs.set("view.language", lang)
        return window.build_profile_menu()
    if kind == "gallery":
        from seohead_desktop.ui.theme_gallery import build_board
        return build_board(width, height)
    raise SystemExit(f"unknown screen: {name}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("names", nargs="+")
    parser.add_argument("--theme", default="light", choices=theming.THEMES)
    parser.add_argument("--lang", default="ru", choices=i18n.LANGUAGES)
    parser.add_argument("--sizes", default="1440x900,800x800")
    parser.add_argument("--project", type=Path, help="existing project directory to open through the core (read-only)")
    parser.add_argument("--core", help="seohead CLI executable (default: .venv-desktop/bin/seohead next to the repo)")
    parser.add_argument("--display", choices=("agent", "simple"), default="agent")
    args = parser.parse_args(argv)
    default_core = Path(__file__).resolve().parents[2] / ".venv-desktop/bin/seohead"
    OPTIONS.update(project=args.project, display=args.display, core=args.core or (str(default_core) if default_core.exists() else None))
    os.environ.setdefault("SEOHEAD_ALLOW_PRIVATE_HOSTS", "crawl.localhost,127.0.0.1")
    app = qt.app(sys.argv[:1])
    app.setStyle("Fusion")
    load_theme(app, args.theme)
    store = AppSettings(schema=full_schema())
    args.out_dir.mkdir(parents=True, exist_ok=True)
    for name in args.names:
        for size in args.sizes.split(","):
            width, height = (int(v) for v in size.split("x"))
            widget = build(name, width, height, store, args.theme, args.lang)
            suffix = "" if args.lang == "ru" else f"-{args.lang}"
            path = args.out_dir / f"{name.replace(':', '-')}-{args.theme}-{size}{suffix}.png"
            if name.startswith(("settings", "projsources", "sources")):
                image = render_modal(widget, width, height, args.theme, args.lang)
                image.save(str(path))
            elif hasattr(widget, "render_image"):
                widget.render_image(width, height, args.theme, args.lang).save(str(path))
            elif name.startswith("shell"):
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
                widget.resize(widget.sizeHint())  # a popup taller than the 800x600 offscreen screen is clamped otherwise
                app.processEvents()
                image = QPixmap(widget.size())
                widget.render(image)
                image.save(str(path))
            print(path)
            widget.close()
    app.quit()
    return 0


if __name__ == "__main__":
    qt.exit_now(main())  # no interpreter finalisation: Qt objects must not be torn down by Python
