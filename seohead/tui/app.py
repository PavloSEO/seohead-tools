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
import time
from collections.abc import Sequence
from pathlib import Path
from queue import Empty, SimpleQueue
from threading import Thread
from typing import TextIO

from rich.console import Console, ConsoleOptions, Group, RenderResult
from rich.layout import Layout
from rich.live import Live
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from seohead import __version__
from seohead.tui import keys, theme
from seohead.tui.state import GROUP_COMMANDS, WATCH_SECTIONS, ShellState

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
    "· 9 Screaming Frog · 0 inbox "
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
    project: str,
    state: ShellState,
    palette: theme.Palette,
    message: str | None,
    *,
    snapshot: dict | None = None,
) -> list[Text]:
    """Browsable retained project evidence; it never starts, cancels or resumes work."""
    if snapshot is None:
        snapshot, failure = _watch_snapshot(project)
    else:
        failure = []
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
        from seohead.projects.progress import project_progress

        progress = project_progress(project, limit=50, offset=state.watch_offset)
        entries = [
            f"[{item['state']}] {item['id']} · {item['title']} · {item['attempt_status']}"
            for item in progress["items"]
        ]
        lines.append(
            Text(
                f"checklist · page offset {state.watch_offset} · {progress['pagination']['total']} declared items:"
            )
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
    elif section == "sf":
        runs = [
            item
            for item in snapshot.get("runs", {}).get("items", [])
            if item["kind"] == "screaming_frog"
        ]
        lines.append(Text("Project-bound Screaming Frog runs · live CLI / supplied exports"))
        if not runs:
            lines.append(Text("No Screaming Frog run is recorded for this project."))
        for run in runs[:10]:
            collector = run["collector"]
            events = run.get("events", [])
            phase = events[-1]["phase"] if events else "not recorded"
            lines.extend(
                [
                    Text(f"{run['state']} · {collector['mode']} · phase {phase}"),
                    Text(
                        f"Owner PID {run['pid']} · {run['pid_state']} · started {run['started_at']}"
                    ),
                    Text(
                        f"Collector PID {run.get('collector_pid') or 'not recorded'} · "
                        f"{run.get('collector_runtime', {}).get('state', 'unknown')} · "
                        f"observed {run.get('collector_runtime', {}).get('observed_at', 'not recorded')}"
                    ),
                    Text(f"Artifact: {run.get('artifact') or 'not recorded'}"),
                    Text(f"Finish: {run.get('finish_reason') or 'not recorded'}"),
                    Text(""),
                ]
            )
        lines.append(
            Text("Live SF URL counts/speed are unavailable unless the collector records them.")
        )
    elif section == "inbox":
        from seohead.projects.inbox import list_entries

        page = list_entries(project, consumer="observer/local", limit=50, offset=state.watch_offset)
        entries = page["entries"]
        state.watch_index = min(state.watch_index, max(0, len(entries) - 1))
        state.watch_inbox_entry_id = entries[state.watch_index]["id"] if entries else None
        lines.append(
            Text(
                f"Inbox · offset {state.watch_offset} · {page['pagination']['total']} entries · Enter opens outcome"
            )
        )
        lines.extend(
            _select(
                [
                    f"{item['id']} · {item['kind']} · "
                    f"{item['triage'][-1]['kind'] if item.get('triage') else 'waiting for agent'} · "
                    f"{item['text'].replace(chr(10), ' ')}"
                    for item in entries
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
    if state.watch_detail_kind == "inbox":
        from seohead.projects.inbox import list_entries

        page = list_entries(project, consumer="observer/local", limit=50, offset=state.watch_offset)
        entry = next(
            (item for item in page["entries"] if item["id"] == state.watch_inbox_entry_id), None
        )
        if entry is None:
            return [Text("Selected inbox entry is unavailable; return and refresh.")]
        lines = [
            Text(entry["id"], style=palette.title if palette.color else ""),
            Text(
                f"{entry['kind']} · {entry['created_at']} · goal {entry.get('goal_state') or 'none'}"
            ),
            Text(entry["text"]),
            Text(""),
            Text("Agent triage / linked work:"),
        ]
        for outcome in entry.get("triage", []):
            lines.extend(
                [
                    Text(f"{outcome['kind']} · {outcome['actor']} · {outcome['recorded_at']}"),
                    Text(outcome["reason"]),
                    Text(
                        str(
                            {
                                key: outcome[key]
                                for key in ("task_ids", "goal_id", "competitors")
                                if key in outcome
                            }
                        )
                    ),
                ]
            )
        if not entry.get("triage"):
            lines.append(Text("Saved locally; no agent triage receipt recorded."))
        return lines
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
    return [
        label,
        Text(f"{len(state.note_text):,}/{state.note_limit:,} characters"),
        Text(state.note_error, style="#fbbf24" if palette.color else ""),
        Text(state.note_text or "(type or dictate text, then press enter)"),
    ]


def _save_note(project: str, state: ShellState) -> str:
    """Keep a draft visible when a local save is refused or fails."""
    from seohead.projects.inbox import submit

    references = [f"section:{state.watch_section}"]
    if state.watch_selected_scan_uuid:
        references.append(f"scan:{state.watch_selected_scan_uuid}")
        if state.watch_detail_ordinal is not None:
            references.append(
                f"finding:{state.watch_selected_scan_uuid}/{state.watch_detail_ordinal}"
            )
    try:
        submit(
            project,
            text=state.note_text,
            kind=state.note_kind,
            author_role="specialist",
            references=references,
        )
    except (OSError, ValueError) as exc:
        state.note_error = f"Could not save: {exc}"
        state.view = "note"
        state.note_ready = False
        return state.note_error
    state.note_text = ""
    state.note_error = ""
    state.note_ready = False
    return f"{state.note_kind.replace('_', ' ')} saved locally; waiting for a bound agent"


class _DashboardLayout(Layout):
    """Use the requested viewport even when rendering a saved preview."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield from super().__rich_console__(console, options.update(height=self.size))


class _ObserverRefresh:
    """One background reader keeps retained-evidence I/O off the input loop."""

    def __init__(self, project: str):
        self.project = project
        self.result: tuple[dict | None, list[Text]] = (
            None,
            [Text("Loading retained project evidence…")],
        )
        self.queue: SimpleQueue = SimpleQueue()
        self.worker: Thread | None = None
        self.next_refresh = 0.0

    def poll(self) -> tuple[dict | None, list[Text]]:
        try:
            self.result = self.queue.get_nowait()
            self.next_refresh = time.monotonic() + 2.0
        except Empty:
            pass
        if (
            self.worker is None or not self.worker.is_alive()
        ) and time.monotonic() >= self.next_refresh:
            self.worker = Thread(target=self._read, daemon=True)
            self.worker.start()
        return self.result

    def _read(self) -> None:
        self.queue.put(_watch_snapshot(self.project))


def _meter(label: str, done: int | None, total: int | None, palette: theme.Palette) -> Text:
    """A denominator-backed gauge; unknown coverage never becomes zero or complete."""
    text = Text(label + "\n", style=palette.muted if palette.color else "")
    if done is None or not total:
        text.append("Not measured", style="#fbbf24" if palette.color else "")
        return text
    fraction = min(1.0, max(0.0, done / total))
    filled = round(20 * fraction)
    text.append("█" * filled, style=palette.accent if palette.color else "")
    text.append("░" * (20 - filled), style=palette.muted if palette.color else "")
    text.append(f"  {done:,} / {total:,}  {fraction:.0%}")
    return text


def _watch_dashboard(
    state: ShellState,
    project: str,
    palette: theme.Palette,
    width: int,
    height: int,
    message: str | None,
    snapshot_override: tuple[dict | None, list[Text]] | None = None,
) -> Layout:
    """Responsive terminal workspace composed from retained evidence only."""
    snapshot, failure = (
        snapshot_override if snapshot_override is not None else _watch_snapshot(project)
    )
    accent = palette.accent if palette.color else ""
    muted = palette.muted if palette.color else ""
    border = "#475569" if palette.color else "none"
    root = _DashboardLayout(size=height)
    root.split_column(
        Layout(name="header", size=3), Layout(name="body"), Layout(name="footer", size=3)
    )
    site = snapshot["project"]["site"] if snapshot else {}
    header = Table.grid(expand=True)
    header.add_column(ratio=1)
    header.add_column(justify="right")
    header.add_row(
        Text("SEOHEAD  /  PROJECT OBSERVER", style=accent),
        Text(
            "NOTE DRAFT  ·  LOCAL" if state.view == "note" else "READ ONLY  ·  LOCAL", style=muted
        ),
    )
    header.add_row(
        Text(site.get("label") or site.get("host") or "Project", style="bold"),
        Text(f"{width + 4} x {height + 2}", style=muted),
    )
    if snapshot:
        recent_run = next(iter(snapshot.get("runs", {}).get("items", [])), None)
        run_hint = "No project run recorded"
        if recent_run:
            last_phase = (
                recent_run["events"][-1]["phase"] if recent_run["events"] else "unknown phase"
            )
            run_hint = f"{recent_run['kind']} · {recent_run['state']} · {last_phase}"
        header.add_row(
            Text(
                f"Notes {snapshot['inbox']['pagination']['total']}  ·  "
                f"Competitors {len(snapshot['preparation']['competitors'])}  ·  0 Inbox",
                style=muted,
            ),
            Text(run_hint, style=accent),
        )
    root["header"].update(header)
    sidebar = width >= 130 and height >= 26
    if sidebar:
        root["body"].split_row(Layout(name="nav", size=23), Layout(name="content"))
        nav = [Text("WORKSPACE", style=muted), Text("")]
        for index, section in enumerate(WATCH_SECTIONS, 1):
            selected = section == state.watch_section
            nav.append(
                Text(
                    f" {'>' if selected else ' '} {index % 10}  {'Screaming Frog' if section == 'sf' else section.title()}",
                    style=palette.highlight if selected and palette.color else "",
                )
            )
        nav.extend(
            [
                Text(""),
                Text("n  Add note", style=accent),
                Text("g  Propose goal", style=accent),
                Text(""),
                Text("Evidence stays local", style=muted),
            ]
        )
        root["nav"].update(Panel(Group(*nav), border_style=border, padding=(1, 1)))
        content = root["content"]
    else:
        content = root["body"]
    if state.view == "note":
        body = _note_lines(state, palette)
    elif state.view == "watch_filter":
        body = [
            Text("Filter findings", style=accent),
            Text(""),
            Text(state.watch_query or "Type text, then press Enter"),
        ]
    elif snapshot is None:
        body = failure
    elif state.view == "watch_detail":
        body = _watch_detail_lines(project, state, palette)
    else:
        body = _watch_lines(project, state, palette, message, snapshot=snapshot)
    available = max(1, height - 10)
    if state.view == "watch_detail":
        content_width = max(10, width - (27 if sidebar else 4))
        wrapping_console = Console(width=content_width)
        body = [
            wrapped
            for line in body
            for wrapped in line.wrap(
                wrapping_console, content_width, overflow="fold", no_wrap=False
            )
        ]
        state.watch_detail_offset = min(state.watch_detail_offset, max(0, len(body) - available))
        body = body[state.watch_detail_offset :]
    elif state.view == "note":
        content_width = max(10, width - (27 if sidebar else 4))
        draft_lines = list(body[-1].wrap(Console(width=content_width), content_width))
        body = body[:-1] + draft_lines[-max(1, available - 3) :]
    # Scroll selected rows into the viewport while retaining their context.
    if state.view == "watch" and state.watch_section not in {"overview", "log"}:
        first = max(0, state.watch_index - max(1, available - 6) + 1)
        body = body[:4] + body[4 + first :]
    for line in body:
        line.no_wrap = state.view != "note"
        line.overflow = "fold" if state.view == "note" else "ellipsis"
    title = "Compose note" if state.view == "note" else state.watch_section.title()
    if snapshot and state.view == "watch" and state.watch_section == "overview" and height >= 24:
        scans = snapshot["scans"]["items"]
        latest = scans[0] if scans else {}
        evidence = latest.get("evidence", {})
        counts = evidence.get("frontier", {}).get("counts") or {}
        native_run = next(
            (
                run
                for run in snapshot.get("runs", {}).get("items", [])
                if run["kind"] == "native"
                and run.get("artifact")
                and latest.get("path")
                and (Path(project) / run["artifact"]).resolve() == Path(latest["path"]).resolve()
                and run["collector"]["config_fingerprint"] == latest.get("config_fingerprint")
            ),
            None,
        )
        outcomes = evidence.get("committed_page_outcomes")
        retained_pages = sum(outcomes.values()) if isinstance(outcomes, dict) else None
        findings = evidence.get("findings", {}).get("total")
        cards = Table.grid(expand=True, padding=(0, 1))
        for _ in range(3):
            cards.add_column(ratio=1)
        cards.add_row(
            *[
                Panel(Text(f"{value}\n{label}", style=accent), border_style=border)
                for value, label in [
                    (str(retained_pages) if retained_pages is not None else "—", "Pages retained"),
                    (str(findings) if findings is not None else "—", "Findings"),
                    (str(snapshot["scans"]["total"]), "Saved scans"),
                ]
            ]
        )
        content.split_column(
            Layout(name="metrics", size=4),
            Layout(name="crawl", size=11 if native_run and height >= 32 else 8),
            Layout(name="evidence"),
        )
        content["metrics"].update(cards)
        scan_state = (
            "PARTIAL" if latest.get("crawl_partial") else latest.get("lifecycle", "NOT RUN").upper()
        )
        discovered = sum(counts.get(key, 0) for key in ("done", "queued", "inflight"))
        sitemap = evidence.get("sitemaps", {}).get("fetch_summaries", {})
        crawl_lines = [
            Text(
                f"{scan_state}  ·  {latest.get('source_kind', 'collector unknown')}  ·  {latest.get('finish_reason') or 'no finish recorded'}",
                style=accent,
            ),
            _meter(
                "Discovered URL coverage · scope can grow", counts.get("done"), discovered, palette
            ),
            Text(
                f"Queue {counts.get('queued', 'unknown')}  ·  In flight {counts.get('inflight', 'unknown')}  ·  Excluded {counts.get('excluded', 'unknown')}"
            ),
            Text(
                "Sitemap: " + (str(sitemap) if sitemap else "NOT MEASURED"),
                style="#fbbf24" if palette.color and not sitemap else "",
            ),
        ]
        if native_run:
            collector = native_run["collector"]
            rate = native_run["counters"].get("rate_per_second")
            crawl_lines.insert(
                1,
                Text(
                    f"Mode {collector['mode']} · last collection sample "
                    f"{f'{rate:.2f} URLs/s' if rate is not None else 'speed unavailable'}"
                ),
            )
            if height >= 32:
                crawl_lines.append(
                    _meter(
                        "Run URL budget · not whole-site completion",
                        native_run["counters"].get("fetched"),
                        collector.get("max_urls"),
                        palette,
                    )
                )
        crawl = Group(*crawl_lines)
        content["crawl"].update(
            Panel(
                crawl,
                title="Collection & scope",
                title_align="left",
                border_style=border,
                padding=(0, 1),
            )
        )
        primary = next(
            (
                item
                for item in snapshot.get("sites", {}).get("items", [])
                if item["role"] == "primary"
            ),
            {},
        )
        method_counts = primary.get("methods", {}).get("kinds", {})
        meters = Table.grid(expand=True, padding=(0, 1))
        meters.add_column(ratio=1)
        meters.add_column(ratio=1)
        meter_cells = []
        for kind, label in [("scenario", "Scenarios"), ("skill", "Skills")]:
            record = method_counts.get(kind, {})
            meter_cells.append(
                _meter(label, record.get("completed"), record.get("expected"), palette)
            )
        meters.add_row(*meter_cells)
        checklist = [meters, Text("")]
        items = snapshot["progress"]["items"]
        custom = [item for item in items if str(item.get("id", "")).startswith("custom:")]
        checklist.append(Text("AGENT TASKS" if custom else "NEXT ACTIONS", style=accent))
        work_items = custom if custom else snapshot["progress"]["next_actions"]
        for item in work_items[: max(3, height - 26)]:
            marker = "+" if item["state"] == "completed" else "-"
            checklist.append(
                Text(
                    f"{marker} [{item['state']}] {item.get('title') or item.get('action') or item['id']}",
                    overflow="ellipsis",
                    no_wrap=True,
                )
            )
        if not work_items:
            checklist.append(
                Text("No agent task recorded · 2 opens the complete checklist", style=muted)
            )
        findings_lines = [Text("FINDINGS BY SEVERITY", style=accent), Text("")]
        severity = evidence.get("findings", {}).get("by_severity", {})
        peak = max(severity.values(), default=1) or 1
        for name in ("critical", "warning", "notice"):
            value = severity.get(name, 0) if findings is not None else None
            color = (
                {"critical": "#fb7185", "warning": "#fbbf24", "notice": "#67e8f9"}[name]
                if palette.color
                else ""
            )
            findings_lines.append(
                Text(
                    f"{name:9} {str(value) if value is not None else '—':>5}  "
                    + "━" * round(15 * (value or 0) / peak),
                    style=color,
                )
            )
        findings_lines.extend([Text(""), Text("PROJECT NOTES", style=accent)])
        entries = snapshot["inbox"].get("entries", [])
        findings_lines.extend(
            Text(item.get("text", ""), no_wrap=True, overflow="ellipsis") for item in entries[-3:]
        )
        if not entries:
            findings_lines.append(Text("n  Write or dictate a note", style=muted))
        findings_lines.extend([Text(""), Text("COMPETITORS", style=accent)])
        competitors = [
            item
            for item in snapshot.get("sites", {}).get("items", [])
            if item["role"] == "competitor"
        ]
        for item in competitors[:5]:
            findings_lines.append(
                Text(
                    f"{item['site']['host'] or item['site']['target']} · scans {item['scans']['total']}",
                    no_wrap=True,
                    overflow="ellipsis",
                )
            )
        if not competitors:
            findings_lines.append(Text("No competitors configured", style=muted))
        if height >= 38:
            findings_lines.extend(
                [
                    Text(""),
                    Text("WORKFLOW", style=accent),
                    Text(
                        f"Next: {snapshot['execution']['next_action'] or 'no active workflow'}",
                        no_wrap=True,
                        overflow="ellipsis",
                    ),
                    Text(""),
                    Text("PROJECT LOG", style=accent),
                ]
            )
            log_lines = snapshot["log"]["text"].splitlines()[-max(3, height - 35) :]
            findings_lines.extend(
                Text(line, no_wrap=True, overflow="ellipsis") for line in log_lines
            )
        if width >= 130:
            content["evidence"].split_row(Layout(name="checklist"), Layout(name="insights"))
            content["checklist"].update(
                Panel(
                    Group(*checklist),
                    title="Scenarios & skills",
                    title_align="left",
                    border_style=border,
                )
            )
            content["insights"].update(
                Panel(
                    Group(*findings_lines),
                    title="Findings & notes",
                    title_align="left",
                    border_style=border,
                )
            )
        else:
            content["evidence"].update(
                Panel(
                    Group(*checklist),
                    title="Scenarios & skills",
                    title_align="left",
                    border_style=border,
                )
            )
    else:
        content.update(
            Panel(
                Group(*body[:available]),
                title=title,
                title_align="left",
                border_style=border,
                padding=(1, 1),
            )
        )
    footer = Text()
    if state.view == "note":
        footer.append("Type or dictate text   Enter Save   Esc Cancel\n", style=accent)
        footer.append("Saved locally; unread notice requires a project-bound agent", style=muted)
    elif state.view == "watch_filter":
        footer.append("Type filter   Enter Apply   Esc Cancel", style=accent)
    elif state.view == "watch_detail":
        footer.append("↑ ↓ Scroll evidence   PgUp/PgDn Scroll page   Enter/Esc Back", style=accent)
    elif not sidebar:
        footer.append(
            "1 Home  2 Tasks  3 Methods  4 Scans  5 Findings\n6 Views  7 Activity  8 Log  9 SF  0 Inbox\n",
            style=muted,
        )
    elif state.watch_section == "findings":
        footer.append(
            "↑ ↓ Browse   Enter Evidence   PgUp/PgDn Page   f Filter   s Sort\n", style=muted
        )
    else:
        footer.append("↑ ↓ Browse   Enter Evidence   1-9 / 0 Switch section\n", style=muted)
    if state.view == "watch":
        footer.append("n Note   g Goal   Esc Back   q Quit", style=accent)
    footer.no_wrap = True
    footer.overflow = "ellipsis"
    root["footer"].update(footer)
    return root


def build_frame(
    state: ShellState,
    *,
    width: int,
    height: int,
    palette: theme.Palette,
    project: str | None = None,
    message: str | None = None,
    snapshot_override: tuple[dict | None, list[Text]] | None = None,
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
    if project is not None and state.view in {"watch", "watch_detail", "watch_filter", "note"}:
        return Group(
            _watch_dashboard(state, project, palette, width, height, message, snapshot_override)
        )
    footer = list(status.wrap(Console(width=width), width))
    body_rows = max(1, height - 4 - len(footer))
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
            *_palette_lines(state, palette, max(1, body_rows - 1)),
            Text(""),
            *footer,
        )
    # Keep each evidence row within its allotted line; a long URL or note
    # must not push the footer below the terminal viewport.
    for line in body:
        line.no_wrap = True
        line.overflow = "ellipsis"
    return Group(header, Text(""), *body[:body_rows], Text(""), *footer)


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
    if project:
        from seohead.projects.inbox import MAX_TEXT

        state.note_limit = MAX_TEXT
    fd = stdin.fileno()
    message: str | None = None
    refresh = _ObserverRefresh(project) if project else None
    try:
        with (
            keys.raw_mode(fd),
            Live(
                console=console,
                screen=True,
                auto_refresh=False,
                vertical_overflow="crop",
            ) as live,
        ):
            console.file.write("\x1b[?2004h")
            console.file.flush()
            while not state.quit_requested:
                live.update(
                    Panel(
                        build_frame(
                            state,
                            width=max(1, console.size.width - 4),
                            height=max(1, console.size.height - 2),
                            palette=palette,
                            project=project,
                            message=message,
                            snapshot_override=refresh.poll() if refresh else None,
                        ),
                        border_style="cyan" if palette.color else "none",
                        padding=(0, 1),
                        height=console.size.height,
                    ),
                    refresh=True,
                )
                state.handle_key(keys.read_key(fd, timeout=1.0 if project else None))
                if project and state.note_ready:
                    message = _save_note(project, state)
    except KeyboardInterrupt:
        pass
    finally:
        console.file.write("\x1b[?2004l")
        console.file.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    """Module entry: ``python -m seohead.tui.app``."""
    no_color = bool(argv and "--no-color" in argv)
    return run(no_color=no_color)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
