"""Keyboard-native profile menu shared by the wide navigation and avatar rail."""

from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QActionGroup, QLabel, QMenu, QVBoxLayout, QWidget

from .. import i18n
from ..i18n import tr, trf
from .icons import material_icon as icon

MODE_NAMES = {"agent": "С агентом", "simple": "Простой", "crawler": "Краулер без проекта"}


def update_profile(window):
    button = window.navigation.profile
    button.name.setText(tr("Павел"))
    button.avatar.setText(tr("ПБ"))
    button.set_mode_line(tr(MODE_NAMES.get(window.display, "С агентом")))
    button.setToolTip(trf("{name} · {mode} · MCP: нет данных · ждёт #929", name="Павел", mode=MODE_NAMES.get(window.display, "С агентом")))


def build_profile_menu(window):
    update_profile(window)
    menu = QMenu(window)
    menu.setProperty("profile_menu", True)
    menu.setAccessibleName(tr("Меню профиля"))
    header = QWidget()
    layout = QVBoxLayout(header)
    layout.setContentsMargins(10, 8, 10, 6)
    for text, style in (("Павел", "control"), (MODE_NAMES.get(window.display, "С агентом"), "meta"),
                        ("локально · этот компьютер", "meta")):
        label = QLabel(text)
        label.setProperty("text_style", style)
        layout.addWidget(label)
    window._menu_widget(menu, header)
    menu.addSeparator()
    menu.addSection(tr("Отображение"))
    group = QActionGroup(menu)
    for mode in ("agent", "simple"):
        action = menu.addAction(MODE_NAMES[mode])
        action.setCheckable(True)
        action.setChecked(window.display == mode)
        group.addAction(action)
        action.triggered.connect(lambda _checked, m=mode: window.set_display(m))
    crawler = menu.addAction(icon("travel_explore"), "Быстрый краул без проекта")
    crawler.setCheckable(True)
    crawler.setChecked(window.display == "crawler")
    crawler.setEnabled(window.can_open_crawler())
    crawler.setToolTip(trf("ждёт #{issue}", issue=942))
    crawler.triggered.connect(lambda: window.open_crawler())
    menu.addSeparator()
    status = menu.addAction(tr("MCP · нет данных · ждёт #929"))
    status.setEnabled(False)
    for title, symbol, section in (("Настройки", "settings", "general"), ("Источники данных", "dns", "sources"),
                                   ("Горячие клавиши", "keyboard", "keys"), ("О программе", "info", "about"),
                                   ("MCP-сервер", "hub", "mcp")):
        action = menu.addAction(icon(symbol), title)
        action.triggered.connect(lambda _checked, s=section: window.open_settings(s))
        if section == "general":
            action.setShortcut(QKeySequence.Preferences)
    window._theme_menu(menu)
    window._language_menu(menu)
    menu.addSeparator()
    cli = menu.addAction(icon("terminal"), "Командная строка")
    cli.setEnabled(False)
    cli.setToolTip("Недоступно в этой сборке")
    menu.addAction(icon("help"), "Справка", window.show_help, "F1")
    menu.setMinimumWidth(264)
    i18n.retranslate(menu)
    return menu
