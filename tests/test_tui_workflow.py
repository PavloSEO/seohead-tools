"""Human terminal workflows, including data boundaries hidden by crop-only tests."""

from __future__ import annotations

import time
from io import StringIO
from threading import Event

from rich.console import Console

from seohead.projects.coverage import coverage_status, initialize_coverage, update_item
from seohead.projects.observer import observe
from seohead.projects.workspace import create_project
from seohead.tui.app import (
    _ObserverRefresh,
    _page_selection,
    _read_view,
    _watch_detail_lines,
    build_frame,
)
from seohead.tui.state import ShellState
from seohead.tui.theme import resolve_palette
from tests.test_project_progress import _plan


def _project(tmp_path):
    root = tmp_path / "project"
    create_project(root, "https://example.test/")
    initialize_coverage(root, plan=_plan(["scenario:structured-data"]))
    update_item(
        root,
        {"id": "custom:late", "title": "Inspect the retained product sample", "order": 999},
        coverage_status(root)["revision"],
    )
    initialize_coverage(root, plan=_plan(["scenario:structured-data", "custom:late"]))
    return root


def _render(root, state, snapshot, *, width=80, height=24, data=None, message=None):
    console = Console(width=width, height=height, file=StringIO(), record=True, no_color=True)
    console.print(
        build_frame(
            state,
            width=width,
            height=height,
            palette=resolve_palette(color=False),
            project=str(root),
            snapshot_override=(snapshot, []),
            view_override=data,
            message=message,
        )
    )
    return console.export_text()


def test_editor_suspend_resume_cursor_and_explicit_discard_preserve_other_draft():
    state = ShellState([], view="watch")
    state.handle_key("char:n")
    for char in "Hello":
        state.handle_key("char:" + char)
    state.handle_key("home")
    state.handle_key("delete")
    assert state.note_text == "ello"
    state.handle_key("escape")
    state.handle_key("char:g")
    state.handle_key("char:G")
    state.handle_key("escape")
    state.handle_key("char:n")
    assert state.note_text == "ello"
    state.handle_key("ctrl_x")
    assert state.note_text == ""
    state.handle_key("escape")
    state.handle_key("char:g")
    assert state.note_text == "G"


def test_large_paste_keeps_every_character_and_refuses_oversized_submit():
    state = ShellState([], view="note")
    text = "Проверка\n" * 1200
    state.handle_key("paste_start")
    for char in text:
        state.handle_key("enter" if char == "\n" else "char:" + char)
    state.handle_key("paste_end")
    state.handle_key("enter")
    assert state.note_text == text
    assert not state.note_ready and state.view == "note"


def test_filter_cancel_and_last_page_navigation_are_not_mutations():
    state = ShellState([], view="watch", watch_section="tasks", watch_query="old")
    state.handle_key("char:f")
    state.handle_key("char:X")
    state.handle_key("escape")
    assert state.watch_query == "old"
    state.watch_count = 2
    state.watch_next_offset = None
    state.handle_key("page_down")
    state.handle_key("end")
    state.handle_key("down")
    assert state.watch_offset == 0 and state.watch_index == 1


def test_task_details_show_human_title_reason_and_definition(tmp_path):
    root = _project(tmp_path)
    state = ShellState([], view="watch", watch_section="tasks")
    state.watch_item_id = "custom:late"
    state.handle_key("enter")
    assert state.view == "watch_detail"
    snapshot = observe(root)
    data = _read_view(str(root), state, snapshot)
    lines = _watch_detail_lines(
        str(root), state, resolve_palette(color=False), snapshot=snapshot, data=data
    )
    text = "\n".join(line.plain for line in lines)
    assert "Inspect the retained product sample" in text
    assert "Scope" in text and "Definition" in text and "Latest attempt" in text


def test_refresh_preserves_selected_identity_without_overriding_arrow_navigation():
    state = ShellState([], view="watch", watch_section="tasks")
    items = [{"id": "a"}, {"id": "b"}]
    _page_selection(state, items, {"total": 2})
    state.handle_key("down")
    _page_selection(state, items, {"total": 2})
    assert state.watch_selected_row_id == "b"
    _page_selection(state, [{"id": "new"}, *items], {"total": 3})
    assert state.watch_index == 2 and state.watch_selected_row_id == "b"


def test_structured_data_view_uses_existing_checklist_and_agreed_meter(tmp_path):
    root = _project(tmp_path)
    state = ShellState([], view="watch")
    state.handle_key("char:a")
    snapshot = observe(root)
    page = _read_view(str(root), state, snapshot)
    assert any(item["id"] == "scenario:structured-data" for item in page["items"])
    assert all(
        "schema" in str(item).lower() or "structured" in str(item).lower() for item in page["items"]
    )
    text = _render(root, ShellState([], view="watch"), snapshot, width=140, height=40, data={})
    assert "0 / 1" in text
    assert "None in agreed scope" in text


def test_cached_render_does_not_read_storage_and_receipt_is_visible(tmp_path, monkeypatch):
    root = _project(tmp_path)
    snapshot = observe(root)

    def forbidden(*args, **kwargs):
        raise AssertionError("render performed storage I/O")

    monkeypatch.setattr("seohead.tui.app._read_view", forbidden)
    monkeypatch.setattr("seohead.tui.app._watch_snapshot", forbidden)
    for width, height in ((80, 24), (120, 32), (237, 68)):
        text = _render(
            root,
            ShellState([], view="watch"),
            snapshot,
            width=width,
            height=height,
            data={},
            message="Note saved locally: receipt 123",
        )
        assert "Note saved locally: receipt 123" in text
        assert "Inspect the retained product sample" in text


def test_slow_page_reader_never_blocks_poll_or_editor(tmp_path, monkeypatch):
    root = _project(tmp_path)
    snapshot = observe(root)
    started, release = Event(), Event()
    monkeypatch.setattr("seohead.tui.app._watch_snapshot", lambda _: (snapshot, []))

    def slow(*args):
        started.set()
        release.wait(2)
        return {"items": []}

    monkeypatch.setattr("seohead.tui.app._read_view", slow)
    state = ShellState([], view="watch", watch_section="tasks")
    reader = _ObserverRefresh(str(root))
    try:
        reader.poll(state)
        assert started.wait(1)
        start = time.monotonic()
        reader.poll(state)
        state.handle_key("char:n")
        state.handle_key("char:П")
        assert time.monotonic() - start < 0.1
        assert state.note_text == "П"
    finally:
        release.set()
        reader.worker.join(2)


def test_explicit_note_write_is_off_input_loop_and_receipt_is_durable(tmp_path, monkeypatch):
    from seohead.projects.inbox import list_entries, submit

    root = _project(tmp_path)
    started, release = Event(), Event()

    def slow_submit(*args, **kwargs):
        started.set()
        release.wait(2)
        return submit(*args, **kwargs)

    monkeypatch.setattr("seohead.projects.inbox.submit", slow_submit)
    state = ShellState([], view="watch", note_text="Keep the product review evidence")
    reader = _ObserverRefresh(str(root))
    try:
        start = time.monotonic()
        reader.save(state)
        reader.poll(state)
        assert time.monotonic() - start < 0.1 and started.wait(1)
        assert reader.saving
    finally:
        release.set()
        reader.worker.join(2)
    reader.poll(state)
    assert reader.saved and not reader.saving
    assert "saved" in reader.saved[1]
    assert list_entries(root, consumer="test")["entries"][0]["text"] == state.note_text


def test_note_from_task_context_saves_the_exact_selected_task_reference(tmp_path):
    from seohead.projects.inbox import list_entries
    from seohead.tui.app import _save_note

    root = _project(tmp_path)
    state = ShellState(
        [],
        view="watch",
        watch_section="tasks",
        watch_item_id="custom:late",
        note_text="Please verify this task's retained evidence",
    )
    message = _save_note(str(root), state)
    assert "saved" in message and not state.note_error
    assert "task:custom:late" in list_entries(root, consumer="test")["entries"][0]["references"]
