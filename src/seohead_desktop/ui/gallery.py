"""Offline native component gallery: python -m seohead_desktop.ui.gallery."""

import argparse
import json
import platform
import sys
from pathlib import Path

from PyQt5.QtCore import PYQT_VERSION_STR, QT_VERSION_STR, Qt, QTimer
from PyQt5.QtWidgets import (
    QApplication,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMainWindow,
    QMenu,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..app import load_theme
from .components import TabConfigurationDialog, material_icon
from .panels import AuditWorkspace, ProjectPanels, component_stylesheet
from .tabcatalogue import ALL_TABS, MAIN_TABS, PROJECT_TABS

SOURCE = "Демо · example.test · сохранённый набор"
ROWS = [
    {
        "url": "https://catalog.example.test/",
        "status": 200,
        "type": "text/html",
        "indexability": "Indexable",
        "title": "Catalog — example store",
        "issues": 0,
        "crawl_depth": 0,
        "description": "A synthetic catalogue fixture for native component verification.",
    },
    {
        "url": "https://catalog.example.test/chairs/",
        "status": 200,
        "type": "text/html",
        "indexability": "Indexable",
        "title": "Chairs for every room",
        "issues": 1,
        "crawl_depth": 1,
        "description": "Synthetic category description.",
    },
    {
        "url": "https://catalog.example.test/chairs/oak/",
        "status": 200,
        "type": "text/html",
        "indexability": "Indexable",
        "title": "Oak chair",
        "issues": 0,
        "crawl_depth": 2,
    },
    {
        "url": "https://catalog.example.test/journal/care/",
        "status": 200,
        "type": "text/html",
        "indexability": "Indexable",
        "title": "",
        "issues": 1,
        "crawl_depth": 2,
    },
    {
        "url": "https://catalog.example.test/archive/desk/",
        "status": 301,
        "type": "text/html",
        "indexability": "Non-Indexable",
        "title": None,
        "issues": 1,
        "crawl_depth": 2,
    },
    {
        "url": "https://catalog.example.test/search/?q=chair",
        "status": 200,
        "type": "text/html",
        "indexability": "Non-Indexable",
        "title": "Search results",
        "issues": 0,
        "crawl_depth": 1,
    },
    {
        "url": "https://catalog.example.test/images/chair.webp",
        "status": 200,
        "type": "image/webp",
        "indexability": None,
        "title": None,
        "issues": None,
        "crawl_depth": 2,
    },
    {
        "url": "https://catalog.example.test/discontinued/",
        "status": 404,
        "type": "text/html",
        "indexability": "Non-Indexable",
        "title": "Product unavailable",
        "issues": 1,
        "crawl_depth": 2,
    },
]
SCANS = [
    {
        "uuid": "demo-native-02",
        "start_url": "https://catalog.example.test/",
        "source_kind": "native",
        "lifecycle": "completed",
        "finished_at": "2026-10-07 10:00",
        "partial": "нет",
    },
    {
        "uuid": "demo-native-01",
        "start_url": "https://catalog.example.test/",
        "source_kind": "native",
        "lifecycle": "completed",
        "finished_at": "2026-10-06 10:00",
        "partial": "да",
    },
]
TASKS = [
    {
        "id": "demo-title",
        "title": "Проверить пустой Title",
        "kind": "finding",
        "state": "open",
        "complete": False,
        "stale": False,
        "attempt_status": "not_started",
        "applicability": "applicable",
        "blocked_by": [],
        "reason": "Синтетическая запись для проверки выбора задачи",
    },
    {
        "id": "demo-recheck",
        "title": "Перепроверить сохранённый скан",
        "kind": "workflow",
        "state": "blocked",
        "complete": False,
        "stale": True,
        "attempt_status": "not_started",
        "applicability": "applicable",
        "blocked_by": ["demo-title"],
        "reason": "Зависимость показана, выполнение не запускалось",
    },
]


class GalleryWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SEOHEAD — галерея нативных компонентов · демо")
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)
        root = QWidget()
        root.setObjectName("workspace")
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        top = QWidget()
        top.setObjectName("topbar")
        topbar = QHBoxLayout(top)
        topbar.setContentsMargins(16, 10, 16, 10)
        self.nav_toggle = QPushButton()
        self.nav_toggle.setIcon(material_icon("menu"))
        self.nav_toggle.setAccessibleName("Показать или скрыть навигацию")
        topbar.addWidget(self.nav_toggle)
        brand = QLabel("SEOHEAD")
        brand.setObjectName("brand")
        topbar.addWidget(brand)
        project = QLabel("Каталог компонентов")
        topbar.addWidget(project)
        topbar.addStretch()
        badge = QLabel("Демо · синтетические данные")
        badge.setObjectName("sourceBadge")
        topbar.addWidget(badge)
        self.restore = QPushButton("Панели")
        self.restore.setIcon(material_icon("view_sidebar"))
        topbar.addWidget(self.restore)
        layout.addWidget(top)
        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("tabNavigation")
        self.navigation.setAccessibleName("Разделы SEOHEAD")
        self.navigation.setFixedWidth(200)
        self.navigation.addItems(
            ["URL и аудит", *(spec.title for spec in PROJECT_TABS)]
        )
        body.addWidget(self.navigation)
        self.stack = QStackedWidget()
        body.addWidget(self.stack, 1)
        layout.addLayout(body, 1)
        self.audit = AuditWorkspace()
        self.projects = ProjectPanels()
        self.stack.addWidget(self.audit)
        self.stack.addWidget(self.projects)
        self.navigation.currentRowChanged.connect(self.navigate)
        self.nav_toggle.clicked.connect(
            lambda: self.navigation.setVisible(not self.navigation.isVisible())
        )
        panels_menu = QMenu(self.restore)
        for title, widget in (
            ("Правая сводка", self.audit.right),
            ("Инспектор URL", self.audit.detail),
        ):
            action = panels_menu.addAction(title)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(widget.setVisible)
        panels_menu.addAction("Восстановить панели", self.audit.restore_panels)
        self.restore.setMenu(panels_menu)
        self.audit.intent_requested.connect(self.handle_intent)
        self.projects.intent_requested.connect(self.handle_intent)
        self.demo_queries = 0
        self.load_demo()
        self.navigation.setCurrentRow(0)
        self.statusBar().showMessage(
            "Галерея компонентов · нет вызовов ядра, сканов и провайдеров"
        )

    def navigate(self, row):
        if row <= 0:
            self.stack.setCurrentWidget(self.audit)
        else:
            self.stack.setCurrentWidget(self.projects)
            self.projects.select_tab(PROJECT_TABS[row - 1].id)

    def load_demo(self):
        self.audit.set_page(
            "internal",
            ROWS,
            total=len(ROWS),
            source=SOURCE,
            available_filters=("all", "html", "images"),
        )
        for id in ("page_titles", "response_codes", "url"):
            self.audit.set_page(id, ROWS, total=len(ROWS), source=SOURCE)
        self.audit.set_page(
            "overview",
            [
                {
                    "name": "Internal · synthetic fixture",
                    "urls": len(ROWS),
                    "percent": "100%",
                },
                {"name": "HTML", "urls": 7, "percent": "87.5%"},
                {"name": "Images", "urls": 1, "percent": "12.5%"},
                {"name": "Реальный краул", "urls": None, "percent": None},
            ],
            total=4,
            source=SOURCE,
        )
        self.audit.set_page(
            "issues",
            [
                {
                    "title": "Page Titles: Missing",
                    "kind": "Issue",
                    "priority": "Medium",
                    "urls": 1,
                    "percent": None,
                },
                {
                    "title": "Response Codes: Client Error",
                    "kind": "Issue",
                    "priority": "High",
                    "urls": 1,
                    "percent": None,
                },
            ],
            total=2,
            source=SOURCE,
        )
        for id in ("work", "tasks", "remediation"):
            self.projects.set_page(id, TASKS, total=len(TASKS), source=SOURCE)
        for id in ("scans", "reanalyze"):
            self.projects.set_page(id, SCANS, total=len(SCANS), source=SOURCE)
        self.projects.panel("compare").set_scans(SCANS)
        self.projects.set_page(
            "inbox",
            [
                {
                    "id": "demo-note",
                    "kind": "note",
                    "state": "unread",
                    "text": "Проверить доступность вкладок с клавиатуры",
                    "created_at": "2026-10-07",
                },
            ],
            total=1,
            source=SOURCE,
        )
        self.projects.panel("inbox").set_submission_enabled(
            True, "Демо: отправка покажет событие в строке состояния"
        )
        self.projects.set_page(
            "scenarios",
            [
                {
                    "id": "demo-audit",
                    "title": "Аудит сохранённого скана",
                    "scope": "Демо",
                    "state": "preview",
                    "requires": "retained scan",
                    "reason": "Синтетическая карточка метода",
                }
            ],
            total=1,
            source=SOURCE,
        )
        self.projects.set_page(
            "skills",
            [
                {
                    "id": "demo-skill",
                    "title": "Проверка evidence",
                    "scope": "Демо",
                    "version": "fixture",
                    "source": "Synthetic metadata",
                    "reason": "Не запускается",
                }
            ],
            total=1,
            source=SOURCE,
        )
        self.projects.set_page(
            "providers",
            [
                {
                    "provider": "Synthetic provider",
                    "state": "not_configured",
                    "reason": "Нет подключения в демо",
                    "observed_at": None,
                    "scope": None,
                }
            ],
            total=1,
            source=SOURCE,
        )
        self.projects.set_page(
            "logs",
            [
                {
                    "timestamp": "2026-10-07",
                    "level": "info",
                    "operation": "component-demo",
                    "state": "ready",
                    "message": "Запущена локальная галерея компонентов",
                }
            ],
            total=1,
            source=SOURCE,
        )
        self.audit.main.panel("internal").table.selectRow(0)

    def handle_intent(self, intent, payload):
        if intent == "query" and payload["tab_id"] == "internal":
            self.demo_queries += 1
            filter = payload["filter_id"]
            rows = [
                row
                for row in ROWS
                if filter == "all"
                or (filter == "html" and row["type"] == "text/html")
                or (filter == "images" and row["type"].startswith("image/"))
            ]
            self.audit.set_page(
                "internal",
                rows,
                total=len(rows),
                source=SOURCE,
                available_filters=("all", "html", "images"),
            )
        elif intent == "select_url":
            row = payload["row"]
            self.audit.set_page("serp_snippet", [row], total=1, source=SOURCE)
        elif intent == "submit_note":
            self.statusBar().showMessage(
                "Демо: получен submit_note; заметка никуда не отправлена и не записана"
            )
        elif intent == "preview_compare":
            self.statusBar().showMessage(
                "Демо: выбраны два скана; совместимость и сравнение проверяет ядро"
            )
        elif intent != "open_panel":
            self.statusBar().showMessage(
                f"Демо: событие {intent}; источник — {payload.get('tab_id', 'форма')}"
            )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--capture-dir",
        type=Path,
        help="Capture actual Qt widgets at desktop/compact sizes and exit",
    )
    args = parser.parse_args()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    tokens = load_theme(app)
    app.setStyleSheet(app.styleSheet() + component_stylesheet(tokens))
    window = GalleryWindow()
    window.show()
    if args.capture_dir:

        def capture():
            directory = args.capture_dir
            directory.mkdir(parents=True, exist_ok=True)
            captures = []

            def save_capture(widget, filename):
                path = directory / filename
                if not widget.grab().save(str(path)):
                    raise RuntimeError(f"Failed to capture {path}")
                captures.append(
                    {
                        "file": filename,
                        "logical_size": [widget.width(), widget.height()],
                    }
                )

            for width, height, filename in (
                (1440, 900, "audit-1440.png"),
                (1024, 720, "audit-1024.png"),
            ):
                window.resize(width, height)
                app.processEvents()
                save_capture(window, filename)
            window.resize(1440, 900)
            window.navigation.setCurrentRow(
                1 + [spec.id for spec in PROJECT_TABS].index("inbox")
            )
            app.processEvents()
            save_capture(window, "inbox-1440.png")
            window.navigation.setCurrentRow(0)
            window.audit.main.select_tab("structured_data")
            app.processEvents()
            save_capture(window, "unavailable-1440.png")
            dialog = TabConfigurationDialog(
                MAIN_TABS,
                ("internal", "page_titles", "images", "structured_data"),
                window,
            )
            dialog.show()
            app.processEvents()
            save_capture(dialog, "configure-tabs.png")
            dialog.reject()
            manifest = {
                "kind": "native-Qt-component-render",
                "demo": True,
                "platform": platform.platform(),
                "pyqt": PYQT_VERSION_STR,
                "qt": QT_VERSION_STR,
                "catalogue_panels": len(ALL_TABS),
                "main_tabs": len(MAIN_TABS),
                "captures": captures,
                "limitations": [
                    "No core integration in gallery",
                    "No new crawl or provider calls",
                    "Cross-platform runtime not verified",
                    "Large-crawl readiness not established",
                ],
            }
            (directory / "capture.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n"
            )
            window.close()
            app.quit()

        QTimer.singleShot(300, capture)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
