"""Interactive terminal shell — ``seohead tui``.

A keyboard-driven command palette over the shared CLI command table. It is a
navigation shell only: it shows what exists and how to invoke it, and it never
starts a crawl, a scan, or any other tool — running tools stays on the
noninteractive CLI and MCP contracts.

Rendering uses ``rich`` (optional ``tui`` extra); keyboard input is stdlib
``termios`` via :mod:`seohead.tui.keys`.
"""

from __future__ import annotations

import os
import sys
import time
from collections.abc import Sequence
from copy import deepcopy
from functools import lru_cache
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
from seohead.tui.labels import label as human_label
from seohead.tui.labels import title as human_title
from seohead.tui.labels import value_lines
from seohead.tui.state import WATCH_SECTIONS, ShellState

#: Below this size the palette cannot keep a usable list plus footer, so the
#: shell renders a compact notice instead of a clipped half-frame.
MIN_WIDTH = 56
MIN_HEIGHT = 16

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
_NOTE_HINTS = "type note · enter save · esc keep draft · ctrl-x discard"
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
        "watch_help": _HELP_HINTS,
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


@lru_cache(maxsize=256)
def _command_reference(name: str) -> list[str]:
    """Reuse actual CLI arguments and shared handler documentation without executing them."""
    from argparse import _SubParsersAction
    from inspect import getdoc

    from seohead.cli import build_parser
    from seohead.servers.handlers import HANDLERS

    parser = build_parser()
    subparsers = next(action for action in parser._actions if isinstance(action, _SubParsersAction))
    command = subparsers.choices.get(name)
    handler = HANDLERS.get(name.replace("-", "_"))
    description = (getdoc(handler) if handler is not None else None) or (
        "Command group; choose one of its listed subcommands."
    )
    return [
        name,
        description,
        "",
        "Not executed here. Run the CLI explicitly when ready.",
        "",
        *(command.format_help().splitlines() if command else [f"seohead {name} --help"]),
    ]


def _detail_lines(state: ShellState, palette: theme.Palette) -> list[Text]:
    return [Text(line) for line in _command_reference(state.selected or "")]


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


def _view_key(state: ShellState) -> tuple:
    """Only data selection belongs in the read key; moving within a page needs no I/O."""
    detail = state.view == "watch_detail"
    return (
        state.watch_section,
        detail,
        state.watch_offset,
        state.watch_query,
        state.watch_status,
        state.watch_sort,
        state.watch_descending,
        state.watch_selected_scan_uuid,
        state.watch_detail_kind if detail else None,
        state.watch_detail_ordinal if detail else None,
        state.watch_item_id if detail else None,
        state.watch_inbox_entry_id if detail else None,
        state.watch_view_name if detail else None,
        state.watch_view_offset if detail else None,
    )


def _read_view(project: str, state: ShellState, snapshot: dict | None) -> dict:
    """Read one bounded page in the background, never during an interactive render."""
    from seohead.projects import observer
    from seohead.projects.inbox import list_entries

    if state.view == "watch_detail":
        if state.watch_detail_kind == "task":
            if state.watch_item_id is None:
                return {"error": "No task selected."}
            return observer.task_detail(project, item_id=state.watch_item_id)
        if state.watch_detail_kind == "finding":
            if state.watch_detail_ordinal is None:
                return {"error": "No finding selected."}
            return observer.finding_detail(
                project,
                scan_uuid=state.watch_selected_scan_uuid,
                ordinal=state.watch_detail_ordinal,
            )
        if state.watch_detail_kind == "view":
            if state.watch_view_name is None:
                return {"error": "No saved view selected."}
            return observer.saved_view_page(
                project,
                name=state.watch_view_name,
                scan_uuid=state.watch_selected_scan_uuid,
                offset=state.watch_view_offset,
            )
    section = state.watch_section
    if section in {"tasks", "methods", "schema"}:
        return observer.checklist_page(
            project,
            offset=state.watch_offset,
            limit=50,
            query=state.watch_query,
            kind={"methods": "method", "schema": "schema"}.get(section),
            state=state.watch_status,
        )
    if section == "findings":
        return observer.findings_page(
            project,
            scan_uuid=state.watch_selected_scan_uuid,
            offset=state.watch_offset,
            query=state.watch_query,
            sort=state.watch_sort,
            descending=state.watch_descending,
        )
    if section == "scans":
        return observer.scans_page(project, offset=state.watch_offset, limit=50)
    if section == "inbox":
        return list_entries(project, consumer="observer/local", limit=50, offset=state.watch_offset)
    return {}


def _page_selection(state: ShellState, items: list, pagination: dict) -> str:
    identities = [
        str(item.get("fingerprint") or item.get("uuid") or item.get("id") or item.get("name"))
        if isinstance(item, dict)
        else str(item)
        for item in items
    ]
    page_identity = (state.watch_section, state.watch_offset, state.watch_query, tuple(identities))
    if (
        state.watch_page_identity is not None
        and page_identity[:3] == state.watch_page_identity[:3]
        and page_identity != state.watch_page_identity
        and state.watch_selected_row_id in identities
    ):
        state.watch_index = identities.index(state.watch_selected_row_id)
    state.watch_count = len(items)
    state.watch_index = min(state.watch_index, max(0, len(items) - 1))
    state.watch_selected_row_id = identities[state.watch_index] if items else None
    state.watch_page_identity = page_identity
    state.watch_next_offset = pagination.get("next_offset")
    state.watch_previous_offset = pagination.get("previous_offset", max(0, state.watch_offset - 50))
    total = pagination.get("total", pagination.get("matched", len(items)))
    start = state.watch_offset + 1 if items else 0
    return f"{start}-{state.watch_offset + len(items)} of {total} · PgUp/PgDn pages"


def _local_entries(snapshot: dict, section: str) -> list:
    if section == "activity":
        runs = [
            {**run, "site_label": site["site"].get("label") or site["site"].get("host")}
            for site in snapshot.get("sites", {}).get("items", [])
            for run in site.get("runs", {}).get("items", [])
        ]
        return runs + list(reversed(snapshot["execution"]["runs"]))
    if section == "sf":
        return [
            item
            for item in snapshot.get("runs", {}).get("items", [])
            if item["kind"] == "screaming_frog"
        ]
    if section == "views":
        return snapshot["saved_views"].get("views", [])
    return snapshot["log"]["text"].splitlines()


def _local_page(snapshot: dict, state: ShellState) -> tuple[list, str]:
    entries = _local_entries(snapshot, state.watch_section)
    offset = state.watch_offset
    items = entries[offset : offset + 50]
    return items, _page_selection(
        state,
        items,
        {
            "total": len(entries),
            "next_offset": offset + 50 if offset + 50 < len(entries) else None,
        },
    )


def _run_summary(item: dict) -> str:
    telemetry = item.get("telemetry", {})
    rate = telemetry.get("current_rate_per_second")
    speed = (
        f"{rate:.2f} {telemetry.get('unit', 'pages')}/s" if rate is not None else "rate unavailable"
    )
    title = item.get("scenario", {}).get("id") or human_label(item.get("kind"))
    mode = item.get("collector", {}).get("mode")
    return " · ".join(
        str(value)
        for value in (
            item.get("site_label"),
            title,
            human_label(item.get("state")),
            human_label(mode) if mode else None,
            speed,
            item.get("next_action"),
        )
        if value
    )


def _watch_lines(
    project: str,
    state: ShellState,
    palette: theme.Palette,
    message: str | None,
    *,
    snapshot: dict | None = None,
    data: dict | None = None,
) -> list[Text]:
    """Browsable retained project evidence; it never starts, cancels or resumes work."""
    if snapshot is None:
        snapshot, failure = _watch_snapshot(project)
    else:
        failure = []
    if snapshot is None:
        return failure
    if data is None:
        data = _read_view(project, state, snapshot)
    if data.get("loading") or data.get("error"):
        return [Text(data.get("error") or "Loading retained entries…")]
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
    if section == "overview":
        completion = progress["audit_task_completion"]
        lines.extend(
            [
                Text(
                    f"Preparation: {human_label(preparation['state'])} · {preparation.get('reason') or ''}"
                ),
                Text(
                    f"Saved scans: {snapshot['scans']['total']} · Notes: {snapshot['inbox']['pagination']['total']}"
                ),
                Text(
                    f"Agreed tasks complete: {str(completion['percent']) + '%' if completion['percent'] is not None else 'Not measured'}"
                ),
                Text(f"Competitors configured: {len(preparation['competitors'])}"),
                Text(""),
                Text("Current work · 2 opens task details"),
            ]
        )
        active = snapshot.get("active_tasks", {}).get("items", [])
        work = active or progress["next_actions"]
        lines.extend(
            Text(
                f"{item.get('title') or item.get('action') or item['id']} · {human_label(item.get('display_state', item['state']))}"
            )
            for item in work[:5]
        )
        if not work:
            lines.append(Text("No active task recorded."))
        latest_notes = snapshot.get("latest_inbox", snapshot["inbox"]).get("entries", [])
        if latest_notes:
            lines.extend(
                [Text(""), Text("Latest note: " + latest_notes[-1]["text"].replace("\n", " "))]
            )
    elif section in {"tasks", "methods", "schema"}:
        progress = data
        items = progress["items"]
        page_hint = _page_selection(state, items, progress["pagination"])
        state.watch_item_id = items[state.watch_index]["id"] if items else None
        entries = [
            f"{human_title(item['title'])} · {human_label(item.get('display_state', item['state']))}"
            for item in items
        ]
        lines.append(
            Text(
                f"{page_hint} · {human_label(state.watch_status) if state.watch_status else 'All states'} · filter {state.watch_query or 'all'}"
            )
        )
        lines.extend(_select(entries, state.watch_index, palette))
        if state.watch_index >= len(entries):
            state.watch_index = max(0, len(entries) - 1)
    elif section == "scans":
        entries = []
        scans = data["items"]
        page_hint = _page_selection(state, scans, data["pagination"])
        for scan in scans:
            status = (
                "partial" if scan["crawl_partial"] or scan["corpus_partial"] else scan["lifecycle"]
            )
            evidence = scan["evidence"]
            finding_count = evidence.get("findings", {}).get("total", "unavailable")
            entries.append(
                f"{human_label(scan['source_kind'])} · {human_label(status)} · {finding_count} findings · {human_label(scan['finish_reason'])} · {scan['uuid'][:8]}"
            )
        if scans:
            state.watch_selected_scan_uuid = scans[state.watch_index]["uuid"]
        lines.append(Text(page_hint))
        lines.extend(_select(entries, state.watch_index, palette))
        for name, step in preparation["steps"].items():
            if name in {"crawl", "sitemap"}:
                lines.append(
                    Text(f"  {name}: {step.get('state', 'unknown')} · {step.get('reason', '')}")
                )
    elif section == "findings":
        page = data
        if page:
            items = page["items"]
            _page_selection(
                state, items, {**page["pagination"], "total": page["counts"]["matched"]}
            )
            state.watch_selected_scan_uuid = page["scan"]["uuid"]
            state.watch_detail_ordinal = None
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
                        f"{human_label(item['severity'])} · {item['message'] or human_label(item['check'] or item['id'])} · {item['target_url'] or ''}"
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
        views, page_hint = _local_page(snapshot, state)
        view_lines = [
            f"{item['name']} · revision {item['revision']} · {', '.join(item['definition']['columns'])}"
            for item in views
        ]
        state.watch_view_name = views[state.watch_index]["name"] if views else None
        review = snapshot["review"]
        lines.append(Text(page_hint))
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
    elif section in {"activity", "sf"}:
        entries, hint = _local_page(snapshot, state)
        lines.append(Text(hint))
        lines.extend(
            _select(
                [_run_summary(item) for item in entries],
                state.watch_index,
                palette,
            )
        )
    elif section == "inbox":
        page = data
        entries = page["entries"]
        page_hint = _page_selection(state, entries, page["pagination"])
        state.watch_inbox_entry_id = entries[state.watch_index]["id"] if entries else None
        lines.append(Text(f"Inbox · {page_hint} · Enter opens outcome"))
        lines.extend(
            _select(
                [
                    f"{item['text'].replace(chr(10), ' ')} · {human_label(item['kind'])} · "
                    f"{human_label(item['triage'][-1]['kind']) if item.get('triage') else 'Waiting for agent'}"
                    for item in entries
                ],
                state.watch_index,
                palette,
            )
        )
    else:
        entries, hint = _local_page(snapshot, state)
        lines.append(Text(hint + " · retained log; Enter expands a line"))
        lines.extend(_select(entries, state.watch_index, palette))
        if snapshot["log"]["truncated"]:
            lines.append(Text("Older log bytes are outside this retained tail."))
    return lines


def _watch_detail_lines(
    project: str,
    state: ShellState,
    palette: theme.Palette,
    *,
    snapshot: dict | None = None,
    data: dict | None = None,
) -> list[Text]:
    if snapshot is None:
        snapshot, failure = _watch_snapshot(project)
        if snapshot is None:
            return failure
    if data is None:
        data = _read_view(project, state, snapshot)
    if data.get("loading") or data.get("error"):
        return [Text(data.get("error") or "Loading retained details…")]
    kind = state.watch_detail_kind
    if kind == "task":
        item = data["item"]
        lines = [
            Text(human_title(item["title"]), style=palette.title if palette.color else ""),
            Text(f"{human_label(item.get('display_state', item['state']))} · {item['id']}"),
            Text(""),
        ]
        for key in (
            "reason",
            "scope",
            "priority",
            "applicability",
            "blocked_by",
            "attempt_status",
            "measurement",
            "attempts",
            "complete",
            "stale",
        ):
            if key in item:
                lines.extend(Text(line) for line in value_lines({key: item[key]}))
        for key in (
            "definition",
            "history",
            "history_total",
            "history_truncated",
            "evidence",
            "review",
            "catalogue",
            "content",
        ):
            value = data.get(key, item.get(key))
            if value is not None:
                lines.extend(
                    [Text(""), Text(human_label(key), style=palette.title if palette.color else "")]
                )
                lines.extend(Text(line) for line in value_lines(value))
        return lines
    if kind == "inbox":
        entry = next(
            (item for item in data["entries"] if item["id"] == state.watch_inbox_entry_id), None
        )
        if entry is None:
            return [Text("Selected inbox entry is unavailable; return and refresh.")]
        lines = [
            Text(human_label(entry["kind"]), style=palette.title if palette.color else ""),
            Text(f"{entry['created_at']} · {entry['id']}"),
            Text(entry["text"]),
            Text(""),
        ]
        lines.extend(
            Text(line)
            for line in value_lines(
                {
                    "goal_state": entry.get("goal_state"),
                    "references": entry.get("references", []),
                    "triage": entry.get("triage", []),
                }
            )
        )
        if not entry.get("triage"):
            lines.append(Text("Saved locally; waiting for an agent triage receipt."))
        return lines
    if kind == "scan":
        scan = next(
            (item for item in data["items"] if item["uuid"] == state.watch_selected_scan_uuid), None
        )
        return [Text(line) for line in value_lines(scan or {"state": "Selected scan unavailable"})]
    if kind == "view":
        page = data["result"]
        pagination = page["pagination"]
        state.watch_view_page_size = pagination["page_size"]
        state.watch_view_next = pagination.get("next_offset")
        return [
            Text(f"Saved view · {state.watch_view_name}"),
            Text(f"Matched {page['counts']['matched']} / {page['counts']['source']} findings"),
            *[Text(line) for line in value_lines(page["items"])],
            Text(f"Results offset {pagination['offset']} · [ Previous / ] Next result page"),
        ]
    if kind in {"activity", "sf", "log"}:
        entries = _local_entries(snapshot, state.watch_section)
        selected = state.watch_offset + state.watch_index
        return [
            Text(line)
            for line in value_lines(
                entries[selected] if selected < len(entries) else "Entry unavailable"
            )
        ]
    finding = data["finding"]
    return [
        Text(
            finding["message"] or human_label(finding["check"] or finding["id"]),
            style=palette.title if palette.color else "",
        ),
        Text(
            f"{human_label(finding['severity'])} · {finding['target_url'] or 'Target not recorded'}"
        ),
        Text(f"Check {finding['check']} · scan {data['scan_uuid']} · ordinal {finding['ordinal']}"),
        Text(""),
        Text("Retained evidence:"),
        Text(
            "Preview limits: 4,096 characters/value, 20 list entries, 40 object fields, 3 levels."
        ),
        *[Text(line) for line in value_lines(data["evidence"])],
        Text(""),
        Text("Retained projection; no check was rerun."),
        *[Text(line) for line in value_lines(data.get("truncation", {}))],
    ]


def _note_lines(state: ShellState, palette: theme.Palette) -> list[Text]:
    title = "new proposed goal" if state.note_kind == "proposed_goal" else "new project note"
    label = Text(title, style=palette.title) if palette.color else Text(title.upper())
    cursor = len(state.note_text) if state.note_cursor is None else state.note_cursor
    draft = Text(state.note_text or "(type or dictate text, then press enter)")
    if state.note_text:
        draft.stylize("reverse", cursor, cursor + 1)
        if cursor == len(state.note_text):
            draft.append("▏", style=palette.accent if palette.color else "")
    return [
        label,
        Text(f"{len(state.note_text):,}/{state.note_limit:,} characters"),
        Text(state.note_error, style="#fbbf24" if palette.color else ""),
        draft,
    ]


def _save_note(project: str, state: ShellState) -> str:
    """Keep a draft visible when a local save is refused or fails."""
    from seohead.projects.inbox import submit

    references = [f"section:{state.watch_section}"]
    if state.watch_item_id and state.watch_section in {"tasks", "methods", "schema"}:
        references.append(f"task:{state.watch_item_id}")
    if state.watch_selected_scan_uuid:
        references.append(f"scan:{state.watch_selected_scan_uuid}")
        if state.watch_detail_ordinal is not None:
            references.append(
                f"finding:{state.watch_selected_scan_uuid}/{state.watch_detail_ordinal}"
            )
    try:
        receipt = submit(
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
    state.note_drafts.pop(state.note_kind, None)
    state.note_cursor = 0
    state.note_error = ""
    state.note_ready = False
    return f"{human_label(state.note_kind)} saved · {receipt['entry']['id']} · waiting for agent triage"


class _DashboardLayout(Layout):
    """Use the requested viewport even when rendering a saved preview."""

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> RenderResult:
        yield from super().__rich_console__(console, options.update(height=self.size))


class _ObserverRefresh:
    """One worker owns snapshot/page/detail reads and explicit note writes."""

    def __init__(self, project: str):
        self.project = project
        self.result: tuple[dict | None, list[Text]] = (
            None,
            [Text("Loading retained project evidence…")],
        )
        self.queue: SimpleQueue = SimpleQueue()
        self.worker: Thread | None = None
        self.next_refresh = 0.0
        self.key: tuple | None = None
        self.page: dict = {"loading": True}
        self.pending_note: ShellState | None = None
        self.saving = False
        self.saved: tuple[ShellState, str, str] | None = None
        self.updated_at: float | None = None

    def save(self, state: ShellState) -> None:
        if not self.saving:
            self.pending_note = deepcopy(state)
            self.saving = True

    def page_for(self, state: ShellState) -> dict:
        return self.page if self.key == _view_key(state) else {"loading": True}

    def poll(self, state: ShellState | None = None) -> tuple[dict | None, list[Text]]:
        while True:
            try:
                event = self.queue.get_nowait()
            except Empty:
                break
            if event[0] == "saved":
                self.saved = event[1:]
                self.saving = False
                continue
            _, key, result, page = event
            self.key, self.result, self.page = key, result, page
            self.updated_at = time.monotonic()
            self.next_refresh = self.updated_at + 0.5
        wanted = _view_key(state) if state else None
        if (self.worker is None or not self.worker.is_alive()) and (
            self.pending_note is not None
            or wanted != self.key
            or time.monotonic() >= self.next_refresh
        ):
            note, self.pending_note = self.pending_note, None
            self.worker = Thread(target=self._read, args=(deepcopy(state), note), daemon=True)
            self.worker.start()
        return self.result

    def _read(self, state: ShellState | None = None, note: ShellState | None = None) -> None:
        if note is not None:
            submitted_text = note.note_text
            message = _save_note(self.project, note)
            self.queue.put(("saved", note, message, submitted_text))
        key = _view_key(state) if state else None
        try:
            result = _watch_snapshot(self.project)
            page = _read_view(self.project, state, result[0]) if state and result[0] else {}
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            result = self.result
            page = {"error": f"Retained data unavailable: {exc}"}
        self.queue.put(("read", key, result, page))


def _meter(label: str, done: int | None, total: int | None, palette: theme.Palette) -> Text:
    """A denominator-backed gauge; unknown coverage never becomes zero or complete."""
    text = Text(label + "\n", style=palette.muted if palette.color else "")
    if total == 0:
        text.append("None in agreed scope")
        return text
    if done is None or total is None:
        text.append("Not measured", style="#fbbf24" if palette.color else "")
        return text
    fraction = min(1.0, max(0.0, done / total))
    filled = round(20 * fraction)
    text.append(f"{done:,} / {total:,}  {fraction:.0%}\n")
    text.append("█" * filled, style=palette.accent if palette.color else "")
    text.append("░" * (20 - filled), style=palette.muted if palette.color else "")
    return text


def _watch_dashboard(
    state: ShellState,
    project: str,
    palette: theme.Palette,
    width: int,
    height: int,
    message: str | None,
    snapshot_override: tuple[dict | None, list[Text]] | None = None,
    view_override: dict | None = None,
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
        Layout(name="header", size=3), Layout(name="body"), Layout(name="footer", size=4)
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
            run_hint = f"{human_label(recent_run['kind'])} · {human_label(recent_run['state'])} · {human_label(last_phase)}"
            runtime = (
                recent_run.get("collector_runtime")
                if recent_run["kind"] == "screaming_frog"
                else recent_run.get("controller")
            )
            if recent_run["state"] == "running" and (runtime or {}).get("state") == "live":
                marker = (
                    "◐◓◑◒"[int(time.monotonic()) % 4]
                    if state.motion_enabled and palette.color
                    else "+"
                )
                run_hint = f"{marker} observed active · {run_hint}"
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
                    f" {'>' if selected else ' '} {str(index % 10) if index < 11 else 'a'}  {'SF scans' if section == 'sf' else 'Schema' if section == 'schema' else human_label(section)}",
                    style=palette.highlight if selected and palette.color else "",
                )
            )
        nav.extend(
            [
                Text(""),
                Text("n  Add note", style=accent),
                Text("g  Propose goal", style=accent),
                Text(""),
                Text("Local evidence", style=muted),
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
            Text(state.filter_draft or "Type text, then press Enter"),
        ]
    elif state.view == "watch_help":
        body = [
            Text(line)
            for line in (
                "1-9 / 0  Switch section · a Structured data",
                "↑/↓  Select · Home/End  First/last row on page",
                "PgUp/PgDn  Previous/next data page",
                "Enter  Open details · Esc  Return from details",
                "f  Filter tasks/methods/findings · c  Clear filter",
                "t  Cycle task/method state filter",
                "Findings: s Sort · r Reverse",
                "n  Note / resume draft · g  Proposed goal / resume draft",
                "Editor: ←/→ Home/End move · Delete/Backspace edit",
                "Editor: Enter save · Esc keep draft · Ctrl-X discard",
                "m  Toggle motion · q / Ctrl-C  Quit · ?  Help",
            )
        ]
    elif snapshot is None:
        body = failure
    elif state.view == "watch_detail":
        body = _watch_detail_lines(project, state, palette, snapshot=snapshot, data=view_override)
    else:
        body = _watch_lines(project, state, palette, message, snapshot=snapshot, data=view_override)
    available = max(1, height - 9)
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
        count = max(1, available - 3)
        cursor = len(state.note_text) if state.note_cursor is None else state.note_cursor
        before_cursor = Text(state.note_text[:cursor]).wrap(
            Console(width=content_width), content_width
        )
        first = max(0, len(before_cursor) - count)
        body = body[:-1] + draft_lines[first : first + count]
    # Scroll selected rows into the viewport while retaining their context.
    if state.view == "watch" and state.watch_section != "overview":
        first = max(0, state.watch_index - max(1, available - 6) + 1)
        body = body[:4] + body[4 + first :]
    for line in body:
        line.no_wrap = state.view != "note"
        line.overflow = "fold" if state.view == "note" else "ellipsis"
    title = "Compose note" if state.view == "note" else state.watch_section.title()
    if (
        snapshot
        and state.view == "watch"
        and state.watch_section == "overview"
        and height >= 32
        and width >= 100
        and not (view_override or {}).get("error")
    ):
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
        retained_pages = evidence.get("committed_pages")
        if retained_pages is None and isinstance(outcomes, dict):
            retained_pages = sum(outcomes.values())
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
        frontier_values = [counts.get(key) for key in ("done", "queued", "inflight")]
        discovered = (
            sum(frontier_values)
            if all(isinstance(value, int) for value in frontier_values)
            else None
        )
        sitemap = evidence.get("sitemaps", {}).get("fetch_summaries", {})
        crawl_lines = [
            Text(
                f"{human_label(scan_state.lower())}  ·  {human_label(latest.get('source_kind'))}  ·  {human_label(latest.get('finish_reason'))}",
                style=accent,
            ),
            _meter(
                "Discovered URL coverage · scope can grow", counts.get("done"), discovered, palette
            ),
            Text(
                f"Queue {counts.get('queued', 'unknown')}  ·  In flight {counts.get('inflight', 'unknown')}  ·  Excluded {counts.get('excluded', 'unknown')}"
            ),
            Text(
                "Sitemap: " + (" · ".join(value_lines(sitemap)) if sitemap else "Not measured"),
                style="#fbbf24" if palette.color and not sitemap else "",
            ),
        ]
        if native_run:
            collector = native_run["collector"]
            telemetry = native_run.get("telemetry", {})
            rate = telemetry.get("current_rate_per_second")
            crawl_lines.insert(
                1,
                Text(
                    f"Mode {human_label(collector['mode'])} · {human_label(telemetry.get('state'))} · "
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
        items = snapshot.get("active_tasks", {}).get("items", snapshot["progress"]["items"])
        custom = [item for item in items if str(item.get("id", "")).startswith("custom:")]
        checklist.append(Text("AGENT TASKS" if custom else "NEXT ACTIONS", style=accent))
        work_items = custom if custom else snapshot["progress"]["next_actions"]
        for item in work_items[: max(3, height - 26)]:
            marker = "+" if item["state"] == "completed" else "-"
            checklist.append(
                Text(
                    f"{marker} {item.get('title') or item.get('action') or item['id']} · {human_label(item.get('display_state', item['state']))}",
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
        entries = snapshot.get("latest_inbox", snapshot["inbox"]).get("entries", [])
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
                padding=(0, 1),
            )
        )
    footer = Text()
    if state.view == "note":
        footer.append("Enter Save   Esc Keep draft   Ctrl-X Discard   ←/→ Edit\n", style=accent)
        footer.append("Saved locally; unread notice requires a project-bound agent", style=muted)
    elif state.view == "watch_filter":
        footer.append("Type filter   Enter Apply   Esc Cancel", style=accent)
    elif state.view == "watch_detail":
        footer.append("↑ ↓ Scroll evidence   PgUp/PgDn Scroll page   Enter/Esc Back", style=accent)
        if state.watch_detail_kind == "view":
            footer.append("\n[ Previous / ] Next result page", style=accent)
    elif state.view == "watch_help":
        footer.append("Enter/Esc Back\n", style=accent)
    elif not sidebar:
        footer.append(
            "1 Home  2 Tasks  3 Methods  4 Scans  5 Findings\n6 Views  7 Activity  8 Log  9 SF  0 Inbox  a Schema\n",
            style=muted,
        )
    elif state.watch_section == "findings":
        footer.append(
            "↑ ↓ Browse   Enter Evidence   PgUp/PgDn Page   f Filter   s Sort\n", style=muted
        )
    elif state.watch_section == "overview":
        footer.append("2 Task details   3 Methods   a Structured data\n", style=muted)
    elif state.watch_section in {"tasks", "methods", "schema"}:
        footer.append(
            "↑ ↓ Browse   Enter Details   PgUp/PgDn Page   f Filter   t State\n", style=muted
        )
    else:
        footer.append("↑ ↓ Browse   Enter Details   PgUp/PgDn Page   f Filter\n", style=muted)
    if state.view == "watch":
        if not sidebar and state.watch_section != "overview":
            footer.append("↑↓ Enter   ", style=accent)
            if state.watch_section in {"tasks", "methods", "schema", "findings"}:
                footer.append("f Filter   ", style=accent)
        footer.append("n Note   g Goal   ? Keys   q Quit", style=accent)
        if state.note_text or any(state.note_drafts.values()):
            footer.append("   Draft kept", style=muted)
    if message:
        footer.append("\n" + message, style=accent)
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
    view_override: dict | None = None,
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
    if project is not None and state.view in {
        "watch",
        "watch_detail",
        "watch_filter",
        "note",
        "watch_help",
    }:
        return Group(
            _watch_dashboard(
                state, project, palette, width, height, message, snapshot_override, view_override
            )
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
    if state.view in {"detail", "help"}:
        body = [wrapped for line in body for wrapped in line.wrap(Console(width=width), width)]
        state.watch_detail_offset = min(state.watch_detail_offset, max(0, len(body) - body_rows))
        body = body[state.watch_detail_offset :]
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
    state.motion_enabled = os.environ.get("SEOHEAD_REDUCED_MOTION", "").lower() not in {
        "1",
        "true",
        "yes",
    }
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
            redraw = True
            next_frame = 0.0
            last_size = console.size
            last_update = None
            while not state.quit_requested:
                snapshot = refresh.poll(state) if refresh else None
                if refresh and refresh.saved:
                    saved, message, submitted_text = refresh.saved
                    refresh.saved = None
                    redraw = True
                    if saved.note_error:
                        if state.note_kind == saved.note_kind:
                            state.note_error = saved.note_error
                            state.view = "note"
                    else:
                        if state.note_drafts.get(saved.note_kind) == submitted_text:
                            state.note_drafts.pop(saved.note_kind, None)
                        if state.note_kind == saved.note_kind and state.note_text == submitted_text:
                            state.note_text = ""
                            state.note_cursor = 0
                updated = refresh.updated_at if refresh else None
                if (
                    redraw
                    or updated != last_update
                    or console.size != last_size
                    or time.monotonic() >= next_frame
                ):
                    live.update(
                        Panel(
                            build_frame(
                                state,
                                width=max(1, console.size.width - 4),
                                height=max(1, console.size.height - 2),
                                palette=palette,
                                project=project,
                                message=message,
                                snapshot_override=snapshot,
                                view_override=refresh.page_for(state) if refresh else None,
                            ),
                            border_style="cyan" if palette.color else "none",
                            padding=(0, 1),
                            height=console.size.height,
                        ),
                        refresh=True,
                    )
                    next_frame = time.monotonic() + 0.5
                    last_size, last_update = console.size, updated
                key = keys.read_key(fd, timeout=0.05)
                redraw = key != "timeout"
                input_deadline = time.monotonic() + 0.01
                for _ in range(256):
                    before_view = state.view
                    if (
                        key == "enter"
                        and state.view == "watch"
                        and refresh
                        and refresh.page_for(state).get("loading")
                    ):
                        message = "Loading this page; select an entry once it arrives."
                        break
                    state.handle_key(key)
                    if (
                        state.note_ready
                        or state.quit_requested
                        or before_view == "watch"
                        or state.view == "watch"
                        or time.monotonic() >= input_deadline
                    ):
                        break
                    key = keys.read_key(fd, timeout=0.0)
                    if key == "timeout":
                        break
                if refresh and state.note_ready:
                    refresh.save(state)
                    state.note_ready = False
                    message = "Saving note locally…"
                if refresh and refresh.saving and state.quit_requested:
                    state.quit_requested = False
                    message = "Save in progress; waiting for local receipt before exit."
    except KeyboardInterrupt:
        pass
    finally:
        console.file.write("\x1b[?2004l")
        console.file.flush()
    drafts = {**state.note_drafts, state.note_kind: state.note_text}
    for kind, draft in drafts.items():
        if draft:
            console.print(
                Text(f"Unsaved {human_label(kind).lower()} — retained below for recovery:\n{draft}")
            )
    return 0


def main(argv: list[str] | None = None) -> int:
    """Module entry: ``python -m seohead.tui.app``."""
    no_color = bool(argv and "--no-color" in argv)
    return run(no_color=no_color)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main(sys.argv[1:]))
