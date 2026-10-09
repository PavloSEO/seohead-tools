#!/usr/bin/env python3
"""Capture the source slice against an explicit core CLI and read-only project.

No fixtures, credential writes, verification calls, sync, or scan dispatch.
Use a core CLI with an isolated empty credential store for secret-free QA.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PyQt5.QtCore import QPoint, Qt, QTimer
from PyQt5.QtGui import QPainter, QPixmap
from PyQt5.QtTest import QTest
from PyQt5.QtWidgets import QApplication

from seohead_desktop.app import MainWindow
from seohead_desktop.source_service import load_sources


def manifest(project):
    return {str(p.relative_to(project)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in project.rglob("*") if p.is_file()}


def wait(app, predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return
        QTest.qWait(25)
    raise RuntimeError("Core read did not finish within the capture budget")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-cli", required=True)
    parser.add_argument("--project", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    if not (args.project / "project.json").is_file() and (args.project / "project/project.json").is_file():
        args.project = args.project / "project"
    if not (args.project / "project.json").is_file():
        parser.error("The capture needs an existing project.json, not a report container")
    args.out.mkdir(parents=True, exist_ok=True)
    before = manifest(args.project)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    screens = (("sources", "list", None), ("oauth", "detail", "gsc"), ("key", "detail", "arsenkin"),
               ("spend", "spend", None), ("agent-config", "agent", None))
    captured = []
    for theme in ("light", "dark", "hc"):
        for width, height in ((1440, 900), (800, 800)):
            window = MainWindow(persistent=False, core_executable=args.core_cli)
            try:
                window.setAttribute(Qt.WA_DontShowOnScreen, True)
                window.prefs.set("view.theme", theme)
                window.show()
                window.read_project(str(args.project))
                wait(app, lambda w=window: w.project_directory and not w._project_loading)
                window.scan_poll_timer.stop()
                wait(app, lambda w=window: not w.requests)
                window.resize(width, height)
                app.processEvents()
                window.resize(width, height)
                errors = []

                def capture_modal(w=window, t=theme, x=width, y=height, error_bucket=errors):
                    dialog = app.activeModalWidget()
                    try:
                        assert dialog and dialog.isModal(), "Settings did not open modally"
                        assert not any(c.view_id == "settings" for c in w.workspace_tabs.contexts())
                        dialog.setAttribute(Qt.WA_DontShowOnScreen, True)
                        dialog.resize(min(dialog.width(), x), min(dialog.height(), y))
                        page = dialog._pages["sources"][1]
                        wait(app, lambda: not page._busy)
                        assert page.snapshot.get("registry"), "No core registry returned"
                        for name, view, provider in screens:
                            dialog.show_section("sources")
                            page.navigate(view, provider)
                            wait(app, lambda: not page._busy)
                            if view == "agent":
                                dialog.show_section("agent")
                                agent_page = dialog._pages["agent"][1]
                                from PyQt5.QtWidgets import QPushButton

                                next(b for b in agent_page.findChildren(QPushButton) if b.text() == "Агент настраивает приложение").click()
                            QTest.qWait(100)
                            scroll = dialog._pages[dialog.current_section()][0]
                            scroll.verticalScrollBar().setValue(0)
                            assert scroll.horizontalScrollBar().maximum() == 0, "Source sheet requires horizontal scrolling"
                            image = QPixmap(x, y)
                            w.render(image)
                            painter = QPainter(image)
                            dialog.render(painter, QPoint((x - dialog.width()) // 2, (y - dialog.height()) // 2))
                            painter.end()
                            path = args.out / f"{name}-{t}-{x}x{y}.png"
                            assert image.save(str(path))
                            captured.append(path.name)
                        page.doctor()
                        wait(app, lambda: not page._busy)
                        assert page._doctor is not None
                    except Exception as exc:
                        error_bucket.append(exc)
                    finally:
                        if dialog:
                            dialog.reject()

                QTimer.singleShot(0, capture_modal)
                window.open_settings("sources")
                if errors:
                    raise errors[0]
                menu = window.build_profile_menu()
                menu.ensurePolished()
                menu.resize(menu.sizeHint())
                image = QPixmap(width, height)
                window.render(image)
                painter = QPainter(image)
                point = window.navigation.profile.mapTo(window, QPoint(0, 0))
                menu.render(painter, QPoint(point.x(), max(0, point.y() - menu.height() - 6)))
                painter.end()
                path = args.out / f"profile-{theme}-{width}x{height}.png"
                assert image.save(str(path))
                captured.append(path.name)
                menu.deleteLater()
                print(f"captured {theme} {width}x{height}", flush=True)
            finally:
                window.close()
                wait(app, lambda w=window: w.pool.activeThreadCount() == 0)
                window.deleteLater()
                app.processEvents()
    after = manifest(args.project)
    evidence = {"screenshots": captured, "project_unchanged": before == after, "project_files": len(before),
                "core_read": load_sources(args.core_cli, "snapshot", str(args.project)),
                "doctor": load_sources(args.core_cli, "doctor")}
    (args.out / "runtime-evidence.json").write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + "\n")
    if before != after:
        raise RuntimeError("Read-only project bytes changed during capture")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
