"""Native Qt Widgets preparation shell with explicit demo and adapter boundaries."""

import argparse
import json
import shutil
import sys
from pathlib import Path
from string import Template

from PyQt5.QtCore import (
    QSettings,
    QSortFilterProxyModel,
    Qt,
    QThreadPool,
    QTimer,
)
from PyQt5.QtGui import QFontDatabase, QIcon, QPainter, QPixmap
from PyQt5.QtSvg import QSvgGenerator, QSvgRenderer
from PyQt5.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMainWindow,
    QPlainTextEdit,
    QPushButton,
    QSpinBox,
    QSplitter,
    QStackedWidget,
    QTableView,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from .gateway import ProjectRead
from .models import UrlModel

ROOT = Path(__file__).resolve().parent


def load_theme(app):
    tokens = json.loads((ROOT / "theme/tokens.json").read_text())
    font = ROOT / "assets/fonts/Roboto.ttf"
    if font.exists():
        QFontDatabase.addApplicationFont(str(font))
    values = {**tokens["colors"], "font_family": tokens["font_family"]}
    app.setStyleSheet(
        Template((ROOT / "theme/theme.qss").read_text()).substitute(values)
    )
    return tokens


def icon(name, color="#49454F"):
    path = ROOT / "assets/icons" / f"{name}.svg"
    if not path.exists():
        return QIcon()
    raw = path.read_text().replace("<svg ", f'<svg fill="{color}" ', 1)
    renderer = QSvgRenderer(raw.encode())
    pixmap = QPixmap(48, 48)
    pixmap.fill(Qt.transparent)
    painter = QPainter(pixmap)
    renderer.render(painter)
    painter.end()
    pixmap.setDevicePixelRatio(2)
    return QIcon(pixmap)


def plain(text):
    view = QPlainTextEdit(text)
    view.setReadOnly(True)
    return view


class MainWindow(QMainWindow):
    def __init__(self, *, persistent=True, core_executable=None):
        super().__init__()
        self.setWindowTitle("SEOHEAD Desktop — подготовительный каркас")
        self.resize(1440, 900)
        self.setMinimumSize(960, 640)
        self.persistent = persistent
        self.settings = (
            QSettings("SEOHEAD", "DesktopPreparation") if persistent else None
        )
        self.core_executable = core_executable or shutil.which("seohead")
        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)
        self.read_generation = 0
        self.demo = json.loads((ROOT / "fixtures/demo.json").read_text())
        workspace = QWidget()
        workspace.setObjectName("workspace")
        self.setCentralWidget(workspace)
        shell = QVBoxLayout(workspace)
        shell.setContentsMargins(0, 0, 0, 0)
        shell.setSpacing(0)
        top = QWidget()
        top.setObjectName("topbar")
        topbar = QHBoxLayout(top)
        topbar.setContentsMargins(16, 10, 16, 10)
        self.nav_toggle = QPushButton()
        self.nav_toggle.setIcon(icon("menu"))
        self.nav_toggle.setAccessibleName("Скрыть или показать навигацию")
        self.nav_toggle.clicked.connect(self.toggle_navigation)
        topbar.addWidget(self.nav_toggle)
        brand = QLabel("SEOHEAD")
        brand.setObjectName("brand")
        topbar.addWidget(brand)
        self.project_picker = QComboBox()
        self.project_picker.addItem(self.demo["label"])
        self.project_picker.setMinimumWidth(180)
        topbar.addWidget(self.project_picker)
        open_project = QPushButton("Открыть проект")
        open_project.setIcon(icon("folder_open"))
        open_project.clicked.connect(self.choose_project)
        topbar.addWidget(open_project)
        topbar.addStretch()
        self.source_badge = QLabel("Демо · синтетические данные")
        self.source_badge.setObjectName("sourceBadge")
        topbar.addWidget(self.source_badge)
        self.new_scan = QPushButton("Новый скан")
        self.new_scan.setProperty("role", "primary")
        self.new_scan.setIcon(icon("play_arrow", "#FFFFFF"))
        self.new_scan.clicked.connect(self.scan_preview)
        topbar.addWidget(self.new_scan)
        shell.addWidget(top)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(0)
        self.navigation = QListWidget()
        self.navigation.setObjectName("navigation")
        self.navigation.setFixedWidth(192)
        self.navigation.addItems(
            ["Работа", "URL", "Проблемы", "Методы", "Входящие", "Отчёты", "Журнал"]
        )
        body.addWidget(self.navigation)
        self.pages = QStackedWidget()
        body.addWidget(self.pages, 1)
        shell.addLayout(body, 1)
        self.pages.addWidget(self.work_page())
        self.pages.addWidget(self.url_page())
        for title in ["Проблемы", "Методы", "Входящие", "Отчёты", "Журнал"]:
            page = QWidget()
            layout = QVBoxLayout(page)
            layout.setContentsMargins(20, 16, 20, 16)
            label = QLabel(title)
            label.setStyleSheet("font-size:20px;font-weight:500")
            layout.addWidget(label)
            layout.addWidget(
                plain(
                    f"{title}: подготовлен контейнер интерфейса.\n\nРеальная проекция ядра ещё не подключена.\nДемо не запускает сканы, не меняет задачи и не отправляет сообщения агенту."
                )
            )
            self.pages.addWidget(page)
        self.navigation.currentRowChanged.connect(self.pages.setCurrentIndex)
        self.navigation.setCurrentRow(1)
        self.statusBar().showMessage(
            "Подготовительный каркас · UI не является завершённым SEO-приложением"
        )
        view_menu = self.menuBar().addMenu("Вид")
        for name, widget in [
            ("Навигация", self.navigation),
            ("Сводка", self.overview),
            ("Инспектор URL", self.inspector),
        ]:
            action = view_menu.addAction(name)
            action.setCheckable(True)
            action.setChecked(True)
            action.toggled.connect(widget.setVisible)
        restore = view_menu.addAction("Восстановить панели")
        restore.triggered.connect(self.restore_panels)
        if self.settings:
            for key, widget in [
                ("geometry", self),
                ("horizontal", self.horizontal),
                ("vertical", self.vertical),
            ]:
                value = self.settings.value(key)
                if value:
                    (widget.restoreGeometry if widget is self else widget.restoreState)(
                        value
                    )
        self.table.selectRow(0)

    def work_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.addWidget(QLabel("Работа с агентом · демонстрация структуры"))
        layout.addWidget(
            plain(
                "Цель: подготовить проверяемый аудит и задачи разработчикам.\n\nПланирование → конфигурация → сбор → анализ → сравнение → задачи → перепроверка → отчёт.\n\nКаркас готов к подключению project-progress / project-observe / inbox через общий adapter.\nЗавершённый workflow не равен исправленному сайту.\nНеизмеренные данные не заменяются нулями."
            )
        )
        return page

    def url_page(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(16, 12, 16, 12)
        self.horizontal = QSplitter(Qt.Horizontal)
        self.vertical = QSplitter(Qt.Vertical)
        table_area = QWidget()
        area = QVBoxLayout(table_area)
        area.setContentsMargins(0, 0, 0, 0)
        toolbar = QHBoxLayout()
        self.url_caption = QLabel("URL · 5 демо-записей")
        toolbar.addWidget(self.url_caption)
        toolbar.addStretch()
        self.search = QLineEdit()
        self.search.setPlaceholderText("Поиск URL в демо-наборе")
        self.search.setClearButtonEnabled(True)
        self.search.setMinimumWidth(240)
        toolbar.addWidget(self.search)
        settings = QPushButton("Панели")
        settings.setIcon(icon("view_sidebar"))
        settings.clicked.connect(
            lambda: self.overview.setVisible(not self.overview.isVisible())
        )
        toolbar.addWidget(settings)
        area.addLayout(toolbar)
        self.model = UrlModel(self.demo["rows"], self)
        self.proxy = QSortFilterProxyModel(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setFilterKeyColumn(0)
        self.proxy.setFilterCaseSensitivity(Qt.CaseInsensitive)
        self.search.textChanged.connect(self.proxy.setFilterFixedString)
        self.table = QTableView()
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(30)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 70)
        self.table.setColumnWidth(2, 75)
        self.table.setColumnWidth(3, 115)
        self.table.setColumnWidth(4, 190)
        self.table.setColumnWidth(5, 85)
        self.table.selectionModel().currentRowChanged.connect(self.show_url)
        area.addWidget(self.table)
        self.vertical.addWidget(table_area)
        self.inspector = QTabWidget()
        self.detail = plain("")
        self.inspector.addTab(self.detail, "Сведения")
        self.debug_detail = plain("")
        self.inspector.addTab(self.debug_detail, "Диагностика")
        for title in ["Ссылки", "HTTP headers", "HTML", "Снимки", "Извлечение"]:
            self.inspector.addTab(
                plain(
                    "Не подключено в каркасе.\nРеальная информация должна поступать только из сохранённых измерений ядра."
                ),
                title,
            )
        self.vertical.addWidget(self.inspector)
        self.vertical.setSizes([480, 230])
        self.horizontal.addWidget(self.vertical)
        self.overview = QWidget()
        summary = QVBoxLayout(self.overview)
        summary.setContentsMargins(16, 8, 4, 8)
        box = QGroupBox("Сводка · демо")
        facts = QFormLayout(box)
        for label, value in [
            ("Источник", "Synthetic fixture"),
            ("Записей", "5"),
            ("Реальных запросов", "0"),
            ("Краул", "Не запускался"),
            ("Sitemap", "Не измерен"),
            ("Покрытие", "Нет измерений"),
        ]:
            facts.addRow(label, QLabel(value))
        summary.addWidget(box)
        summary.addWidget(
            plain(
                "Доступно в каркасе:\n\nПоиск и выбор URL\nСортировка демо-таблицы\nСкрытие и восстановление панелей\nВкладки сведений\n\nРеальные сканы, задачи и сообщения агенту ещё не подключены."
            )
        )
        self.horizontal.addWidget(self.overview)
        self.horizontal.setSizes([1000, 280])
        layout.addWidget(self.horizontal)
        return page

    def show_url(self, current, previous):
        source = self.proxy.mapToSource(current)
        if not source.isValid():
            self.detail.setPlainText("Выберите сохранённую запись")
            return
        row = self.model.rows[source.row()]
        self.detail.setPlainText(
            "Демо · синтетическая запись\n\n"
            f"Адрес: {row['url']}\nHTTP: {row.get('status', 'Не измерено')}\n"
            f"Тип: {row.get('type', 'Не измерено')}\nИндексация: {row.get('indexability', 'Не измерено')}\n"
            f"Title: {row.get('title') or 'Не измерено'}\nПроблемы: {row.get('issues', 'Не измерено')}\n\n"
            "Это демонстрационные данные. Headers, HTML и cookies не измерялись."
        )
        self.debug_detail.setPlainText(json.dumps(row, ensure_ascii=False, indent=2))

    def toggle_navigation(self):
        self.navigation.setVisible(not self.navigation.isVisible())

    def restore_panels(self):
        for widget in [self.navigation, self.overview, self.inspector]:
            widget.show()
        self.horizontal.setSizes([1000, 280])
        self.vertical.setSizes([480, 230])

    def scan_preview(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Новый скан · только preview каркаса")
        dialog.resize(500, 320)
        layout = QVBoxLayout(dialog)
        form = QFormLayout()
        form.addRow("URL", QLineEdit("https://shop.example.test/"))
        mode = QComboBox()
        mode.addItems(["Native HTML", "Native JavaScript", "Screaming Frog"])
        form.addRow("Источник", mode)
        limit = QSpinBox()
        limit.setRange(1, 50000)
        limit.setValue(5000)
        form.addRow("Лимит URL", limit)
        layout.addLayout(form)
        message = QLabel(
            "Каркас показывает будущий preview. Backend submission ещё не подключён: реальный скан не будет запущен."
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        buttons = QDialogButtonBox(QDialogButtonBox.Close)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        dialog.exec_()

    def choose_project(self):
        directory = QFileDialog.getExistingDirectory(
            self, "Открыть существующий проект SEOHEAD"
        )
        if directory:
            self.read_project(directory)

    def read_project(self, directory):
        if not self.core_executable:
            self.statusBar().showMessage(
                "Укажите --core-cli: CLI ядра SEOHEAD не найден"
            )
            return
        self.read_generation += 1
        generation = self.read_generation
        reader = ProjectRead(self.core_executable, directory)
        reader.signals.loaded.connect(
            lambda result: self.project_loaded(result, generation)
        )
        reader.signals.failed.connect(
            lambda text: (
                self.statusBar().showMessage(text)
                if generation == self.read_generation
                else None
            )
        )
        self.statusBar().showMessage("Чтение проекта в фоне…")
        self.pool.start(reader)

    def project_loaded(self, result, generation):
        if generation != self.read_generation:
            return
        self.model.replace([])
        self.url_caption.setText("URL · реальная проекция ещё не подключена")
        self.search.setPlaceholderText("URL-адаптер не подключён")
        self.search.setEnabled(False)
        self.source_badge.setText("Реальный проект · только metadata")
        self.project_picker.clear()
        self.project_picker.addItem("Подключённый проект")
        self.detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.debug_detail.setPlainText(json.dumps(result, ensure_ascii=False, indent=2))
        self.overview.hide()
        self.statusBar().showMessage(
            "Прочитан existing CLI project-open. URL query adapter ещё не подключён; демо-строки очищены."
        )

    def closeEvent(self, event):
        if self.settings:
            self.settings.setValue("geometry", self.saveGeometry())
            self.settings.setValue("horizontal", self.horizontal.saveState())
            self.settings.setValue("vertical", self.vertical.saveState())
        super().closeEvent(event)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--core-cli", help="Existing seohead CLI executable; read-only project opening"
    )
    parser.add_argument(
        "--capture",
        type=Path,
        help="Save the native widget rendering to a PNG and exit",
    )
    parser.add_argument(
        "--export-svg",
        type=Path,
        help="Export actual Qt painting as SVG for Figma import",
    )
    parser.add_argument("--no-settings", action="store_true")
    args = parser.parse_args()
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps)
    app = QApplication(sys.argv[:1])
    app.setStyle("Fusion")
    load_theme(app)
    window = MainWindow(persistent=not args.no_settings, core_executable=args.core_cli)
    window.show()
    if args.capture or args.export_svg:

        def capture():
            if args.capture:
                args.capture.parent.mkdir(parents=True, exist_ok=True)
                window.grab().save(str(args.capture))
            if args.export_svg:
                args.export_svg.parent.mkdir(parents=True, exist_ok=True)
                generator = QSvgGenerator()
                generator.setFileName(str(args.export_svg))
                generator.setSize(window.size())
                generator.setViewBox(window.rect())
                generator.setTitle("SEOHEAD native Qt preparation skeleton — demo")
                painter = QPainter(generator)
                window.render(painter)
                painter.end()
            window.close()
            app.quit()

        QTimer.singleShot(500, capture)
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
