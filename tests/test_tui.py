"""The observer shell remains an optional, non-executing terminal adapter."""

from __future__ import annotations

import fcntl
import os
import pty
import resource
import termios
import time
from threading import Event

import pytest
from rich.console import Console

from seohead import cli
from seohead.projects.workspace import create_project
from seohead.tui import theme
from seohead.tui.app import (
    MIN_HEIGHT,
    MIN_WIDTH,
    _meter,
    _ObserverRefresh,
    _watch_lines,
    build_frame,
)
from seohead.tui.keys import raw_mode, read_key
from seohead.tui.state import ShellState
from tests.test_scan_history import _finished


def test_palette_navigation_filters_without_running_a_command():
    state = ShellState(commands=["project-status", "crawl-site"])
    state.handle_key("char:p")
    state.handle_key("char:r")
    assert state.selected == "project-status"
    state.handle_key("enter")
    assert state.view == "detail"
    state.handle_key("escape")
    assert state.view == "palette" and state.quit_requested is False


def test_plain_small_terminal_has_ascii_status_and_resize_message():
    state = ShellState(commands=["project-status"])
    palette = theme.resolve_palette(color=False)
    frame = build_frame(state, width=MIN_WIDTH - 1, height=MIN_HEIGHT - 1, palette=palette)
    console = Console(no_color=True, width=MIN_WIDTH, record=True)
    console.print(frame)
    rendered = console.export_text()
    assert "terminal too small" in rendered
    assert "[-] NO COLOR" in rendered


def test_tui_is_an_optional_command_with_a_plain_mode_flag():
    args = cli.build_parser().parse_args(["tui", "--no-color"])
    assert args.command == "tui" and args.no_color is True
    watch = cli.build_parser().parse_args(["watch", "--project", "synthetic", "--no-color"])
    assert watch.command == "watch" and watch.project == "synthetic"
    assert theme.color_enabled(no_color_flag=True, stdout_is_tty=True) is False


def test_interactive_entrypoints_are_declared_without_becoming_handler_commands():
    assert set(cli.INTERACTIVE_COMMANDS) == {"tui", "watch"}
    assert set(cli.INTERACTIVE_COMMANDS).isdisjoint(cli.COMMANDS)
    assert set(cli.INTERACTIVE_COMMANDS) <= set(cli.DOCUMENTED_CLI_ENTRYPOINTS)


def test_raw_key_reader_keeps_one_utf8_character_intact():
    reader, writer = os.pipe()
    try:
        os.write(writer, "П".encode())
        assert read_key(reader) == "char:П"
    finally:
        os.close(reader)
        os.close(writer)


def test_bracketed_paste_preserves_multiline_text_until_explicit_save():
    reader, writer = os.pipe()
    try:
        os.write(writer, b"\x1b[200~first\nsecond\x1b[201~\r")
        state = ShellState(commands=[], view="note")
        while True:
            key = read_key(reader)
            state.handle_key(key)
            if key == "paste_end":
                break
        assert state.note_text == "first\nsecond"
        assert state.view == "note" and not state.note_ready
        state.handle_key(read_key(reader))
        assert state.note_ready
    finally:
        os.close(reader)
        os.close(writer)


def test_high_descriptor_keeps_timeout_unicode_and_bracketed_paste():
    if resource.getrlimit(resource.RLIMIT_NOFILE)[0] <= 1024:
        pytest.skip("host descriptor limit cannot exercise FD_SETSIZE")
    reader, writer = os.pipe()
    high = fcntl.fcntl(reader, fcntl.F_DUPFD, 1024)
    try:
        assert read_key(high, timeout=0) == "timeout"
        os.write(writer, "\x1b[200~П\x1b[201~".encode())
        assert [read_key(high) for _ in range(3)] == ["paste_start", "char:П", "paste_end"]
    finally:
        os.close(high)
        os.close(reader)
        os.close(writer)


def test_delete_and_modified_arrows_do_not_cancel_or_pollute_note():
    reader, writer = os.pipe()
    try:
        os.write(writer, b"\x1b[3~\x1b[1;5D\r")
        state = ShellState(commands=[], view="note", note_text="retain this draft")
        state.handle_key(read_key(reader))
        state.handle_key(read_key(reader))
        assert state.view == "note" and state.note_text == "retain this draft"
        assert read_key(reader) == "enter"
    finally:
        os.close(reader)
        os.close(writer)


def test_overlong_note_and_save_failure_preserve_the_draft(tmp_path, monkeypatch):
    from seohead.tui.app import _save_note

    state = ShellState(commands=[], view="note", note_text="x" * 8001)
    state.handle_key("enter")
    assert state.view == "note" and not state.note_ready
    assert "shorten" in state.note_error
    state.note_text = "Do not lose this question"
    state.note_ready = True

    def refused(*args, **kwargs):
        raise OSError("write refused")

    monkeypatch.setattr("seohead.projects.inbox.submit", refused)
    assert "write refused" in _save_note(str(tmp_path), state)
    assert state.note_text == "Do not lose this question"
    assert state.view == "note" and not state.note_ready


def test_saved_note_is_linked_to_selected_finding(tmp_path):
    from seohead.projects.inbox import list_entries
    from seohead.tui.app import _save_note

    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    state = ShellState(
        commands=[],
        view="watch",
        note_text="Please recheck this finding",
        watch_section="findings",
        watch_selected_scan_uuid="scan-001",
        watch_detail_ordinal=7,
        note_ready=True,
    )
    _save_note(str(root), state)
    entry = list_entries(root, consumer="proof")["entries"][0]
    assert entry["references"] == ["section:findings", "scan:scan-001", "finding:scan-001/7"]
    assert state.note_text == "" and not state.note_ready


def test_sf_and_inbox_tabs_expose_bound_runs_and_human_notes(tmp_path):
    from seohead.projects.inbox import submit
    from seohead.projects.observer import observe
    from seohead.projects.run_observation import start

    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    start(
        root,
        kind="screaming_frog",
        mode="sf_exports",
        max_urls=0,
        config_fingerprint="offline-exports",
        artifact=None,
        counters={"fetched": None, "queued": None, "inflight": None, "excluded": None},
    )
    submit(root, text="Please inspect this competitor")
    snapshot = observe(root)
    palette = theme.resolve_palette(color=False)
    state = ShellState(commands=[], view="watch")
    state.handle_key("char:9")
    sf = "\n".join(
        line.plain for line in _watch_lines(str(root), state, palette, None, snapshot=snapshot)
    )
    assert "sf_exports" in sf and "Collector PID not recorded" in sf
    assert "unavailable" in sf
    state.handle_key("char:0")
    inbox = "\n".join(
        line.plain for line in _watch_lines(str(root), state, palette, None, snapshot=snapshot)
    )
    assert "Please inspect this competitor" in inbox and "waiting for agent" in inbox
    state.handle_key("enter")
    assert state.watch_detail_kind == "inbox"


def test_inbox_page_after_first_hundred_is_reachable(tmp_path):
    import json

    from seohead.projects.inbox import submit
    from seohead.projects.observer import observe

    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    submit(root, text="Note 0")
    path = root / "inbox.json"
    document = json.loads(path.read_text())
    first = document["entries"][0]
    document["entries"] = [
        {**first, "id": f"inbox:synthetic-{index}", "text": f"Note {index}"} for index in range(101)
    ]
    path.write_text(json.dumps(document))
    state = ShellState(commands=[], view="watch", watch_section="inbox", watch_offset=100)
    lines = _watch_lines(
        str(root), state, theme.resolve_palette(color=False), None, snapshot=observe(root)
    )
    assert "Note 100" in "\n".join(line.plain for line in lines)


def test_raw_input_preserves_terminal_newline_output_and_restores_attributes():
    master, slave = pty.openpty()
    try:
        original = termios.tcgetattr(slave)
        with raw_mode(slave):
            os.write(slave, b"first\nsecond\n")
            assert os.read(master, 64) == b"first\r\nsecond\r\n"
        restored = termios.tcgetattr(slave)
        # The kernel can mark pending input after a mode change; compare
        # configured flags rather than that transient status bit.
        restored[3] &= ~getattr(termios, "PENDIN", 0)
        original[3] &= ~getattr(termios, "PENDIN", 0)
        assert restored == original
    finally:
        os.close(master)
        os.close(slave)


def test_watch_frame_fits_viewport_with_long_evidence_rows(tmp_path):
    root = tmp_path / "project"
    create_project(root, "https://example.test/" + "long/" * 80)
    state = ShellState(commands=[], view="watch")
    for width, height in [(116, 28), (76, 22), (50, 12)]:
        console = Console(width=width, no_color=True)
        frame = build_frame(
            state,
            width=width,
            height=height,
            palette=theme.resolve_palette(color=False),
            project=str(root),
        )
        lines = console.render_lines(frame, console.options)
        assert len(lines) <= height
        assert all(sum(segment.cell_length for segment in line) <= width for line in lines)


def test_progress_gauges_show_declared_denominator_and_unknown_evidence():
    palette = theme.resolve_palette(color=False)
    assert "5 / 10  50%" in _meter("Scenarios", 5, 10, palette).plain
    assert "Not measured" in _meter("Sitemap", None, None, palette).plain
    assert "100 / 1,097  9%" in _meter("Discovered URL coverage", 100, 1097, palette).plain


def test_slow_evidence_read_does_not_block_keyboard_refresh(monkeypatch):
    started, release = Event(), Event()

    def slow_read(project):
        started.set()
        release.wait(2)
        return {"ready": True}, []

    monkeypatch.setattr("seohead.tui.app._watch_snapshot", slow_read)
    refresh = _ObserverRefresh("synthetic")
    try:
        start = time.monotonic()
        assert refresh.poll()[0] is None
        assert time.monotonic() - start < 0.1
        assert started.wait(1)
        assert refresh.poll()[0] is None
        state = ShellState(commands=[], view="watch")
        state.handle_key("char:n")
        state.handle_key("char:П")
        assert state.note_text == "П"
    finally:
        release.set()
        refresh.worker.join(2)
    assert refresh.poll()[0] == {"ready": True}


def test_evidence_detail_can_scroll_and_return_to_selected_scan():
    state = ShellState(commands=[], view="watch", watch_section="scans")
    state.handle_key("enter")
    state.handle_key("page_down")
    assert state.watch_detail_offset == 10
    state.handle_key("up")
    assert state.watch_detail_offset == 9
    state.handle_key("escape")
    assert state.view == "watch" and state.watch_section == "scans"


def test_note_draft_is_visible_before_background_evidence_finishes():
    state = ShellState(commands=[], view="note", note_text="Typed draft remains visible")
    console = Console(width=120, height=30, record=True, no_color=True)
    console.print(
        build_frame(
            state,
            width=120,
            height=30,
            palette=theme.resolve_palette(color=False),
            project="pending",
            snapshot_override=(None, []),
        )
    )
    rendered = console.export_text()
    assert "Typed draft remains visible" in rendered
    assert "Enter Save" in rendered


def test_task_page_keys_read_the_next_durable_checklist_page(tmp_path):
    from seohead.projects.coverage import initialize_coverage
    from seohead.projects.observer import observe
    from seohead.projects.progress import project_progress

    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    initialize_coverage(root)
    state = ShellState(commands=[], view="watch", watch_section="tasks")
    palette = theme.resolve_palette(color=False)
    snapshot = observe(root)
    first = _watch_lines(str(root), state, palette, None, snapshot=snapshot)
    state.handle_key("page_down")
    second = _watch_lines(str(root), state, palette, None, snapshot=snapshot)
    expected = project_progress(root, limit=50, offset=50)
    assert expected["items"][0]["id"] in second[4].plain
    assert first[4].plain != second[4].plain
    assert "page offset 50" in second[3].plain


def test_imported_scan_unknown_frontier_does_not_crash_dashboard(tmp_path, monkeypatch):
    from seohead.projects.observer import observe

    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    _finished(root / "scans" / "fixture.sqlite")
    snapshot = observe(root)
    snapshot["scans"]["items"][0]["evidence"]["frontier"] = {
        "state": "unavailable",
        "counts": None,
        "reason": "imported input has no frontier",
    }
    monkeypatch.setattr("seohead.tui.app._watch_snapshot", lambda _: (snapshot, []))
    console = Console(width=120, height=30, record=True)
    console.print(
        build_frame(
            ShellState(commands=[], view="watch"),
            width=120,
            height=30,
            palette=theme.resolve_palette(color=False),
            project=str(root),
        )
    )
    assert "Not measured" in console.export_text()


def test_dashboard_is_bounded_in_fullscreen_compact_and_each_section(tmp_path):
    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    _finished(root / "scans" / "synthetic.sqlite")
    for width, height in [(237, 68), (120, 30), (80, 22)]:
        for section in (
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
        ):
            state = ShellState(commands=[], view="watch", watch_section=section)
            console = Console(width=width, height=height, no_color=True)
            frame = build_frame(
                state,
                width=width,
                height=height,
                palette=theme.resolve_palette(color=False),
                project=str(root),
            )
            lines = console.render_lines(frame, console.options)
            assert len(lines) <= height
            assert all(sum(part.cell_length for part in line) <= width for line in lines)


def test_watch_state_can_filter_sort_page_and_open_a_retained_finding():
    state = ShellState(commands=[], view="watch")
    state.handle_key("char:5")
    assert state.watch_section == "findings"
    state.handle_key("char:f")
    assert state.view == "watch_filter"
    state.handle_key("char:д")
    state.handle_key("enter")
    assert state.view == "watch" and state.watch_query == "д"
    state.handle_key("char:s")
    assert state.watch_sort == "check"
    state.handle_key("char:r")
    assert state.watch_descending is True
    state.handle_key("page_down")
    assert state.watch_offset == 50
    state.watch_detail_ordinal = 7
    state.handle_key("enter")
    assert state.view == "watch_detail"
    state.handle_key("escape")
    assert state.view == "watch"


def test_watch_frame_browses_a_retained_finding_and_its_evidence(tmp_path):
    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    _finished(root / "scans" / "synthetic.sqlite")
    state = ShellState(commands=[], view="watch", watch_section="findings")
    palette = theme.resolve_palette(color=False)
    console = Console(no_color=True, width=100, record=True)
    console.print(
        build_frame(
            state,
            width=100,
            height=30,
            palette=palette,
            project=str(root),
        )
    )
    assert "findings" in console.export_text()
    assert state.watch_detail_ordinal is not None
    state.view = "watch_detail"
    console = Console(no_color=True, width=100, record=True)
    console.print(
        build_frame(
            state,
            width=100,
            height=30,
            palette=palette,
            project=str(root),
        )
    )
    assert "retained evidence" in console.export_text()
