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
from collections.abc import Sequence
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
_WATCH_HINTS = "1 overview · 2 tasks · 3 methods · 4 scans · 5 log · n note · g goal · q quit"
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


def command_rows(commands: Sequence[str]) -> list[str]:
    """The CLI passes its command table in, keeping presentation out of the core graph."""
    return list(commands)


def _header_text(palette: theme.Palette) -> Text:
    if not palette.color:
        return Text(f"{_TITLE} {__version__} — {_SUBTITLE}")
    text = Text()
    text.append(_TITLE, style=palette.accent)
    text.append(f" {__version__}", style=palette.title)
    text.append(f"  {_SUBTITLE}", style=palette.muted)
    return text


def _status_line(state: ShellState, palette: theme.Palette, width: int, height: int) -> Text:
    mode = (
        theme.badge("ok", "COLOR", palette)
        if palette.color
        else theme.badge("off", "NO COLOR", palette)
    )
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
            Text.from_markup(f"[{palette.muted}]{escape(empty)}[/]")
            if palette.color
            else Text(empty)
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


def _watch_lines(
    project: str, palette: theme.Palette, message: str | None, section: str
) -> list[Text]:
    """Project evidence only: this observer does not start, cancel, or resume work."""
    from seohead.projects.observer import observe

    try:
        snapshot = observe(project)
    except (OSError, ValueError) as exc:
        return [Text(f"project unavailable: {exc}"), Text("No action was started.")]
    site = snapshot["project"]["site"]
    progress = snapshot["progress"]
    preparation = snapshot["preparation"]
    lines = [
        Text(
            f"{section} · {site['label'] or site['host']}",
            style=palette.title if palette.color else "",
        ),
        Text(f"site     {site['target']}"),
        Text(""),
    ]
    if message:
        lines.append(Text(message, style=palette.accent if palette.color else ""))
    if section == "overview":
        lines.extend(
            (
                Text(
                    f"preparation  {preparation['state']} · {preparation.get('reason') or 'recorded state'}"
                ),
                Text(
                    f"competitors  {len(preparation['competitors'])} configured; prepared is not analyzed"
                ),
                Text(f"scans        {snapshot['scans']['total']} retained"),
                Text(
                    f"task coverage {progress['audit_task_completion']['percent'] if progress['audit_task_completion']['percent'] is not None else 'unknown'}"
                ),
                Text(
                    f"goals/notes  {snapshot['inbox']['pagination']['total']} retained prompts and handoffs"
                ),
                Text(
                    f"workflow runs {len(snapshot['execution']['runs'])} · next {snapshot['execution']['next_action'] or 'none'}"
                ),
                Text(
                    f"monitor      {'configured' if snapshot['monitor']['policy'] else 'not configured'} · "
                    f"last run {snapshot['monitor']['last_run']['state'] if snapshot['monitor']['last_run'] else 'none'}"
                ),
                Text(""),
                Text("Use numbered views to inspect evidence rather than an agent claim."),
            )
        )
    elif section == "tasks":
        lines.append(Text("planned checklist and attempts:"))
        for item in progress["items"]:
            lines.append(Text(f"  [{item['state']}] {item['id']} — {item['title']}"))
        if not progress["items"]:
            lines.append(Text("  no initialized checklist entries"))
    elif section == "methods":
        lines.append(Text("scenarios and method skills:"))
        for item in snapshot["methods"]:
            lines.append(Text(f"  [{item['state']}] {item['id']} · {item['attempt_status']}"))
        if not snapshot["methods"]:
            lines.append(Text("  none planned; this is not completion"))
        lines.append(Text("competitor coverage:"))
        for item in preparation["competitors"]:
            lines.append(Text(f"  [{item.get('state', 'unknown')}] {item['url']}"))
        lines.append(Text("goals and prompts:"))
        for run in snapshot["execution"]["runs"][-3:]:
            lines.append(Text(f"  [{run['state']}] {run['scenario_id']} · {run['id']}"))
        for item in snapshot["inbox"]["entries"][-5:]:
            lines.append(Text(f"  [{item['goal_state'] or item['kind']}] {item['text']}"))
    elif section == "scans":
        lines.append(Text("native/Screaming Frog retained scan history:"))
        for scan in snapshot["scans"]["items"]:
            state = (
                "partial" if scan["crawl_partial"] or scan["corpus_partial"] else scan["lifecycle"]
            )
            lines.append(
                Text(
                    f"  [{state}] {scan['uuid']} · {scan['source_kind']} · {scan['finish_reason'] or 'unknown stop'}"
                )
            )
            evidence = scan["evidence"]
            if evidence["state"] == "available":
                frontier = evidence["frontier"]
                counts = frontier.get("counts") if frontier["state"] == "available" else None
                lines.append(
                    Text(
                        f"    frontier {counts if counts is not None else 'unknown'} · "
                        f"findings {evidence['findings']['total']} · "
                        f"sitemaps {evidence['sitemaps']['fetch_summaries']}"
                    )
                )
                for finding in evidence["findings"]["items"][:3]:
                    lines.append(
                        Text(
                            f"      {finding['severity']} {finding['check']} · "
                            f"{finding['target_url'] or finding['id']}"
                        )
                    )
                if evidence["findings"]["truncated"]:
                    lines.append(Text("      additional findings retained in the scan artifact"))
            else:
                lines.append(Text(f"    evidence unavailable: {evidence['reason']}"))
        if not snapshot["scans"]["items"]:
            lines.append(Text("  no retained scan; counts and sitemap state are unknown"))
        for name, step in preparation["steps"].items():
            if name in {"crawl", "sitemap"}:
                lines.append(
                    Text(f"  {name}: {step.get('state', 'unknown')} · {step.get('reason', '')}")
                )
        monitor = snapshot["monitor"]
        if monitor["last_run"] is not None:
            lines.append(
                Text(
                    f"  monitor {monitor['last_run']['state']} · "
                    f"scan {monitor['last_run']['scan_id']} · "
                    f"observed {len(monitor['last_run']['observed_urls'])} URLs"
                )
            )
    else:
        lines.append(Text("project execution log (tail):"))
        lines.extend(Text(line) for line in snapshot["log"]["text"].splitlines()[-12:])
        if snapshot["log"]["truncated"]:
            lines.append(Text("  log tail is truncated"))
    lines.extend((Text(""), Text("The observer is read-only except for an explicit saved note.")))
    return lines


def _note_lines(state: ShellState, palette: theme.Palette) -> list[Text]:
    title = "new proposed goal" if state.note_kind == "proposed_goal" else "new project note"
    label = Text(title, style=palette.title) if palette.color else Text(title.upper())
    return [label, Text(""), Text(state.note_text or "(type or dictate text, then press enter)")]


def build_frame(
    state: ShellState,
    *,
    width: int,
    height: int,
    palette: theme.Palette,
    project: str | None = None,
    message: str | None = None,
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
        body = _watch_lines(project, palette, message, state.watch_section)
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
    commands: Sequence[str] = (),
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
    state = ShellState(commands=command_rows(commands), view="watch" if project else "palette")
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

                    submit(
                        project,
                        text=state.note_text,
                        kind=state.note_kind,
                        author_role="specialist",
                    )
                    message = f"{state.note_kind.replace('_', ' ')} saved to the project inbox"
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
