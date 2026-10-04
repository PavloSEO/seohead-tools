"""Interactive terminal shell — ``seohead tui``.

A keyboard-driven command palette over the shared CLI command table. It is a
navigation shell only: it shows what exists and how to invoke it, and it never
starts a crawl, a scan, or any other tool — running tools stays on the
noninteractive CLI and MCP contracts.

Rendering uses ``rich`` (optional ``tui`` extra); keyboard input is stdlib
``termios`` via :mod:`seohead.tui.keys`.
"""

from __future__ import annotations

import sys
from typing import TextIO

from rich.console import Console, Group
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from seohead import __version__
from seohead.tui import keys, theme
from seohead.tui.state import GROUP_COMMANDS, ShellState

#: Below this size the palette cannot keep a usable list plus footer, so the
#: shell renders a compact notice instead of a clipped half-frame.
MIN_WIDTH = 50
MIN_HEIGHT = 12

_TITLE = "SEOHEAD Tools"
_SUBTITLE = "interactive shell"

_PALETTE_HINTS = "type filter · up/down move · enter open · esc clear/back · ? keys · q quit"
_DETAIL_HINTS = "enter/esc back · ctrl-c quit"
_HELP_HINTS = "esc back · ctrl-c quit"
_WATCH_HINTS = "n note · r refresh · esc/q quit · refreshes every second"
_NOTE_HINTS = "type dictated note · enter save · esc discard"

_HELP_LINES = (
    ("up / down, page up / page down", "move the cursor through the command list"),
    ("home / end", "jump to the first / last command"),
    ("printable characters", "filter the palette"),
    ("backspace", "delete one filter character"),
    ("enter", "open the selected command"),
    ("esc", "close a view, clear the filter, then leave the shell"),
    ("q (empty filter)", "leave the shell"),
    ("ctrl-c", "leave the shell immediately"),
    ("?", "show this reference"),
)


def command_rows() -> list[str]:
    """The palette source: the shared CLI command table plus group namespaces."""
    from seohead.cli import COMMANDS

    return list(COMMANDS)


def _header_text(palette: theme.Palette) -> Text:
    if not palette.color:
        return Text(f"{_TITLE} {__version__} — {_SUBTITLE}")
    text = Text()
    text.append(_TITLE, style=palette.accent)
    text.append(f" {__version__}", style=palette.title)
    text.append(f"  {_SUBTITLE}", style=palette.muted)
    return text


def _status_line(state: ShellState, palette: theme.Palette, width: int, height: int) -> Text:
    mode = theme.badge("ok", "COLOR", palette) if palette.color else theme.badge("off", "NO COLOR", palette)
    size = theme.badge("info", f"{width}x{height}", palette)
    hints = {
        "palette": _PALETTE_HINTS,
        "detail": _DETAIL_HINTS,
        "help": _HELP_HINTS,
        "watch": _WATCH_HINTS,
        "note": _NOTE_HINTS,
    }[state.view]
    if palette.color:
        return Text.from_markup(f"{mode}  {size}  {hints}")
    # Plain mode renders ASCII badges containing brackets; append literally so
    # they are not reparsed as markup.
    return Text(f"{mode}  {size}  {hints}")


def _palette_lines(state: ShellState, palette: theme.Palette, rows: int) -> list[Text]:
    items = state.filtered
    cursor = min(state.index, max(len(items) - 1, 0))
    first = max(0, min(cursor - rows + 1, max(len(items) - rows, 0)))
    visible = items[first : first + rows]
    lines: list[Text] = []
    for offset, name in enumerate(visible):
        current = first + offset == cursor
        marker = ">" if current else " "
        if palette.color and current:
            lines.append(Text.from_markup(f"[{palette.highlight}] {marker} {escape(name)} [/]"))
        else:
            lines.append(Text(f" {marker} {name}"))
    if not items:
        empty = f"no commands match {state.query!r}"
        lines.append(
            Text.from_markup(f"[{palette.muted}]{escape(empty)}[/]") if palette.color else Text(empty)
        )
    return lines


def _detail_lines(state: ShellState, palette: theme.Palette) -> list[Text]:
    name = state.selected or ""
    run_hint = f"seohead {name} --help"
    if name in GROUP_COMMANDS:
        run_hint = f"seohead {name} --help  (namespace with its own subcommands)"
    rows = [
        f"command   {name}",
        "",
        theme.badge("warn", "NOT EXECUTED HERE", palette),
        "",
        "This shell only navigates the toolkit. To run the tool,",
        f"leave the shell and use: {run_hint}",
        "The same capability is exposed over `seohead mcp` for agents.",
    ]
    if not palette.color:
        # ASCII badges contain brackets; keep them literal, not markup.
        return [Text(row) for row in rows]
    return [Text.from_markup(row) for row in rows]


def _help_lines(palette: theme.Palette) -> list[Text]:
    out = [Text("keys", style=palette.title) if palette.color else Text("KEYS"), Text("")]
    width = max(len(k) for k, _ in _HELP_LINES)
    for combo, meaning in _HELP_LINES:
        out.append(Text(f"  {combo.ljust(width)}  {meaning}"))
    return out


def _watch_lines(project: str, palette: theme.Palette, message: str | None) -> list[Text]:
    """Project evidence only: this observer does not start, cancel, or resume work."""
    from seohead.projects.progress import project_progress
    from seohead.projects.workspace import project_status

    try:
        status = project_status(project)
        progress = project_progress(project, limit=8)
    except (OSError, ValueError) as exc:
        return [Text(f"project unavailable: {exc}"), Text("No action was started.")]
    site = status["project"]["site"]
    scans = status["scans"]
    partial_scans = sum(
        bool(item.get("crawl_partial") or item.get("corpus_partial")) for item in scans["items"]
    )
    lines = [
        Text(f"project  {site['label'] or site['host']}", style=palette.title if palette.color else ""),
        Text(f"site     {site['target']}"),
        Text(f"scans    {scans['total']} retained · {partial_scans} partial in this page"),
        Text(f"checklist {progress['state']} · task completion {progress['audit_task_completion']['percent'] if progress['audit_task_completion']['percent'] is not None else 'unknown'}"),
        Text(""),
    ]
    if message:
        lines.append(Text(message, style=palette.accent if palette.color else ""))
    lines.append(Text("remaining evidence:"))
    for item in progress["items"]:
        lines.append(Text(f"  [{item['state']}] {item['id']} — {item['title']}"))
    if not progress["items"]:
        lines.append(Text("  no initialized checklist entries"))
    lines.extend((Text(""), Text("The observer is read-only except for an explicit saved note.")))
    return lines


def _note_lines(state: ShellState, palette: theme.Palette) -> list[Text]:
    label = Text("new project note", style=palette.title) if palette.color else Text("NEW PROJECT NOTE")
    return [label, Text(""), Text(state.note_text or "(type or dictate text, then press enter)")]


def build_frame(
    state: ShellState, *, width: int, height: int, palette: theme.Palette,
    project: str | None = None, message: str | None = None,
) -> Group:
    """One screen of the shell as a rich renderable (also used by tests)."""
    header = _header_text(palette)
    status = _status_line(state, palette, width, height)
    if width < MIN_WIDTH or height < MIN_HEIGHT:
        notice = Text(
            f"terminal too small ({width}x{height}); need at least {MIN_WIDTH}x{MIN_HEIGHT}\n"
            "resize the window or press q / ctrl-c to leave"
        )
        return Group(header, Text(""), notice, Text(""), status)
    body_rows = height - 6
    if state.view == "detail":
        body = _detail_lines(state, palette)
    elif state.view == "help":
        body = _help_lines(palette)
    elif state.view == "watch" and project is not None:
        body = _watch_lines(project, palette, message)
    elif state.view == "note":
        body = _note_lines(state, palette)
    else:
        filter_line = Text(f"filter: {state.query}")
        if palette.color:
            filter_line = Text.from_markup(f"[{palette.muted}]filter:[/] {escape(state.query)}")
        return Group(
            header,
            Text(""),
            filter_line,
            *_palette_lines(state, palette, body_rows),
            Text(""),
            status,
        )
    return Group(header, Text(""), *body[: max(body_rows, 1)], Text(""), status)


def run(
    *,
    no_color: bool = False,
    stdin: TextIO | None = None,
    console: Console | None = None,
    project: str | None = None,
) -> int:
    """Launch the interactive shell; returns a process exit code."""
    stdin = sys.stdin if stdin is None else stdin
    if console is None:
        use_color = theme.color_enabled(no_color_flag=no_color, stdout_is_tty=sys.stdout.isatty())
        console = Console(no_color=not use_color)
    palette = theme.resolve_palette(color=not console.no_color)
    if not stdin.isatty():
        print(
            "seohead tui is interactive and needs a terminal; "
            "use the noninteractive commands (seohead --help) or `seohead mcp`.",
            file=sys.stderr,
        )
        return 1
    state = ShellState(commands=command_rows(), view="watch" if project else "palette")
    fd = stdin.fileno()
    message: str | None = None
    try:
        with keys.raw_mode(fd):
            while not state.quit_requested:
                console.clear()
                console.print(
                    Panel(
                        build_frame(
                            state,
                            width=console.size.width,
                            height=console.size.height,
                            palette=palette,
                            project=project,
                            message=message,
                        ),
                        border_style="cyan" if palette.color else "none",
                        padding=(0, 1),
                    )
                )
                state.handle_key(keys.read_key(fd, timeout=1.0 if project else None))
                if project and state.note_ready:
                    from seohead.projects.inbox import submit

                    submit(project, text=state.note_text, author_role="specialist")
                    message = "note saved to the project inbox"
                    state.note_text = ""
                    state.note_ready = False
    except KeyboardInterrupt:
        pass
    finally:
        console.clear()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Module entry: ``python -m seohead.tui.app``."""
    no_color = bool(argv and "--no-color" in argv)
    return run(no_color=no_color)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
