"""Agent settings owns its configuration subpage; no permission state is saved locally."""

from PyQt5.QtWidgets import QPushButton

from ... import i18n
from .helpers import page
from .source_details import agent_detail


def attach(body):
    button = QPushButton(i18n.tr("Агент настраивает приложение"), body)
    button.setProperty("role", "text")
    body.layout().addWidget(button)

    def open_config():
        for index in range(body.layout().count()):
            widget = body.layout().itemAt(index).widget()
            if widget:
                widget.hide()
        back = QPushButton(i18n.tr("Агент"))
        back.setProperty("role", "text")
        subpage = page(back, agent_detail())
        body.layout().addWidget(subpage)
        host = body.parentWidget()
        while host is not None and not hasattr(host, "section_title"):
            host = host.parentWidget()
        if host:
            host.section_title.setText(i18n.tr("Агент настраивает приложение"))

        def close_config():
            body.layout().removeWidget(subpage)
            subpage.deleteLater()
            for index in range(body.layout().count()):
                body.layout().itemAt(index).widget().show()
            if host:
                host.section_title.setText(i18n.tr("Агент"))

        back.clicked.connect(close_config)
        i18n.retranslate(subpage)

    button.clicked.connect(open_config)
    return body
