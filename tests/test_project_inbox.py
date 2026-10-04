"""Durable observer inbox: no action is implied by a notification."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from multiprocessing import get_context

import pytest

from seohead.projects.inbox import (
    acknowledge,
    fingerprint,
    list_entries,
    mark_read,
    set_goal_state,
    submit,
    unread_summary,
)
from seohead.projects.observer import finding_detail, findings_page, observe
from seohead.projects.workspace import create_project
from seohead.servers.mcp_server import build_server
from tests.test_scan_history import _finished


def _project(tmp_path):
    root = tmp_path / "synthetic-project"
    create_project(root, "https://example.test/", label="Synthetic observer fixture")
    return root


def _separate_writer(directory: str) -> None:
    submit(directory, text="Written by a separate collector", references=["scan:synthetic-process"])


def _bound_mcp_observer(directory: str, consumer: str, allowlist: str, queue) -> None:
    """Spawn target: each stdio host reads only its own startup binding."""
    os.environ["SEOHEAD_MCP_CONSUMER_ID"] = consumer
    os.environ["SEOHEAD_MCP_PROJECT_ALLOWLIST"] = allowlist
    server = build_server()
    result = server._tool_manager.get_tool("seo_project_observe").fn(directory=directory)
    queue.put(result.get("inbox_unread"))


def test_note_read_ack_and_goal_transitions_are_explicit_and_durable(tmp_path):
    root = _project(tmp_path)
    note = submit(root, text="Review sitemap gap", references=["scan:synthetic-1"])["entry"]
    goal = submit(root, text="Compare the named competitor", kind="proposed_goal")["entry"]

    before = unread_summary(root, consumer="agent/session-a")
    assert before["count"] == 2
    inspected = mark_read(root, consumer="agent/session-a", entry_ids=[note["id"]])
    assert inspected["entries"][0]["read_at"] and inspected["entries"][0]["acknowledged_at"] is None
    assert unread_summary(root, consumer="agent/session-a")["count"] == 2
    acknowledged = acknowledge(root, consumer="agent/session-a", entry_ids=[note["id"]])
    assert acknowledged["entries"][0]["acknowledged_at"]
    assert unread_summary(root, consumer="agent/session-a")["count"] == 1
    assert (
        set_goal_state(root, entry_id=goal["id"], state="accepted")["entry"]["goal_state"]
        == "accepted"
    )
    assert (
        set_goal_state(root, entry_id=goal["id"], state="completed")["entry"]["goal_state"]
        == "completed"
    )
    with pytest.raises(ValueError, match="proposed goal"):
        set_goal_state(root, entry_id=note["id"], state="accepted")


def test_concurrent_observer_submissions_are_not_lost_and_read_only_is_stable(tmp_path):
    root = _project(tmp_path)
    scan_hash = fingerprint(root)

    def write(index):
        return submit(root, text=f"Synthetic note {index}", references=["section:coverage"])[
            "entry"
        ]["id"]

    with ThreadPoolExecutor(max_workers=4) as executor:
        ids = list(executor.map(write, range(8)))

    listed = list_entries(root, consumer="agent/session-a", limit=20)
    assert {entry["id"] for entry in listed["entries"]} == set(ids)
    assert listed["pagination"]["total"] == 8
    assert fingerprint(root) != scan_hash
    again = fingerprint(root)
    unread_summary(root, consumer="agent/session-a")
    assert fingerprint(root) == again


def test_separate_process_handoff_survives_writer_exit(tmp_path):
    root = _project(tmp_path)
    writer = get_context("spawn").Process(target=_separate_writer, args=(str(root),))
    writer.start()
    writer.join(timeout=15)
    assert writer.exitcode == 0

    handed_off = list_entries(root, consumer="agent/replacement")
    assert handed_off["entries"][0]["text"] == "Written by a separate collector"
    assert unread_summary(root, consumer="agent/replacement")["count"] == 1


def test_mcp_progress_returns_notice_without_consuming_it(tmp_path):
    root = _project(tmp_path)
    entry = submit(root, text="Keep this visible", references=["finding:synthetic-1"])["entry"]
    server = build_server()
    progress = server._tool_manager.get_tool("seo_project_progress")
    result = progress.fn(directory=str(root), consumer="agent/session-a")
    assert result["inbox_unread"] == {
        "count": 1,
        "entries": [{"id": entry["id"], "kind": "note", "references": ["finding:synthetic-1"]}],
        "truncated": False,
    }
    assert unread_summary(root, consumer="agent/session-a")["count"] == 1


def test_project_bound_mcp_work_call_gets_only_its_consumer_notice(tmp_path):
    root = _project(tmp_path)
    submit(root, text="Scoped note")
    server = build_server()
    facts = server._tool_manager.get_tool("seo_project_facts")
    result = facts.fn(
        directory=str(root),
        facts=[{"name": "cms", "value": "fixture", "provenance": "operator", "observed_at": None}],
        consumer="agent/session-a",
    )
    assert result["inbox_unread"]["count"] == 1
    assert unread_summary(root, consumer="agent/other-session")["count"] == 1
    assert unread_summary(root, consumer="agent/session-a")["count"] == 1


def test_observer_snapshot_keeps_missing_work_and_logs_visible(tmp_path):
    root = _project(tmp_path)
    (root / "log.md").write_text("# Project log\n\nSynthetic handoff evidence\n", encoding="utf-8")
    submit(root, text="Review missing competitor", kind="proposed_goal")

    result = observe(root, consumer="agent/session-a")

    assert result["progress"]["audit_complete"] is False
    assert result["preparation"]["state"] == "pending"
    assert result["execution"]["runs"] == []
    assert result["monitor"]["policy"] is None and result["monitor"]["last_run"] is None
    assert result["scans"]["total"] == 0
    assert result["inbox_unread"]["count"] == 1
    assert "Synthetic handoff evidence" in result["log"]["text"]


def test_utf8_dictation_text_survives_handoff_and_restart(tmp_path):
    root = _project(tmp_path)
    text = "Проверь, почему сценарий и конкурент ещё не пройдены"
    entry = submit(root, text=text, kind="proposed_goal")["entry"]

    # A new reader process would reload inbox.json through this public API.
    restored = list_entries(root, consumer="agent/next-session")["entries"]
    assert restored[0]["id"] == entry["id"]
    assert restored[0]["text"] == text


def test_observer_reads_retained_frontier_findings_and_sitemap_without_mutation(tmp_path):
    root = _project(tmp_path)
    scan = root / "scans" / "synthetic.sqlite"
    _finished(scan)
    before = scan.read_bytes()

    result = observe(root)

    evidence = result["scans"]["items"][0]["evidence"]
    assert evidence["state"] == "available"
    assert evidence["frontier"]["state"] == "available"
    assert evidence["findings"]["total"] >= 0
    assert len(evidence["findings"]["items"]) <= 20
    assert "fetch_summaries" in evidence["sitemaps"]
    assert scan.read_bytes() == before


def test_observer_finding_browser_filters_sorts_pages_and_keeps_scan_read_only(tmp_path):
    root = _project(tmp_path)
    scan = root / "scans" / "synthetic.sqlite"
    _finished(scan)
    before = scan.read_bytes()

    unfiltered = findings_page(root, limit=2, sort="target_url", descending=True)
    assert unfiltered["counts"]["source"] >= 1
    needle = unfiltered["items"][0]["check"]
    page = findings_page(root, query=needle, limit=2, sort="target_url", descending=True)
    assert page["counts"]["matched"] >= 1
    assert page["counts"]["returned"] <= 2
    detail = finding_detail(root, ordinal=page["items"][0]["ordinal"])
    assert detail["finding"]["check"] == needle
    assert detail["evidence"]["check"] == needle
    assert scan.read_bytes() == before


def test_host_consumer_binding_is_project_scoped_and_two_servers_do_not_share_it(
    tmp_path, monkeypatch
):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = _project(tmp_path / "one")
    second = _project(tmp_path / "two")
    submit(first, text="First project handoff")
    submit(second, text="Second project handoff")

    monkeypatch.setenv("SEOHEAD_MCP_CONSUMER_ID", "agent/first")
    monkeypatch.setenv("SEOHEAD_MCP_PROJECT_ALLOWLIST", str(first))
    first_server = build_server()
    observe_tool = first_server._tool_manager.get_tool("seo_project_observe")
    first_result = observe_tool.fn(directory=str(first))
    outside_result = observe_tool.fn(directory=str(second))
    assert first_result["inbox_unread"]["count"] == 1
    assert "inbox_unread" not in outside_result
    assert unread_summary(first, consumer="agent/first")["count"] == 1

    monkeypatch.setenv("SEOHEAD_MCP_CONSUMER_ID", "agent/second")
    monkeypatch.setenv("SEOHEAD_MCP_PROJECT_ALLOWLIST", str(second))
    second_server = build_server()
    second_result = second_server._tool_manager.get_tool("seo_project_observe").fn(
        directory=str(second)
    )
    assert second_result["inbox_unread"]["count"] == 1
    assert unread_summary(second, consumer="agent/second")["count"] == 1
    assert unread_summary(first, consumer="agent/first")["count"] == 1
    assert os.environ["SEOHEAD_MCP_CONSUMER_ID"] == "agent/second"


def test_restart_isolation_keeps_each_host_bound_to_its_own_project(tmp_path):
    (tmp_path / "one").mkdir()
    (tmp_path / "two").mkdir()
    first = _project(tmp_path / "one")
    second = _project(tmp_path / "two")
    submit(first, text="First process note")
    submit(second, text="Second process note")
    context = get_context("spawn")
    queue = context.Queue()
    first_process = context.Process(
        target=_bound_mcp_observer,
        args=(str(first), "agent/first-process", str(first), queue),
    )
    second_process = context.Process(
        target=_bound_mcp_observer,
        args=(str(second), "agent/second-process", str(second), queue),
    )
    first_process.start()
    second_process.start()
    first_process.join(timeout=20)
    second_process.join(timeout=20)
    assert first_process.exitcode == second_process.exitcode == 0
    notices = [queue.get(timeout=5), queue.get(timeout=5)]
    assert all(notice["count"] == 1 for notice in notices)
    assert unread_summary(first, consumer="agent/first-process")["count"] == 1
    assert unread_summary(second, consumer="agent/second-process")["count"] == 1
