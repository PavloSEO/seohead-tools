"""Main-window methods: inbox."""

from __future__ import annotations

from pathlib import Path

from .common import (  # noqa: F401
    CONSUMER_ID,
    PAGE_LIMIT,
    ROOT,
    configure_table,
    plain,
    scan_request_key,
)
from .ui.presentation import (
    readable_record,
)


class InboxMixin:
    def load_inbox(self, result):
        self.inbox_revision = result.get("revision")
        rows = list(result.get("entries") or [])
        self.inbox_model.replace(rows)
        total = (result.get("pagination") or {}).get("total", len(rows))
        self.inbox_caption.setText(f"Входящие · {total} сохранённых записей")
        self.project_panels.set_page(
            "inbox", rows, total=total, source="Сохранённые входящие проекта"
        )
        self.update_note_controls()
        if rows:
            self.inbox_table.selectRow(0)
        else:
            self.inbox_detail.setPlainText("В этом проекте пока нет сохранённых заметок")

    def load_unread(self, result):
        count = result.get("count")
        if type(count) is not int:
            self.source_badge.setText("Локальный проект · непрочитанные не измерены")
            return
        label = "сообщений" if count != 1 else "сообщение"
        self.source_badge.setText(f"Локальный проект · {count} непрочит. {label}")

    def show_inbox_entry(self, current, _previous):
        if not current.isValid():
            return
        self.inbox_detail.setPlainText(readable_record(self.inbox_model.rows[current.row()], heading="Запись входящих"))

    def note_project_key(self):
        if not self.project_directory:
            return None
        return (self.current_project_uuid or "", str(Path(self.project_directory).resolve()))

    def stash_note_drafts(self):
        key = self.note_project_key()
        if key is None or not hasattr(self, "note_input"):
            return
        panel = self.project_panels.panel("inbox")
        self._note_drafts[key] = {"main": (self.note_input.text(), self.note_kind.currentData()), "project": (panel.note.toPlainText(), panel.kind.currentData())}

    def restore_note_drafts(self):
        draft = self._note_drafts.get(self.note_project_key(), {})
        panel = self.project_panels.panel("inbox")
        for name, edit, combo in (("main", self.note_input, self.note_kind), ("project", panel.note, panel.kind)):
            text, kind = draft.get(name, ("", "note"))
            edit.blockSignals(True)
            (edit.setText if name == "main" else edit.setPlainText)(text)
            edit.blockSignals(False)
            combo.setCurrentIndex(max(0, combo.findData(kind)))
        self.update_note_controls()

    def update_note_controls(self):
        if not hasattr(self, "note_submit"):
            return
        panel = self.project_panels.panel("inbox")
        ready = bool(self.project_directory) and type(self.inbox_revision) is int and not self._project_loading and self._pending_note is None
        length = len(self.note_input.text().strip())
        self.note_submit.setEnabled(ready and 0 < length <= 4000 and self.note_kind.currentData() in {"note", "proposed_goal"})
        destination = f"Проект: {self.project_picker.currentText()} · {self.project_directory}" if self.project_directory else "Откройте проект"
        reason = "Сохранение…" if self._pending_note is not None else "Ожидание состояния входящих" if not ready else "Черновик относится только к этому проекту"
        if hasattr(self, "note_destination"):
            self.note_destination.setText(destination + " · " + reason)
        panel.set_submission_enabled(ready, destination + " · " + reason)

    def submit_note(self, supplied_text=None, supplied_kind=None):
        if not self.project_directory or self._project_loading or self._pending_note is not None:
            return
        if type(self.inbox_revision) is not int:
            self.notice.show_error("Состояние входящих ещё не получено. Обновите проект перед сохранением; черновик остаётся в форме.")
            return
        if isinstance(supplied_text, bool):
            supplied_text = None
        source = "project" if supplied_text is not None else "main"
        text = (supplied_text if supplied_text is not None else self.note_input.text()).strip()
        kind = supplied_kind or self.note_kind.currentData()
        if not 0 < len(text) <= 4000:
            self.notice.show_error("Введите заметку до 4 000 символов. Текст остаётся в форме.")
            return
        if kind not in {"note", "proposed_goal"}:
            self.notice.show_error("Выбранный тип сообщения не поддержан ядром. Выберите заметку или предложенную цель; текст не изменён.")
            return
        pending = {"key": self.note_project_key(), "source": source, "text": text, "kind": kind}
        self._pending_note = pending
        self.update_note_controls()
        self.start_command("inbox-submit", "seo_project_inbox_submit", {"directory": self.project_directory, "text": text, "kind": kind, "author_role": "specialist", "expected_revision": self.inbox_revision}, lambda result: self.note_saved(result, pending))
        if self._pending_note is pending and "inbox-submit" not in self.requests:
            self._pending_note = None
            self.update_note_controls()

    def note_saved(self, _result, pending=None):
        pending = pending or self._pending_note
        if not pending:
            return
        if self._pending_note is pending:
            self._pending_note = None
        if self.notice.context == "inbox-submit":
            self.notice.hide()
        if pending["key"] != self.note_project_key():
            saved = self._note_drafts.get(pending["key"], {})
            if saved.get(pending["source"]) == (pending["text"], pending["kind"]):
                saved[pending["source"]] = ("", pending["kind"])
            return
        panel = self.project_panels.panel("inbox")
        edit, combo = (panel.note, panel.kind) if pending["source"] == "project" else (self.note_input, self.note_kind)
        current = edit.toPlainText() if pending["source"] == "project" else edit.text()
        if current.strip() == pending["text"] and combo.currentData() == pending["kind"]:
            edit.clear()
        self.stash_note_drafts()
        self.update_note_controls()
        self.statusBar().showMessage("Заметка сохранена в выбранном проекте; остальные черновики сохранены")
        self.refresh_project()
