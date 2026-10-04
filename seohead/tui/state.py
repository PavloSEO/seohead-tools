"""Pure navigation state for the interactive shell — no rendering, no I/O.

Kept dependency-free so behavior (filtering, cursor movement, view switches)
is unit-tested without a terminal and so the core boundary stays obvious:
this module knows command *names*, nothing about running them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Views the shell can be in. ``palette`` is the command list, ``detail`` the
#: selected command's read-only page, ``help`` the key reference.
VIEWS = ("palette", "detail", "help", "watch", "note")

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
    watch_section: str = "overview"
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
            if key == "escape":
                self.note_text = ""
                self.view = "watch"
            elif key == "backspace":
                self.note_text = self.note_text[:-1]
            elif key == "enter" and self.note_text.strip():
                self.note_ready = True
                self.view = "watch"
            elif key.startswith("char:") and key[5:].isprintable():
                self.note_text += key[5:]
            return
        if self.view == "watch":
            if key == "char:n":
                self.note_text = ""
                self.note_kind = "note"
                self.view = "note"
            elif key == "char:g":
                self.note_text = ""
                self.note_kind = "proposed_goal"
                self.view = "note"
            elif key in {"char:1", "char:2", "char:3", "char:4", "char:5"}:
                self.watch_section = {
                    "char:1": "overview",
                    "char:2": "tasks",
                    "char:3": "methods",
                    "char:4": "scans",
                    "char:5": "log",
                }[key]
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
