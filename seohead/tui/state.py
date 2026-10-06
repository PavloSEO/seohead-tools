"""Pure navigation state for the interactive shell — no rendering, no I/O.

Kept dependency-free so behavior (filtering, cursor movement, view switches)
is unit-tested without a terminal and so the core boundary stays obvious:
this module knows command *names*, nothing about running them.
"""

from __future__ import annotations

from dataclasses import dataclass, field

#: Views the shell can be in. ``palette`` is the command list, ``detail`` the
#: selected command's read-only page, ``help`` the key reference.
VIEWS = ("palette", "detail", "help", "watch", "note", "watch_filter", "watch_detail", "watch_help")

WATCH_SECTIONS = (
    "overview",
    "tasks",
    "methods",
    "scans",
    "findings",
    "views",
    "activity",
    "log",
    "sf",
    "inbox",
    "schema",
    "sites",
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
    note_return_view: str = "watch"
    note_error: str = ""
    note_limit: int = 8000
    note_cursor: int | None = None
    note_drafts: dict[str, str] = field(default_factory=dict)
    paste_active: bool = False
    motion_enabled: bool = True
    watch_section: str = "overview"
    watch_index: int = 0
    watch_offset: int = 0
    watch_query: str = ""
    watch_status: str | None = None
    filter_draft: str = ""
    watch_sort: str = "severity"
    watch_descending: bool = False
    watch_detail_ordinal: int | None = None
    watch_detail_offset: int = 0
    watch_detail_kind: str = "finding"
    watch_selected_scan_uuid: str | None = None
    watch_site_uuid: str | None = None
    watch_site_detail_uuid: str | None = None
    watch_site_openable: bool = False
    watch_view_name: str | None = None
    watch_view_offset: int = 0
    watch_view_next: int | None = None
    watch_view_page_size: int = 50
    watch_inbox_entry_id: str | None = None
    watch_item_id: str | None = None
    watch_count: int | None = None
    watch_next_offset: int | None = None
    watch_previous_offset: int | None = None
    watch_page_size: int = 50
    watch_page_identity: tuple | None = None
    watch_selected_row_id: str | None = None
    watch_return_view: str = "watch"
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
            self.watch_detail_offset = 0
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
        if key == "ctrl_d":
            self.quit_requested = True
            return
        if key == "ctrl_c":
            self.quit_requested = True
            return
        if self.view == "watch_help":
            if key in {"escape", "enter", "char:?"}:
                self.view = self.watch_return_view
            return
        if self.view in {"watch", "watch_detail"} and key in {"char:n", "char:g"}:
            self.note_return_view = self.view
            self.note_drafts[self.note_kind] = self.note_text
            self.note_kind = "note" if key == "char:n" else "proposed_goal"
            self.note_text = self.note_drafts.get(self.note_kind, "")
            self.note_cursor = len(self.note_text)
            self.note_error = ""
            self.view = "note"
            return
        if self.view == "note":
            cursor = len(self.note_text) if self.note_cursor is None else self.note_cursor
            if key == "paste_start":
                self.paste_active = True
            elif key == "paste_end":
                self.paste_active = False
            elif key == "enter" and self.paste_active:
                self.note_text = self.note_text[:cursor] + "\n" + self.note_text[cursor:]
                self.note_cursor = cursor + 1
            elif key == "escape":
                self.note_drafts[self.note_kind] = self.note_text
                self.paste_active = False
                self.view = self.note_return_view
            elif key == "backspace":
                self.note_text = self.note_text[: max(0, cursor - 1)] + self.note_text[cursor:]
                self.note_cursor = max(0, cursor - 1)
                self.note_error = ""
            elif key == "delete":
                self.note_text = self.note_text[:cursor] + self.note_text[cursor + 1 :]
            elif key == "left":
                self.note_cursor = max(0, cursor - 1)
            elif key == "right":
                self.note_cursor = min(len(self.note_text), cursor + 1)
            elif key in {"up", "down"}:
                line_start = self.note_text.rfind("\n", 0, cursor) + 1
                column = cursor - line_start
                if key == "up" and line_start:
                    previous = self.note_text.rfind("\n", 0, line_start - 1) + 1
                    self.note_cursor = min(previous + column, line_start - 1)
                elif key == "down":
                    next_line = self.note_text.find("\n", cursor)
                    if next_line >= 0:
                        end = self.note_text.find("\n", next_line + 1)
                        self.note_cursor = min(
                            next_line + 1 + column, end if end >= 0 else len(self.note_text)
                        )
            elif key == "home":
                self.note_cursor = 0
            elif key == "end":
                self.note_cursor = len(self.note_text)
            elif key == "ctrl_x":
                self.note_text = ""
                self.note_drafts.pop(self.note_kind, None)
                self.note_cursor = 0
                self.note_error = "Draft discarded."
            elif key == "enter" and self.note_text.strip():
                if len(self.note_text) > self.note_limit:
                    self.note_error = (
                        f"Draft exceeds {self.note_limit:,} characters; shorten it before saving."
                    )
                else:
                    self.note_ready = True
                    self.view = self.note_return_view
            elif key.startswith("char:") and key[5:].isprintable():
                self.note_text = self.note_text[:cursor] + key[5:] + self.note_text[cursor:]
                self.note_cursor = cursor + len(key[5:])
                if len(self.note_text) > self.note_limit:
                    self.note_error = f"Draft kept in full; shorten to {self.note_limit:,} characters before saving."
            return
        if self.view == "watch_filter":
            if key == "escape":
                self.view = "watch"
            elif key == "backspace":
                self.filter_draft = self.filter_draft[:-1]
            elif key == "enter":
                self.watch_query = self.filter_draft
                self.watch_offset = 0
                self.watch_index = 0
                self.view = "watch"
            elif key.startswith("char:") and key[5:].isprintable() and len(self.filter_draft) < 256:
                self.filter_draft += key[5:]
            return
        if self.view == "watch_detail":
            if self.watch_detail_kind == "site" and key == "char:s" and self.watch_site_openable:
                self.watch_site_uuid = self.watch_site_detail_uuid
                self.watch_selected_scan_uuid = None
                self.watch_section = "scans"
                self.watch_index = 0
                self.watch_offset = 0
                self.watch_count = None
                self.view = "watch"
                return
            if self.watch_detail_kind == "view" and key in {"char:[", "char:]"}:
                if key == "char:[":
                    self.watch_view_offset = max(
                        0, self.watch_view_offset - self.watch_view_page_size
                    )
                elif self.watch_view_next is not None:
                    self.watch_view_offset = self.watch_view_next
                self.watch_detail_offset = 0
                return
            if key in {"down", "page_down"}:
                self.watch_detail_offset += 1 if key == "down" else 10
            elif key in {"up", "page_up"}:
                self.watch_detail_offset = max(
                    0, self.watch_detail_offset - (1 if key == "up" else 10)
                )
            elif key == "home":
                self.watch_detail_offset = 0
            elif key == "end":
                self.watch_detail_offset = 10**9
            if key in {"escape", "enter", "ctrl_c"}:
                self.view = "watch"
                self.quit_requested = key == "ctrl_c"
            return
        if self.view == "watch":
            if key == "char:m":
                self.motion_enabled = not self.motion_enabled
            elif key == "char:?":
                self.watch_return_view = self.view
                self.view = "watch_help"
            elif key in {
                "char:1",
                "char:2",
                "char:3",
                "char:4",
                "char:5",
                "char:6",
                "char:7",
                "char:8",
                "char:9",
                "char:0",
                "char:a",
                "char:p",
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
                    "char:9": "sf",
                    "char:0": "inbox",
                    "char:a": "schema",
                    "char:p": "sites",
                }[key]
                self.watch_index = 0
                self.watch_offset = 0
                self.watch_detail_ordinal = None
                self.watch_detail_kind = "finding"
                self.watch_query = ""
                self.watch_status = None
                self.watch_count = None
                self.watch_item_id = None
            elif key == "up":
                self.watch_index = max(0, self.watch_index - 1)
            elif key == "down":
                self.watch_index = min(
                    self.watch_index + 1,
                    max(0, self.watch_count - 1)
                    if self.watch_count is not None
                    else self.watch_index + 1,
                )
            elif key == "home":
                self.watch_index = 0
            elif key == "end":
                self.watch_index = max(0, (self.watch_count or 1) - 1)
            elif key == "page_up":
                self.watch_offset = (
                    self.watch_previous_offset
                    if self.watch_count is not None
                    else max(0, self.watch_offset - self.watch_page_size)
                )
                self.watch_offset = self.watch_offset or 0
                self.watch_index = 0
            elif key == "page_down":
                if self.watch_count is None or self.watch_next_offset is not None:
                    self.watch_offset = (
                        self.watch_next_offset
                        if self.watch_next_offset is not None
                        else self.watch_offset + self.watch_page_size
                    )
                    self.watch_index = 0
            elif key == "char:f" and self.watch_section in {
                "findings",
                "tasks",
                "methods",
                "schema",
            }:
                self.filter_draft = self.watch_query
                self.view = "watch_filter"
            elif key == "char:c" and self.watch_section in {
                "findings",
                "tasks",
                "methods",
                "schema",
            }:
                self.watch_query = ""
                self.watch_offset = 0
                self.watch_index = 0
            elif key == "char:s" and self.watch_section == "findings":
                choices = ("severity", "check", "target_url", "id")
                self.watch_sort = choices[(choices.index(self.watch_sort) + 1) % len(choices)]
                self.watch_offset = 0
                self.watch_index = 0
            elif key == "char:t" and self.watch_section in {"tasks", "methods", "schema"}:
                choices = (None, "running", "blocked", "review", "remaining", "stale", "completed")
                self.watch_status = choices[(choices.index(self.watch_status) + 1) % len(choices)]
                self.watch_offset = 0
                self.watch_index = 0
            elif key == "char:r" and self.watch_section == "findings":
                self.watch_descending = not self.watch_descending
                self.watch_offset = 0
                self.watch_index = 0
            elif key == "enter" and self.watch_section != "overview":
                self.watch_detail_offset = 0
                self.watch_view_offset = 0
                self.watch_detail_kind = {
                    "findings": "finding",
                    "scans": "scan",
                    "views": "view",
                    "inbox": "inbox",
                    "tasks": "task",
                    "methods": "task",
                    "schema": "task",
                    "sites": "site",
                }.get(self.watch_section, self.watch_section)
                self.view = "watch_detail"
            elif key in ("escape", "char:q", "ctrl_c"):
                self.quit_requested = True
            return
        if self.view in ("detail", "help"):
            if key in {"down", "page_down"}:
                self.watch_detail_offset += 1 if key == "down" else 10
            elif key in {"up", "page_up"}:
                self.watch_detail_offset = max(
                    0, self.watch_detail_offset - (1 if key == "up" else 10)
                )
            elif key == "home":
                self.watch_detail_offset = 0
            elif key == "end":
                self.watch_detail_offset = 10**9
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
