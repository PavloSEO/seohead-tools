"""Keyboard-native profile menu shared by the wide navigation and avatar rail."""

from PyQt5.QtGui import QKeySequence
from PyQt5.QtWidgets import QActionGroup, QLabel, QMenu, QVBoxLayout, QWidget

from .. import i18n, theming
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
    metrics = theming.metrics()["sources"]
    layout.setContentsMargins(metrics["popup_padding_x"], metrics["popup_padding_y"], metrics["popup_padding_x"], metrics["popup_padding_y"])
    layout.setSpacing(theming.metrics()["spacing"][0])
    for text, style in (("Павел", "control"), (MODE_NAMES.get(window.display, "С агентом"), "meta")):
        label = QLabel(text)
        label.setProperty("text_style", style)
        layout.addWidget(label)
    window._menu_widget(menu, header)
    menu.addSeparator()
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
    action = menu.addAction(icon("settings"), "Настройки")
    action.setShortcut(QKeySequence.Preferences)
    action.triggered.connect(lambda: window.open_settings())
    window._theme_menu(menu)
    window._language_menu(menu)
    mcp = menu.addMenu(icon("hub"), tr("MCP-сервер · нет данных"))
    state = mcp.addAction(trf("ждёт #{issue}", issue=929))
    state.setEnabled(False)
    mcp.addAction(icon("settings"), "Настройки", lambda: window.open_settings("mcp"))
    menu.addSeparator()
    cli = menu.addAction(icon("terminal"), "Командная строка")
    cli.setEnabled(False)
    cli.setToolTip("Недоступно в этой сборке")
    menu.addAction(icon("help"), "Справка", window.show_help, "F1")
    menu.setFixedWidth(theming.metrics()["layout"]["profile_popup"])
    i18n.retranslate(menu)
    return menu
