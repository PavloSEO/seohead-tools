"""Denominator-backed scan summary using only the bounded project observation."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from rich.console import Group
from rich.panel import Panel
from rich.text import Text

from seohead.tui.labels import label
from seohead.tui.localization import ui


def project_directory(directory: str) -> str:
    path = Path(directory).expanduser()
    if not (path / "project.json").is_file() and (path / "project/project.json").is_file():
        path /= "project"
    return str(path)


def select(snapshot: dict, scan: str | None) -> dict:
    if not scan:
        return snapshot

    def matches(item):
        return scan in {str(item.get(k)) for k in ("uuid", "id", "scan_uuid", "run_id")}

    result = deepcopy(snapshot)
    scans = [item for item in result.get("scans", {}).get("items", []) if matches(item)]
    runs = [item for item in result.get("runs", {}).get("items", []) if matches(item)]
    if not scans and not runs:
        raise ValueError("selected scan is absent from the retained observation")
    result.setdefault("scans", {})["items"] = scans
    result.setdefault("runs", {})["items"] = runs
    result["selected_scan"] = scan
    return result


def summary(snapshot: dict | None) -> str:
    if snapshot is None:
        return ui("Loading…")
    runs = snapshot.get("runs", {}).get("items", [])
    run = next((r for r in runs if r.get("state") == "running"), runs[0] if runs else {})
    scans = snapshot.get("scans", {}).get("items", [])
    saved = scans[0] if scans else {}
    if not run and not saved:
        return ui("No scan recorded")
    frontier = saved.get("evidence", {}).get("frontier", {}).get("counts", {}) or {}
    counts = run.get("counters") or {"fetched": frontier.get("done"), **frontier}
    fetched = counts.get("fetched")
    queued, inflight = counts.get("queued"), counts.get("inflight")
    semantics = run.get("telemetry", {}).get(
        "queue_semantics", "separate" if frontier else "unknown"
    )
    population = [fetched, queued] + ([inflight] if semantics == "separate" else [])
    total = (
        sum(population)
        if semantics != "unknown" and all(type(v) is int for v in population)
        else None
    )
    progress = (
        f"{fetched} / {total} · {fetched / total:.0%}"
        if total
        else str(fetched)
        if fetched is not None
        else ui("Not measured")
    )
    identity = run.get("id") or run.get("uuid") or saved.get("uuid") or "—"
    unknown = ui("unknown")
    state = label(run.get("state") or saved.get("lifecycle"))
    freshness = label(run.get("telemetry", {}).get("state"))
    errors = counts.get("errors")
    if errors is None:
        errors = run.get("errors")
    if not isinstance(errors, (int, float)):
        errors = unknown
    return f"{identity} · {state} · {progress} · {ui('Queue')} {queued if queued is not None else unknown} · {ui('Excluded')} {counts.get('excluded', unknown)} · {ui('Errors')} {errors} · {freshness}"


def panel(snapshot: dict | None, palette, *, compact: bool = False):
    line = Text(summary(snapshot), style=palette.accent if palette.color else "")
    if compact:
        site = (snapshot or {}).get("project", {}).get("site", {})
        return Text(
            f"SEOHEAD · {site.get('label') or site.get('host') or ui('Project')} · {ui('Scan now')}: "
            + line.plain
        )
    return Panel(
        Group(line), title=ui("Scan now"), border_style=palette.accent if palette.color else "none"
    )
