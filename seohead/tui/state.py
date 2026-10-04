"""Pure navigation state for the interactive shell — no rendering, no I/O.

Kept dependency-free so behavior (filtering, cursor movement, view switches)
is unit-tested without a terminal and so the core boundary stays obvious:
this module knows command *names*, nothing about running them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Views the shell can be in. ``palette`` is the command list, ``detail`` the
#: selected command's read-only page, ``help`` the key reference.
VIEWS = ("palette", "detail", "help", "watch", "note", "watch_filter", "watch_detail")

WATCH_SECTIONS = (
    "overview",
    "tasks",
    "methods",
    "scans",
    "findings",
    "views",
    "activity",
    "log",
)

#: Commands offered next to the flat tool list. Grouped namespaces keep their
#: own subcommands in the CLI; the palette lists them as single entries whose
#: detail view points at ``seohead <group> --help``.
GROUP_COMMANDS = ("scan", "project", "skill", "scenario", "sf", "mcp")


@dataclass
class ShellState:
    """Keyboard-navigable palette over the shared CLI command table."""

    commands: list[str]
    query: str = ""
    index: int = 0
    view: str = "palette"
    #: True once the user asked to leave; the run loop turns it into exit 0.
    quit_requested: bool = False
    note_text: str = ""
    note_ready: bool = False
    note_kind: str = "note"
    note_error: str = ""
    note_limit: int = 8000
    paste_active: bool = False
    watch_section: str = "overview"
    watch_index: int = 0
    watch_offset: int = 0
    watch_query: str = ""
    watch_sort: str = "severity"
    watch_descending: bool = False
    watch_detail_ordinal: int | None = None
    watch_detail_offset: int = 0
    watch_detail_kind: str = "finding"
    watch_selected_scan_uuid: str | None = None
    watch_view_name: str | None = None
    _all: list[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._all = list(self.commands) + [c for c in GROUP_COMMANDS if c not in self.commands]
        self.commands = self._all

    @property
    def filtered(self) -> list[str]:
        """Commands containing the current query, case-insensitive."""
        if not self.query:
            return list(self._all)
        needle = self.query.lower()
        return [c for c in self._all if needle in c]

    @property
    def selected(self) -> str | None:
        items = self.filtered
        if not items:
            return None
        return items[min(self.index, len(items) - 1)]

    def move(self, delta: int) -> None:
        items = self.filtered
        if not items:
            self.index = 0
            return
        self.index = max(0, min(self.index + delta, len(items) - 1))

    def jump(self, index: int) -> None:
        items = self.filtered
        self.index = max(0, min(index, max(len(items) - 1, 0)))

    def type_char(self, char: str) -> None:
        self.query += char
        self.index = 0

    def backspace(self) -> None:
        self.query = self.query[:-1]
        self.index = 0

    def open_selected(self) -> None:
        if self.selected is not None:
            self.view = "detail"

    def back(self) -> bool:
        """Esc handling: clear a filter first, leave a sub-view, else quit."""
        if self.view != "palette":
            self.view = "palette"
            return True
        if self.query:
            self.query = ""
            self.index = 0
            return True
        return False

    def handle_key(self, key: str) -> None:
        """Apply one symbolic key name (see :mod:`seohead.tui.keys`)."""
        if key == "timeout":
            return
        if self.view == "note":
            if key == "paste_start":
                self.paste_active = True
            elif key == "paste_end":
                self.paste_active = False
            elif key == "enter" and self.paste_active:
                if len(self.note_text) <= self.note_limit:
                    self.note_text += "\n"
            elif key == "escape":
                self.note_text = ""
                self.note_error = ""
                self.paste_active = False
                self.view = "watch"
            elif key == "backspace":
                self.note_text = self.note_text[:-1]
                self.note_error = ""
            elif key == "enter" and self.note_text.strip():
                if len(self.note_text) > self.note_limit:
                    self.note_error = (
                        f"Draft exceeds {self.note_limit:,} characters; shorten it before saving."
                    )
                else:
                    self.note_ready = True
                    self.view = "watch"
            elif key.startswith("char:") and key[5:].isprintable():
                if len(self.note_text) <= self.note_limit:
                    self.note_text += key[5:]
                else:
                    self.note_error = (
                        f"Draft limit is {self.note_limit:,}; extra input was not added."
                    )
            return
        if self.view == "watch_filter":
            if key == "escape":
                self.view = "watch"
            elif key == "backspace":
                self.watch_query = self.watch_query[:-1]
            elif key == "enter":
                self.watch_offset = 0
                self.watch_index = 0
                self.view = "watch"
            elif key.startswith("char:") and key[5:].isprintable() and len(self.watch_query) < 256:
                self.watch_query += key[5:]
            return
        if self.view == "watch_detail":
            if key in {"down", "page_down"}:
                self.watch_detail_offset += 1 if key == "down" else 10
            elif key in {"up", "page_up"}:
                self.watch_detail_offset = max(
                    0, self.watch_detail_offset - (1 if key == "up" else 10)
                )
            if key in {"escape", "enter", "ctrl_c"}:
                self.view = "watch"
                self.quit_requested = key == "ctrl_c"
            return
        if self.view == "watch":
            if key == "char:n":
                self.note_text = ""
                self.note_error = ""
                self.note_kind = "note"
                self.view = "note"
            elif key == "char:g":
                self.note_text = ""
                self.note_error = ""
                self.note_kind = "proposed_goal"
                self.view = "note"
            elif key in {
                "char:1",
                "char:2",
                "char:3",
                "char:4",
                "char:5",
                "char:6",
                "char:7",
                "char:8",
            }:
                self.watch_section = {
                    "char:1": "overview",
                    "char:2": "tasks",
                    "char:3": "methods",
                    "char:4": "scans",
                    "char:5": "findings",
                    "char:6": "views",
                    "char:7": "activity",
                    "char:8": "log",
                }[key]
                self.watch_index = 0
                self.watch_offset = 0
                self.watch_detail_ordinal = None
                self.watch_detail_kind = "finding"
            elif key == "up":
                self.watch_index = max(0, self.watch_index - 1)
            elif key == "down":
                self.watch_index += 1
            elif key == "page_up":
                self.watch_offset = max(0, self.watch_offset - 50)
                self.watch_index = 0
            elif key == "page_down":
                self.watch_offset += 50
                self.watch_index = 0
            elif key == "char:f" and self.watch_section == "findings":
                self.view = "watch_filter"
            elif key == "char:c" and self.watch_section == "findings":
                self.watch_query = ""
                self.watch_offset = 0
                self.watch_index = 0
            elif key == "char:s" and self.watch_section == "findings":
                choices = ("severity", "check", "target_url", "id")
                self.watch_sort = choices[(choices.index(self.watch_sort) + 1) % len(choices)]
                self.watch_offset = 0
                self.watch_index = 0
            elif key == "char:r" and self.watch_section == "findings":
                self.watch_descending = not self.watch_descending
                self.watch_offset = 0
                self.watch_index = 0
            elif key == "enter" and self.watch_section in {"findings", "scans", "views"}:
                self.watch_detail_offset = 0
                self.watch_detail_kind = {
                    "findings": "finding",
                    "scans": "scan",
                    "views": "view",
                }[self.watch_section]
                self.view = "watch_detail"
            elif key in ("escape", "char:q", "ctrl_c"):
                self.quit_requested = True
            return
        if self.view in ("detail", "help"):
            if key in ("escape", "enter", "ctrl_c"):
                self.view = "palette"
                self.quit_requested = key == "ctrl_c"
            return
        if key == "up":
            self.move(-1)
        elif key == "down":
            self.move(1)
        elif key == "page_up":
            self.move(-10)
        elif key == "page_down":
            self.move(10)
        elif key == "home":
            self.jump(0)
        elif key == "end":
            self.jump(len(self.filtered) - 1)
        elif key == "enter":
            self.open_selected()
        elif key == "escape":
            self.quit_requested = not self.back()
        elif key == "backspace":
            self.backspace()
        elif key == "ctrl_c":
            self.quit_requested = True
        elif key == "char:?":
            self.view = "help"
        elif key == "char:q" and not self.query:
            self.quit_requested = True
        elif key.startswith("char:") and key[5:].isprintable():
            self.type_char(key[5:])
