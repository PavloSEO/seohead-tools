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
_WATCH_HINTS = (
    "1 overview · 2 tasks · 3 methods · 4 scans · 5 findings · 6 views · 7 activity · 8 log "
    "· arrows/page browse · n note · g goal · q quit"
)
_NOTE_HINTS = "type dictated note · enter save · esc discard"
_FILTER_HINTS = "type finding filter · enter apply · esc discard"
_WATCH_DETAIL_HINTS = "enter/esc back · ctrl-c quit"

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
        "watch_filter": _FILTER_HINTS,
        "watch_detail": _WATCH_DETAIL_HINTS,
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


def _select(lines: list[str], index: int, palette: theme.Palette) -> list[Text]:
    """Render a bounded selection list without assigning any action to Enter."""
    if not lines:
        return [Text("  no retained entries")]
    selected = min(max(index, 0), len(lines) - 1)
    rendered = []
    for ordinal, line in enumerate(lines):
        marker = ">" if ordinal == selected else " "
        rendered.append(
            Text(
                f" {marker} {line}",
                style=palette.highlight if palette.color and ordinal == selected else "",
            )
        )
    return rendered


def _watch_snapshot(project: str) -> tuple[dict | None, list[Text]]:
    from seohead.projects.observer import observe

    try:
        return observe(project), []
    except (OSError, ValueError) as exc:
        return None, [Text(f"project unavailable: {exc}"), Text("No action was started.")]


def _watch_lines(
    project: str, state: ShellState, palette: theme.Palette, message: str | None
) -> list[Text]:
    """Browsable retained project evidence; it never starts, cancels or resumes work."""
    snapshot, failure = _watch_snapshot(project)
    if snapshot is None:
        return failure
    site = snapshot["project"]["site"]
    progress = snapshot["progress"]
    preparation = snapshot["preparation"]
    section = state.watch_section
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
        completion = progress["audit_task_completion"]
        lines.extend(
            [
                Text(
                    f"preparation  {preparation['state']} · {preparation.get('reason') or 'recorded state'}"
                ),
                Text(
                    f"scans        {snapshot['scans']['total']} retained; unknown totals remain unknown"
                ),
                Text(
                    f"task coverage {completion['percent'] if completion['percent'] is not None else 'unknown'}"
                ),
                Text(
                    f"competitors  {len(preparation['competitors'])} configured; configured is not analyzed"
                ),
                Text(f"goals/notes  {snapshot['inbox']['pagination']['total']} persistent entries"),
                Text(
                    f"workflow runs {len(snapshot['execution']['runs'])} · next {snapshot['execution']['next_action'] or 'none'}"
                ),
                Text(
                    f"monitor      {'configured' if snapshot['monitor']['policy'] else 'not configured'} · last {snapshot['monitor']['last_run']['state'] if snapshot['monitor']['last_run'] else 'none'}"
                ),
                Text(""),
                Text("Use numbered views to inspect retained evidence, not an agent claim."),
            ]
        )
    elif section == "tasks":
        entries = [
            f"[{item['state']}] {item['id']} · {item['title']} · {item['attempt_status']}"
            for item in progress["items"]
        ]
        lines.append(
            Text("checklist (completion, partial, skipped and unavailable remain distinct):")
        )
        lines.extend(_select(entries, state.watch_index, palette))
        if state.watch_index >= len(entries):
            state.watch_index = max(0, len(entries) - 1)
    elif section == "methods":
        methods = [
            f"[{item['state']}] {item['id']} · attempt {item['attempt_status']} · {item['reason'] or 'no reason recorded'}"
            for item in snapshot["methods"]
        ]
        competitors = [
            f"competitor [{item.get('state', 'unknown')}] {item['url']}"
            for item in preparation["competitors"]
        ]
        lines.append(Text("planned scenarios, skills and competitor coverage:"))
        lines.extend(_select([*methods, *competitors], state.watch_index, palette))
    elif section == "scans":
        entries = []
        for scan in snapshot["scans"]["items"]:
            status = (
                "partial" if scan["crawl_partial"] or scan["corpus_partial"] else scan["lifecycle"]
            )
            evidence = scan["evidence"]
            finding_count = evidence.get("findings", {}).get("total", "unavailable")
            entries.append(
                f"[{status}] {scan['uuid']} · {scan['source_kind']} · findings {finding_count} · {scan['finish_reason'] or 'unknown stop'}"
            )
        if snapshot["scans"]["items"]:
            state.watch_index = min(state.watch_index, len(snapshot["scans"]["items"]) - 1)
            state.watch_selected_scan_uuid = snapshot["scans"]["items"][state.watch_index]["uuid"]
        lines.append(Text("native/Screaming Frog retained scans; select a scan to inspect state:"))
        lines.extend(_select(entries, state.watch_index, palette))
        for name, step in preparation["steps"].items():
            if name in {"crawl", "sitemap"}:
                lines.append(
                    Text(f"  {name}: {step.get('state', 'unknown')} · {step.get('reason', '')}")
                )
    elif section == "findings":
        from seohead.projects.observer import findings_page

        try:
            page = findings_page(
                project,
                scan_uuid=state.watch_selected_scan_uuid,
                offset=state.watch_offset,
                query=state.watch_query,
                sort=state.watch_sort,
                descending=state.watch_descending,
            )
        except (OSError, ValueError) as exc:
            lines.append(Text(f"findings unavailable: {exc}"))
        else:
            items = page["items"]
            state.watch_index = min(state.watch_index, max(0, len(items) - 1))
            if items:
                state.watch_detail_ordinal = items[state.watch_index]["ordinal"]
            lines.append(
                Text(
                    f"findings {page['counts']['matched']}/{page['counts']['source']} · page {page['pagination']['offset']} · "
                    f"filter={state.watch_query!r} · sort={state.watch_sort} {'desc' if state.watch_descending else 'asc'}"
                )
            )
            lines.extend(
                _select(
                    [
                        f"[{item['severity'] or 'unknown'}] {item['check'] or item['id']} · {item['target_url'] or ''} · {item['message'] or ''}"
                        for item in items
                    ],
                    state.watch_index,
                    palette,
                )
            )
            lines.append(
                Text(
                    "f filter · c clear · s sort field · r reverse · enter evidence · page keys next/previous"
                )
            )
    elif section == "views":
        saved = snapshot["saved_views"]
        view_lines = [
            f"{item['name']} · revision {item['revision']} · {', '.join(item['definition']['columns'])}"
            for item in saved.get("views", [])
        ]
        if saved.get("views"):
            state.watch_index = min(state.watch_index, len(saved["views"]) - 1)
            state.watch_view_name = saved["views"][state.watch_index]["name"]
        review = snapshot["review"]
        lines.append(Text("saved finding views and review/export readiness:"))
        lines.extend(_select(view_lines, state.watch_index, palette))
        lines.extend(
            [
                Text(f"manual review waiting: {len(review['manual_waiting'])}"),
                Text(f"approved deliverables ready: {len(review['deliverable_ready'])}"),
                Text(
                    "Export is an explicit CLI/MCP action; this observer does not publish anything."
                ),
            ]
        )
    elif section == "activity":
        monitor = snapshot["monitor"]
        lines.extend(
            [
                Text(f"workflow next action: {snapshot['execution']['next_action'] or 'none'}"),
                Text(f"workflow resumable run: {snapshot['execution']['resumable_run'] or 'none'}"),
                Text(f"monitor policy: {'configured' if monitor['policy'] else 'not configured'}"),
                Text(f"monitor runner: {monitor['runner'].get('state', 'unknown')}"),
                Text(
                    f"monitor last run: {monitor['last_run']['state'] if monitor['last_run'] else 'none'}"
                ),
                Text(""),
                Text("recent workflow records:"),
            ]
        )
        lines.extend(
            _select(
                [
                    f"[{run['state']}] {run['scenario']['id']} · {run['id']} · next {run['next_action'] or 'none'}"
                    for run in snapshot["execution"]["runs"][-20:]
                ],
                state.watch_index,
                palette,
            )
        )
    else:
        lines.append(Text("project execution log (bounded retained tail):"))
        lines.extend(Text(line) for line in snapshot["log"]["text"].splitlines()[-20:])
        if snapshot["log"]["truncated"]:
            lines.append(Text("  log tail is truncated"))
    lines.extend(
        (
            Text(""),
            Text("The observer is read-only except for an explicit saved note or proposed goal."),
        )
    )
    return lines


def _watch_detail_lines(project: str, state: ShellState, palette: theme.Palette) -> list[Text]:
    from seohead.projects.observer import finding_detail, saved_view_page

    try:
        if state.watch_detail_kind == "scan":
            snapshot, failure = _watch_snapshot(project)
            if snapshot is None:
                return failure
            scan = next(
                (
                    item
                    for item in snapshot["scans"]["items"]
                    if item["uuid"] == state.watch_selected_scan_uuid
                ),
                None,
            )
            if scan is None:
                return [
                    Text("The selected retained scan is no longer available."),
                    Text("Press escape to return."),
                ]
            evidence = scan["evidence"]
            return [
                Text("retained scan", style=palette.title if palette.color else ""),
                Text(f"uuid       {scan['uuid']}"),
                Text(f"source     {scan['source_kind']}"),
                Text(f"lifecycle  {scan['lifecycle']}"),
                Text(f"stop       {scan['finish_reason'] or 'unknown'}"),
                Text(f"frontier   {evidence.get('frontier', {}).get('counts', 'unavailable')}"),
                Text(f"findings   {evidence.get('findings', {}).get('total', 'unavailable')}"),
                Text(
                    f"sitemaps   {evidence.get('sitemaps', {}).get('fetch_summaries', 'unavailable')}"
                ),
                Text(
                    "This page only reads the saved scan. Choose Findings to browse its evidence."
                ),
            ]
        if state.watch_detail_kind == "view":
            if state.watch_view_name is None:
                return [Text("No saved view is selected."), Text("Press escape to return.")]
            page = saved_view_page(
                project,
                name=state.watch_view_name,
                scan_uuid=state.watch_selected_scan_uuid,
            )["result"]
            return [
                Text(
                    f"saved view · {state.watch_view_name}",
                    style=palette.title if palette.color else "",
                ),
                Text(
                    f"source findings {page['counts']['source']} · matched {page['counts']['matched']}"
                ),
                Text(f"state {page['state']} · page size {page['pagination']['page_size']}"),
                Text(""),
                *[
                    Text(
                        "  "
                        + " · ".join(
                            f"{column}={item['fields'].get(column) or 'unknown'}"
                            for column in page["columns"]
                        )
                    )
                    for item in page["items"]
                ],
                Text(
                    "This applies a saved local definition to retained evidence; it does not export or publish."
                ),
            ]
        if state.watch_detail_ordinal is None:
            return [Text("No retained finding is selected."), Text("Press escape to return.")]
        detail = finding_detail(
            project, scan_uuid=state.watch_selected_scan_uuid, ordinal=state.watch_detail_ordinal
        )
    except (OSError, ValueError) as exc:
        return [Text(f"observer detail unavailable: {exc}"), Text("Press escape to return.")]
    finding = detail["finding"]
    lines = [
        Text(
            f"finding evidence · {finding['check'] or finding['id']}",
            style=palette.title if palette.color else "",
        ),
        Text(f"ordinal  {finding['ordinal']}"),
        Text(f"severity {finding['severity'] or 'unknown'}"),
        Text(f"target   {finding['target_url'] or 'not recorded'}"),
        Text(f"message  {finding['message'] or 'not recorded'}"),
        Text(f"fingerprint {finding['fingerprint'] or 'not recorded'}"),
        Text(""),
        Text("retained evidence:"),
    ]
    for key, value in detail["evidence"].items():
        rendered = str(value).replace("\n", " ")
        lines.append(Text(f"  {key}: {rendered[:240]}"))
    lines.append(Text(""))
    lines.append(Text("This is a bounded projection of the saved audit; it did not rerun a check."))
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
        body = _watch_lines(project, state, palette, message)
    elif state.view == "watch_detail" and project is not None:
        body = _watch_detail_lines(project, state, palette)
    elif state.view == "note":
        body = _note_lines(state, palette)
    elif state.view == "watch_filter":
        body = [
            Text("finding filter", style=palette.title if palette.color else ""),
            Text(""),
            Text(state.watch_query or "(type text, then press enter)"),
        ]
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
