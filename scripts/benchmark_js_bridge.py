#!/usr/bin/env python3
"""Opt-in owned-origin HTML/JavaScript benchmark with frozen resource budgets.

The supervisor uses the platform's numeric ps inventory, not psutil or browser
installation. Every browser is the real renderer; hooks only observe phases and
raise a graceful KeyboardInterrupt after a committed document for recovery.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROFILE = ROOT / "examples" / "js-bridge-benchmark.v1.json"
MIB = 1024 * 1024


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(MIB), b""):
            h.update(chunk)
    return h.hexdigest()


def disk_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file() and not p.is_symlink())


def source_identity() -> dict:
    import importlib.metadata

    import seohead

    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    dirty = subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip()
    try:
        playwright_version = importlib.metadata.version("playwright")
    except importlib.metadata.PackageNotFoundError:
        playwright_version = "unavailable"
    try:
        available_memory = os.sysconf("SC_AVPHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
    except (AttributeError, ValueError, OSError):
        available_memory = None
    chrome = Path(os.environ["SEOHEAD_CHROME"]) if os.environ.get("SEOHEAD_CHROME") else None
    return {
        "browser_sha256": digest(chrome) if chrome is not None and chrome.is_file() else None,
        "available_memory_bytes": available_memory,
        "available_memory_note": "OS available-page estimate; unavailable on platforms without SC_AVPHYS_PAGES",
        "load_average": list(os.getloadavg()) if hasattr(os, "getloadavg") else None,
        "revision": revision,
        "dirty": bool(dirty),
        "loaded_module": str(Path(seohead.__file__).resolve()),
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "platform": platform.platform(),
        "machine": platform.machine(),
        "logical_cpus": os.cpu_count(),
        "physical_memory_bytes": os.sysconf("SC_PHYS_PAGES") * os.sysconf("SC_PAGE_SIZE")
        if hasattr(os, "sysconf")
        else None,
        "playwright": playwright_version,
        "profile_sha256": digest(PROFILE),
        "harness_sha256": digest(Path(__file__)),
        "modules": {
            name: digest(ROOT / name)
            for name in (
                "seohead/tools/render.py",
                "seohead/crawl/sqlite_render.py",
                "seohead/crawl/render_escalation.py",
                "seohead/servers/scan_handlers.py",
                "seohead/crawl/settings.py",
                "seohead/storage/native_scan.py",
            )
        },
    }


def cases(profile: dict) -> list[dict]:
    return [
        {
            "mode": mode,
            "pages": pages,
            "links": links,
            "forms": profile["forms_per_page"],
            "repetition": repetition,
            "render_limit": pages,
        }
        for mode in ("html", "javascript")
        for pages in profile["page_counts"]
        for links in profile["link_densities"]
        for repetition in range(1, 4)
    ]


class Origin:
    """An owned HTTP origin; the JS version returns a shell, never a fake DOM."""

    def __init__(self, pages: int, links: int, forms: int, mode: str, dom_bytes: int = 16384):
        if not 1 <= pages <= 10001 or not 0 <= links <= 64 or not 0 <= forms <= 128:
            raise ValueError("fixture dimensions exceed the approved bounds")
        self.pages, self.links, self.forms, self.mode, self.dom_bytes = (
            pages,
            links,
            forms,
            mode,
            dom_bytes,
        )
        self.requests = Counter()
        self.unexpected = Counter()
        self.server = None

    def body(self, ordinal: int) -> str:
        # Distinct href fragments preserve the declared occurrence density even
        # when the small 32-page corpus has 64 link occurrences per page.
        links = "".join(
            f'<a href="/p/{(ordinal + n) % self.pages}#link-{n}">Link {n}</a>'
            for n in range(self.links)
        )
        forms = "".join(
            f'<form action="/submit/{ordinal}/{n}" method="post"><input name="n"></form>'
            for n in range(self.forms)
        )
        return f'<main data-benchmark="rendered"><h1>Owned page {ordinal}</h1>{links}{forms}<p>Deterministic corpus content for page {ordinal}.</p></main>'

    def page(self, ordinal: int) -> bytes:
        body = self.body(ordinal)
        if self.mode == "html":
            html = f'<!doctype html><html><head><title>Owned page {ordinal}</title><meta charset="utf-8"></head><body>{body}<div id="pad"></div></body></html>'
            return html.replace(
                '<div id="pad"></div>',
                '<div id="pad">' + "x" * max(0, self.dom_bytes - len(html.encode())) + "</div>",
            ).encode()
        # Remove the executing script before serialization so only observed DOM
        # remains. Padding targets the same serialized size across densities.
        script = (
            f"document.body.innerHTML={json.dumps(body + '<div id=pad></div>')};"
            f"document.title='Owned page {ordinal}';"
            f"document.querySelector('#pad').textContent='x'.repeat(Math.max(0,{self.dom_bytes}-new TextEncoder().encode('<!DOCTYPE html>'+document.documentElement.outerHTML).length));"
        )
        return f'<!doctype html><html><head><title>Owned page {ordinal}</title><meta charset="utf-8"></head><body><div id="root"></div><script>{script}</script></body></html>'.encode()

    def __enter__(self):
        origin = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                path = urlsplit(self.path).path
                origin.requests[path] += 1
                if path == "/robots.txt":
                    body, content = b"User-agent: *\nAllow: /\n", "text/plain"
                elif path == "/sitemap.xml":
                    body = (
                        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
                        + "".join(
                            f"<url><loc>{origin.url}/p/{n}</loc></url>" for n in range(origin.pages)
                        )
                        + "</urlset>"
                    ).encode()
                    content = "application/xml"
                elif path.startswith("/p/") and path[3:].isdigit() and int(path[3:]) < origin.pages:
                    body, content = origin.page(int(path[3:])), "text/html; charset=utf-8"
                elif path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                else:
                    origin.unexpected[path] += 1
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                origin.unexpected["POST " + self.path] += 1
                self.send_error(405)

            def log_message(self, *_args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        return self

    def __exit__(self, *_args):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class GracefulPause(KeyboardInterrupt):
    """Leave committed evidence and elapsed render budget intact."""


def evidence(path: Path, case: dict, expected_rendered: int) -> dict:
    """Stream exact typed observations and selected DOM hashes, not timing fields."""
    from seohead.storage import open_scan
    from seohead.storage.audit_v2 import AuditV2Reader
    from seohead.storage.bodies import read_document
    from seohead.tools.parser import parse_html

    con = open_scan(path)
    try:
        counts = {
            table: con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            for table in ("pages", "links", "forms")
        }
        reps = dict(
            con.execute("SELECT representation,COUNT(*) FROM pages GROUP BY representation")
        )
        population = case["pages"] if case["mode"] == "html" else expected_rendered
        assert counts["pages"] == case["pages"], counts
        assert (
            con.execute(
                "SELECT COUNT(*) FROM pages WHERE status_code != 200 OR status_code IS NULL"
            ).fetchone()[0]
            == 0
        )
        assert counts["forms"] == population * case["forms"], counts
        assert counts["links"] == population * case["links"], counts
        assert reps.get("rendered", 0) == expected_rendered, reps
        hashes = {}
        for name, sql in {
            "links": "SELECT s.url,d.url,l.anchor,l.ordinal,l.evidence_representation,l.raw_href,l.nofollow,l.position,l.rel_json,l.target FROM links l JOIN urls s ON s.url_id=l.source_url_id JOIN urls d ON d.url_id=l.destination_url_id ORDER BY s.url,l.evidence_representation,l.ordinal,l.link_id",
            "forms": "SELECT u.url,f.action,f.method,f.has_password,f.ordinal,f.evidence_representation FROM forms f JOIN urls u ON u.url_id=f.page_url_id ORDER BY u.url,f.evidence_representation,f.ordinal,f.form_id",
        }.items():
            h = hashlib.sha256()
            for row in con.execute(sql):
                ordinal = int(urlsplit(row[0]).path.rsplit("/", 1)[-1])
                if name == "links":
                    assert row[2] == f"Link {row[3]}"
                    assert urlsplit(row[1]).path == f"/p/{(ordinal + row[3]) % case['pages']}"
                    assert row[5] == f"/p/{(ordinal + row[3]) % case['pages']}#link-{row[3]}"
                else:
                    assert row[2] == "post" and row[3] == 0
                    assert urlsplit(row[1]).path == f"/submit/{ordinal}/{row[4]}"
                h.update(json.dumps(tuple(row), ensure_ascii=False).encode() + b"\n")
            hashes[name] = h.hexdigest()
        page_hash = hashlib.sha256()
        for row in con.execute(
            "SELECT p.*,u.url FROM pages p JOIN urls u USING(url_id) ORDER BY p.page_ordinal"
        ):
            data = dict(row)
            ordinal = int(urlsplit(data["url"]).path.rsplit("/", 1)[-1])
            assert data["title"] == f"Owned page {ordinal}"
            if data["representation"] == "rendered" or case["mode"] == "html":
                assert data["h1"] == f"Owned page {ordinal}"
            for key in ("url_id", "document_id", "response_time"):
                data.pop(key, None)
            page_hash.update(json.dumps(data, sort_keys=True, ensure_ascii=False).encode() + b"\n")
        hashes["pages"] = page_hash.hexdigest()
        dom_hashes = []
        for row in con.execute(
            "SELECT d.document_id,u.url,d.body_sha256 FROM pages p JOIN documents d ON d.document_id=p.document_id JOIN urls u ON u.url_id=p.url_id WHERE p.representation='rendered' ORDER BY u.url"
        ):
            html = read_document(con, row[0], max_decoded_bytes=1024 * 1024)
            parsed = parse_html(html, row[1])
            assert len(parsed.get("forms", [])) == case["forms"]
            assert abs(len(html.encode()) - 16384) <= 512
            assert 'data-benchmark="rendered"' in html
            dom_hashes.append((row[1], row[2]))
        render_row = con.execute(
            "SELECT payload_json FROM context_items WHERE kind='render_elapsed' AND item_key='run'"
        ).fetchone()
        with AuditV2Reader(path) as audit:
            render_summary = audit.header["run"].get("render_escalation", {})
        if case["mode"] == "javascript":
            assert render_summary.get("render_requests") == expected_rendered, render_summary
            assert sum(render_summary.get("render_counts", {}).values()) == expected_rendered
            if expected_rendered < case["pages"]:
                assert render_summary.get("render_budget_exhausted") is True
                assert render_summary.get("patterns_partially_rendered")
            else:
                assert not render_summary.get("render_budget_exhausted")
                assert not render_summary.get("time_budget_exhausted")
        return {
            "render_summary": render_summary,
            "counts": counts,
            "representations": reps,
            "ordered_evidence_sha256": hashes,
            "selected_dom_sha256": dom_hashes,
            "render_elapsed": json.loads(render_row[0]) if render_row else None,
            "renderer_versions": [
                tuple(row)
                for row in con.execute(
                    "SELECT DISTINCT json_extract(renderer_json,'$.engine'),json_extract(renderer_json,'$.engine_version') FROM documents WHERE representation='rendered' ORDER BY 1,2"
                )
            ],
        }
    finally:
        con.close()


def worker(config: dict, output: Path) -> None:
    import resource

    from seohead.servers import handlers, scan_handlers
    from seohead.storage.native_scan import NativeScan

    profile = json.loads(PROFILE.read_text())
    identity = source_identity()
    if (
        not config.get("source_revision")
        or identity["revision"] != config["source_revision"]
        or identity["dirty"]
    ):
        raise RuntimeError("benchmark source changed after freeze")
    for key, expected in config.get("runtime_identity", {}).items():
        if identity.get(key) != expected:
            raise RuntimeError(f"benchmark runtime changed after freeze: {key}")
    events = output / "events.jsonl"
    phase = "setup"
    case_name = "setup"
    cancelled = False
    committed = 0

    def emit(name: str, **values):
        own = resource.getrusage(resource.RUSAGE_SELF)
        children = resource.getrusage(resource.RUSAGE_CHILDREN)
        values["cpu_self_waited_children_seconds"] = (
            own.ru_utime + own.ru_stime + children.ru_utime + children.ru_stime
        )
        with events.open("a") as stream:
            stream.write(
                json.dumps({"time": time.monotonic(), "case": case_name, "phase": name, **values})
                + "\n"
            )

    def observe(name):
        nonlocal phase
        phase = {"analysis": "audit"}.get(name, name)
        emit(phase)

    original_crawl = scan_handlers.crawl_site_scan
    original_commit = NativeScan.commit_render

    def instrumented_crawl(*args, **kwargs):
        kwargs["observation"] = observe
        return original_crawl(*args, **kwargs)

    def committed_render(scan, *args, **kwargs):
        nonlocal committed, cancelled
        document = original_commit(scan, *args, **kwargs)
        if len(args) > 1 and args[1] is not None:
            committed += 1
            emit(phase, event="render_committed", document_id=document)
            if case_name == "recovery" and not cancelled and committed == config["pages"] // 2:
                cancelled = True
                raise GracefulPause("planned graceful pause after committed rendered document")
        return document

    overrides = {
        "limits.max_urls": config["pages"],
        "limits.max_requests": config["pages"] * 8 + 100,
        "speed.min_delay_seconds": 0,
        "speed.concurrency": 1,
        "speed.adaptive": False,
        "link_position.classify": True,
        "link_attributes.capture": True,
        "http.timeout_seconds": 5,
        "sitemaps.auto_discover": False,
        "cache.mode": "off",
        "storage.format_version": "scan.v2",
        "storage.body_mode": "captured_entity_bytes",
        "rendering.mode": "js" if config["mode"] == "javascript" else "raw",
        "rendering.escalation.policy": "full",
        "rendering.escalation.max_render_urls": config["render_limit"],
        "rendering.escalation.max_render_seconds": profile["budgets"]["render_wall_seconds"],
        "rendering.rendered_links.crawl": True,
        "rendering.browser.page_concurrency": 1,
        "rendering.browser.script_timeout_seconds": 0,
        "rendering.artifacts.screenshots": False,
        "rendering.artifacts.console_errors": False,
    }
    results = []
    # One fresh worker is one cold/warm pair; the origin stays identical.
    with (
        patch.dict(os.environ, {"SEOHEAD_ALLOW_PRIVATE_HOSTS": "127.0.0.1"}),
        Origin(config["pages"], config["links"], config["forms"], config["mode"]) as origin,
        patch.object(scan_handlers, "crawl_site_scan", instrumented_crawl),
        patch.object(NativeScan, "commit_render", committed_render),
    ):
        names = config.get("temperatures", ["application_cold", "warmed_worker_repeat"])
        if config.get("recovery"):
            names.append("recovery")
        for case_name in names:
            target = output / case_name
            target.mkdir()
            scan = target / "scan.seohead"
            committed = 0
            emit("setup", event="case_start")
            start = time.monotonic()
            usage_before = resource.getrusage(resource.RUSAGE_SELF)
            child_before = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_before = (
                usage_before.ru_utime
                + usage_before.ru_stime
                + child_before.ru_utime
                + child_before.ru_stime
            )
            paused_elapsed = None
            try:
                response = handlers.crawl_site(
                    url=origin.url + "/p/0",
                    sitemap=origin.url + "/sitemap.xml",
                    scan_out=str(scan),
                    producer_build=identity["revision"],
                    overrides=overrides,
                )
            except GracefulPause:
                con = sqlite3.connect(scan)
                try:
                    saved = json.loads(
                        con.execute(
                            "SELECT payload_json FROM context_items WHERE kind='render_elapsed' AND item_key='run'"
                        ).fetchone()[0]
                    )
                    paused_docs = con.execute(
                        "SELECT COUNT(*) FROM documents WHERE representation='rendered'"
                    ).fetchone()[0]
                finally:
                    con.close()
                assert (
                    saved["active"] is False
                    and 0 < saved["seconds"] < profile["budgets"]["render_wall_seconds"]
                )
                assert paused_docs == config["pages"] // 2
                paused_elapsed = saved["seconds"]
                emit("resume", event="graceful_checkpoint", elapsed=saved, documents=paused_docs)
                response = handlers.crawl_site(
                    resume=str(scan), producer_build=identity["revision"]
                )
            if not response.get("audit_available"):
                raise AssertionError("the public native workflow did not produce an audit")
            expected = config["render_limit"] if config["mode"] == "javascript" else 0
            observe("query_report")
            measured = evidence(scan, config, expected)
            source_digest = digest(scan)
            companion = scan.with_name(scan.name + ".audit-v2.sqlite")
            audit_digest = digest(companion)
            # Registered shared readers/exporters exercise retained contracts.
            inspected = handlers.scan_inspect(str(scan), limit=2)
            exported = handlers.scan_export(
                str(scan),
                str(target / "pages.csv"),
                format="csv",
                records=["pages"],
                fields={"pages": ["url", "title", "representation"]},
            )
            observe("reanalysis")
            derived = target / "reanalysis.seohead"
            reanalysis = handlers.scan_reanalyze(
                str(scan), str(derived), producer_build=identity["revision"]
            )
            repeated = evidence(derived, config, expected)
            assert repeated["ordered_evidence_sha256"] == measured["ordered_evidence_sha256"]
            assert repeated["selected_dom_sha256"] == measured["selected_dom_sha256"]
            assert digest(scan) == source_digest and digest(companion) == audit_digest
            if case_name == "recovery":
                reference = results[-1]["evidence"]
                assert measured["render_elapsed"]["seconds"] >= paused_elapsed
                assert (
                    measured["render_elapsed"]["seconds"]
                    <= profile["budgets"]["render_wall_seconds"]
                )
                assert measured["render_elapsed"]["active"] is False
                assert measured["ordered_evidence_sha256"] == reference["ordered_evidence_sha256"]
                assert measured["selected_dom_sha256"] == reference["selected_dom_sha256"]
                assert measured["render_summary"] == reference["render_summary"]
                assert committed == config["pages"], (
                    "resume rerendered or omitted committed documents"
                )
            usage_after = resource.getrusage(resource.RUSAGE_SELF)
            child_after = resource.getrusage(resource.RUSAGE_CHILDREN)
            cpu_used = (
                usage_after.ru_utime
                + usage_after.ru_stime
                + child_after.ru_utime
                + child_after.ru_stime
                - cpu_before
            )
            assert cpu_used <= profile["budgets"]["cpu_seconds"], "case CPU budget exceeded"
            assert not origin.unexpected, dict(origin.unexpected)
            result = {
                "unexpected_http_requests": dict(origin.unexpected),
                "cpu_self_and_waited_children_seconds": cpu_used,
                "graceful_checkpoint_elapsed_seconds": paused_elapsed,
                "case": case_name,
                "status": "passed",
                "elapsed_seconds": time.monotonic() - start,
                "evidence": measured,
                "urls_per_second": config["pages"] / (time.monotonic() - start),
                "scan_sha256": source_digest,
                "audit_sha256": audit_digest,
                "rendered_population": expected,
                "static_population": config["pages"] - expected,
                "unrendered_by_budget": config["mode"] == "javascript"
                and expected < config["pages"],
                "inspect_ok": not inspected.get("error"),
                "export": exported,
                "reanalysis_ok": reanalysis.get("ok", True),
            }
            results.append(result)
            (target / "result.json").write_text(json.dumps(result, indent=2, default=str) + "\n")
            emit("done", event="case_end")
    usage = resource.getrusage(resource.RUSAGE_SELF)
    child = resource.getrusage(resource.RUSAGE_CHILDREN)
    (output / "result.json").write_text(
        json.dumps(
            {
                "status": "passed",
                "identity": identity,
                "configuration": config,
                "overrides": overrides,
                "cases": results,
                "cpu_self_and_waited_children_seconds": usage.ru_utime
                + usage.ru_stime
                + child.ru_utime
                + child.ru_stime,
                "python_process_highwater_rss_mib": usage.ru_maxrss
                / (MIB if sys.platform == "darwin" else 1024),
            },
            indent=2,
            default=str,
        )
        + "\n"
    )


def cpu_seconds(value: str) -> float:
    days = 0
    if "-" in value:
        left, value = value.split("-", 1)
        days = int(left)
    parts = [float(x) for x in value.split(":")]
    return days * 86400 + sum(x * 60**i for i, x in enumerate(reversed(parts)))


def process_sample(root_pid: int, known: set[int]) -> dict:
    raw = subprocess.check_output(["ps", "-axo", "pid=,ppid=,rss=,time="], text=True)
    rows = {}
    for line in raw.splitlines():
        pid, parent, rss, cpu = line.split()
        rows[int(pid)] = (int(parent), int(rss) * 1024, cpu_seconds(cpu))
    owned = {pid for pid in known if pid in rows} | ({root_pid} if root_pid in rows else set())
    while True:
        added = {pid for pid, (parent, *_) in rows.items() if parent in owned} - owned
        if not added:
            break
        owned.update(added)
    known.update(owned)
    return {
        "rss_bytes": sum(rows[pid][1] for pid in owned),
        "python_rss_bytes": rows.get(root_pid, (0, 0, 0))[1],
        "cpu_seconds": {str(pid): rows[pid][2] for pid in owned},
        "owned_processes": len(owned),
    }


def supervise(config: dict, output: Path, suite_root: Path, profile: dict) -> dict:
    output.mkdir()
    (output / "configuration.json").write_text(json.dumps(config, indent=2) + "\n")
    budgets = profile["budgets"]
    with (
        (output / "stdout.log").open("w") as stdout,
        (output / "stderr.log").open("w") as stderr,
        (output / "samples.jsonl").open("w") as samples,
    ):
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "_worker", "--out", str(output)],
            cwd=ROOT,
            env={**os.environ, "PYTHONPATH": str(ROOT), "SEOHEAD_CHROME": config["browser_path"]},
            stdout=stdout,
            stderr=stderr,
            start_new_session=True,
        )
        known = set()
        cpu = {}
        cpu_starts = {}
        phase = "setup"
        case = "setup"
        last = time.monotonic()
        totals = Counter()
        case_totals = Counter()
        peaks = {}
        event_offset = 0
        failure = ""
        peak_disk = 0
        try:
            while process.poll() is None:
                now = time.monotonic()
                totals[(case, phase)] += now - last
                case_totals[case] += now - last
                last = now
                event_file = output / "events.jsonl"
                if event_file.exists():
                    with event_file.open() as stream:
                        stream.seek(event_offset)
                        for line in stream:
                            event = json.loads(line)
                            case = event["case"]
                            phase = event["phase"]
                        event_offset = stream.tell()
                sample = process_sample(process.pid, known)
                cpu_starts.setdefault(case, sum(cpu.values()))
                for pid, value in sample["cpu_seconds"].items():
                    cpu[pid] = max(cpu.get(pid, 0), value)
                sample.update(time=now, case=case, phase=phase)
                samples.write(json.dumps(sample) + "\n")
                samples.flush()
                key = (case, phase)
                peak = peaks.setdefault(key, {"process_tree_rss_bytes": 0, "python_rss_bytes": 0})
                peak["process_tree_rss_bytes"] = max(
                    peak["process_tree_rss_bytes"], sample["rss_bytes"]
                )
                peak["python_rss_bytes"] = max(peak["python_rss_bytes"], sample["python_rss_bytes"])
                peak_disk = max(peak_disk, disk_bytes(output))
                phase_limit = budgets.get(
                    f"{phase}_wall_seconds", budgets["case_total_wall_seconds"]
                )
                if totals[key] > phase_limit:
                    failure = f"{phase} wall budget exceeded"
                elif case_totals[case] > budgets["case_total_wall_seconds"]:
                    failure = "case wall budget exceeded"
                elif sample["rss_bytes"] > budgets["process_tree_rss_mib"] * MIB:
                    failure = "sampled process-tree RSS budget exceeded"
                elif sample["python_rss_bytes"] > budgets["python_rss_mib"] * MIB:
                    failure = "Python RSS budget exceeded"
                elif sum(cpu.values()) - cpu_starts[case] > budgets["cpu_seconds"]:
                    failure = "sampled CPU budget exceeded"
                elif disk_bytes(output / case) > budgets["case_disk_mib"] * MIB:
                    failure = "case retained output budget exceeded"
                elif disk_bytes(suite_root) > budgets["total_outputs_mib"] * MIB:
                    failure = "total retained output budget exceeded"
                elif shutil.disk_usage(suite_root).free < budgets["minimum_free_disk_mib"] * MIB:
                    failure = "free disk reserve exhausted"
                if failure:
                    break
                time.sleep(profile["sample_interval_seconds"])
        finally:
            if process.poll() is None:
                os.killpg(process.pid, signal.SIGINT)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait()
        result_path = output / "result.json"
        result = json.loads(result_path.read_text()) if result_path.exists() else {}
        if process.returncode or not result:
            failure = failure or f"worker exited {process.returncode} without successful result"
        if result.get("cpu_self_and_waited_children_seconds", 0) > budgets["cpu_seconds"] * len(
            result.get("cases", [])
        ):
            failure = failure or "self plus waited-child CPU budget exceeded"
        boundaries = []
        event_file = output / "events.jsonl"
        previous = None
        if event_file.exists():
            exact = {}
            for line in event_file.read_text().splitlines():
                event = json.loads(line)
                if previous is not None and event["case"] == previous["case"]:
                    key = (previous["case"], previous["phase"])
                    point = exact.setdefault(
                        key, {"wall_seconds": 0, "cpu_self_waited_children_seconds": 0}
                    )
                    point["wall_seconds"] += event["time"] - previous["time"]
                    point["cpu_self_waited_children_seconds"] += max(
                        0,
                        event["cpu_self_waited_children_seconds"]
                        - previous["cpu_self_waited_children_seconds"],
                    )
                previous = event
            boundaries = [
                {"case": case, "phase": phase, **value} for (case, phase), value in exact.items()
            ]
            for point in boundaries:
                if point["wall_seconds"] > budgets.get(
                    point["phase"] + "_wall_seconds", budgets["case_total_wall_seconds"]
                ):
                    failure = failure or point["phase"] + " exact wall budget exceeded"
        record = {
            "phase_boundaries": boundaries,
            "status": "blocked" if failure else "passed",
            "reason": failure,
            "worker_exit": process.returncode,
            "configuration": config,
            "phase_samples": [
                {"case": c, "phase": p, "wall_seconds": totals[(c, p)], **v}
                for (c, p), v in peaks.items()
            ],
            "sampled_cpu_lower_bound_seconds": sum(cpu.values()),
            "peak_retained_output_bytes": peak_disk,
            "sampling_note": "100 ms sampled aggregate process-tree peak, not instantaneous peak; Python high-water is separate.",
            "result_path": str(result_path),
        }
        (output / "measurement.json").write_text(json.dumps(record, indent=2) + "\n")
        return record


def growth_gate(records: list[dict], profile: dict) -> dict:
    """Compare phase peaks at fixed mode, density, repetition and temperature."""
    measurements = {}
    for record in records:
        config = record["configuration"]
        for row in record["phase_samples"]:
            if row["case"] not in {"application_cold", "warmed_worker_repeat"}:
                continue
            key = (config["mode"], config["links"], config["repetition"], row["case"], row["phase"])
            measurements.setdefault(key, {})[config["pages"]] = row
    comparisons = []
    for key, sizes in measurements.items():
        if 32 not in sizes or 160 not in sizes:
            continue
        small, large = sizes[32], sizes[160]
        python_growth = max(0, large["python_rss_bytes"] - small["python_rss_bytes"]) / MIB
        tree_growth = (
            max(0, large["process_tree_rss_bytes"] - small["process_tree_rss_bytes"]) / MIB
        )
        passed = (
            python_growth <= profile["budgets"]["growth_small_to_large_python_mib"]
            and tree_growth <= profile["budgets"]["growth_small_to_large_process_tree_mib"]
        )
        comparisons.append(
            {
                "axis": key,
                "python_growth_mib": python_growth,
                "process_tree_growth_mib": tree_growth,
                "python_mib_per_page": python_growth / 128,
                "process_tree_mib_per_page": tree_growth / 128,
                "passed": passed,
            }
        )
    expected = {
        (mode, links, repeat, temperature, phase)
        for mode in ("html", "javascript")
        for links in profile["link_densities"]
        for repeat in range(1, 4)
        for temperature in profile["temperature"]
        for phase in (
            "collection",
            "audit",
            "reanalysis",
            "query_report",
            *(("render",) if mode == "javascript" else ()),
        )
    }
    observed = {tuple(row["axis"]) for row in comparisons}
    missing = sorted(expected - observed)
    return {
        "status": "passed"
        if not missing and all(row["passed"] for row in comparisons)
        else "blocked",
        "missing_phase_axes": missing,
        "comparisons": comparisons,
    }


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("plan", "run", "page-cap", "smoke", "_worker"))
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--source-revision")
    parser.add_argument(
        "--execute", action="store_true", help="explicitly execute the approved serial benchmark"
    )
    args = parser.parse_args(argv)
    profile = json.loads(PROFILE.read_text())
    if args.command == "_worker":
        worker(json.loads((args.out / "configuration.json").read_text()), args.out)
        return 0
    identity = source_identity()
    if args.command == "plan":
        args.out.write_text(
            json.dumps(
                {"profile": profile, "identity": identity, "case_pairs": cases(profile)}, indent=2
            )
            + "\n"
        )
        return 0
    if (
        not args.execute
        or not args.source_revision
        or args.source_revision != identity["revision"]
        or identity["dirty"]
    ):
        parser.error("execution requires --execute, exact --source-revision and a clean checkout")
    if sys.platform not in {"darwin", "linux"} or shutil.which("ps") is None:
        parser.error("numeric process-tree measurement is unavailable on this platform")
    if identity["playwright"] == "unavailable":
        parser.error("Playwright is unavailable; no installation attempted")
    from playwright.sync_api import sync_playwright

    with sync_playwright() as runtime:
        executable = Path(os.environ.get("SEOHEAD_CHROME") or runtime.chromium.executable_path)
    if not executable.is_file() or not os.access(executable, os.X_OK):
        parser.error("Chromium is unavailable; select an installed executable with SEOHEAD_CHROME")
    identity["browser_sha256"] = digest(executable)
    if args.out.exists():
        parser.error(
            "output must be a new directory; existing benchmark evidence is never replaced"
        )
    args.out.mkdir(parents=True)
    if shutil.disk_usage(args.out).free < profile["budgets"]["minimum_free_disk_mib"] * MIB:
        parser.error("free disk reserve is below the frozen minimum")
    (args.out / "frozen-manifest.json").write_text(
        json.dumps(
            {"profile": profile, "identity": identity, "approved_command": args.command}, indent=2
        )
        + "\n"
    )
    planned = cases(profile)
    if args.command == "smoke":
        planned = [
            {
                "mode": "javascript",
                "pages": 2,
                "links": 1,
                "forms": 2,
                "repetition": 1,
                "render_limit": 2,
                "recovery": True,
            }
        ]
    elif args.command == "page-cap":
        planned = [
            {
                "mode": "javascript",
                "pages": 10001,
                "links": 8,
                "forms": 128,
                "repetition": 1,
                "render_limit": 16,
                "temperatures": ["application_cold"],
            }
        ]
    else:
        for case in planned:
            case["recovery"] = (
                case["mode"] == "javascript"
                and case["pages"] == 160
                and case["links"] == 64
                and case["repetition"] == 1
            )
    results = []
    for ordinal, case in enumerate(planned):
        case["source_revision"] = identity["revision"]
        case["browser_path"] = str(executable)
        case["runtime_identity"] = {
            key: identity[key]
            for key in (
                "python",
                "sqlite",
                "playwright",
                "browser_sha256",
                "machine",
                "physical_memory_bytes",
                "logical_cpus",
            )
        }
        record = supervise(case, args.out / f"pair-{ordinal:02d}", args.out, profile)
        results.append(record)
        (args.out / "manifest.json").write_text(
            json.dumps(
                {
                    "schema": "seohead.js-bridge-benchmark.v1",
                    "identity": identity,
                    "results": results,
                },
                indent=2,
            )
            + "\n"
        )
        print(
            json.dumps({"pair": ordinal, "status": record["status"], "reason": record["reason"]}),
            flush=True,
        )
        if record["status"] != "passed":
            return 1
    if args.command == "run":
        growth = growth_gate(results, profile)
        (args.out / "growth.json").write_text(json.dumps(growth, indent=2) + "\n")
        if growth["status"] != "passed":
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
