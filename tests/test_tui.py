"""The observer shell remains an optional, non-executing terminal adapter."""

from __future__ import annotations

import os
import pty
import termios

from rich.console import Console

from seohead import cli
from seohead.projects.workspace import create_project
from seohead.tui import theme
from seohead.tui.app import MIN_HEIGHT, MIN_WIDTH, build_frame
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
