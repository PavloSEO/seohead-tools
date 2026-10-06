#!/usr/bin/env python3
"""Exercise one complete synthetic native-crawl route at production-scale counts.

The fixture is an owned ``.test`` origin behind ``httpx.MockTransport``.  It
uses the ordinary native handler, robots fetch, sitemap parser and loader,
SQLite collector, analyzer, audit.v2 writer and read-only consumers.  It never
opens a socket and it never inserts rows directly into a scan database.

This is deliberately a manual capacity gate, not a normal test-suite fixture:
the 50k, 100k and 1m stages write real retained artifacts and can take material
time and disk.  Results belong under ``Work/tools/reports/`` and a non-zero
exit means a requested stage did not prove readiness.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import random
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

import httpx

# Running ``python scripts/...`` otherwise puts only ``scripts`` ahead of the
# shared editable environment, which can import ``seohead`` from another
# checkout. Every capacity artifact must be bound to this worktree.
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

HOST = "million-crawl.test"
START_URL = f"https://{HOST}/p/0"
SITEMAP_INDEX = f"https://{HOST}/sitemap-index.xml"
RUNTIME_KEYS = ("python", "sqlite", "httpx", "lxml", "beautifulsoup4")


class SyntheticOrigin:
    """A deterministic HTML, robots and sitemap origin without socket I/O."""

    def __init__(
        self,
        pages: int,
        shard_size: int,
        interrupt_after: int | None = None,
        *,
        links_per_page: int = 1,
        forms_per_page: int = 0,
        body_padding_bytes: int = 0,
        body_profile: str = "padding",
    ) -> None:
        if pages < 1:
            raise ValueError("pages must be positive")
        if shard_size < 1:
            raise ValueError("shard_size must be positive")
        if (
            not 1 <= links_per_page <= 150
            or not 0 <= forms_per_page <= 128
            or not 0 <= body_padding_bytes <= 65536
        ):
            raise ValueError("fixture density is outside its declared bounds")
        if body_profile not in {"padding", "catalogue-v1"}:
            raise ValueError("unknown synthetic body profile")
        self.body_profile = body_profile
        self.links_per_page = min(links_per_page, pages)
        self.forms_per_page = forms_per_page
        self.body_padding_bytes = body_padding_bytes
        self.pages = pages
        self.shard_size = shard_size
        self.interrupt_after = interrupt_after
        self.page_requests = 0
        self.requests: Counter[str] = Counter()

    @property
    def shard_count(self) -> int:
        return (self.pages + self.shard_size - 1) // self.shard_size

    def resume(self) -> None:
        self.interrupt_after = None

    @staticmethod
    def _xml(tag: str, values: Iterator[str]) -> bytes:
        return (
            "<?xml version='1.0' encoding='UTF-8'?>"
            + f"<{tag} xmlns='http://www.sitemaps.org/schemas/sitemap/0.9'>"
            + "".join(values)
            + f"</{tag}>"
        ).encode("utf-8")

    def _index(self) -> bytes:
        return self._xml(
            "sitemapindex",
            (
                f"<sitemap><loc>https://{HOST}/sitemaps/{shard}.xml</loc></sitemap>"
                for shard in range(self.shard_count)
            ),
        )

    def _shard(self, shard: int) -> bytes:
        first = shard * self.shard_size
        last = min(first + self.shard_size, self.pages)
        if shard < 0 or first >= self.pages:
            return b""
        return self._xml(
            "urlset",
            (
                f"<url><loc>https://{HOST}/p/{page}</loc><lastmod>2026-10-04</lastmod>"
                "<changefreq>weekly</changefreq><priority>0.7</priority></url>"
                for page in range(first, last)
            ),
        )

    def _body_extra(self, page: int) -> str:
        if self.body_profile == "padding":
            return "<p>" + ("x" * self.body_padding_bytes) + "</p>"
        rng = random.Random(page)
        materials = (
            "aluminium",
            "steel",
            "polymer",
            "ceramic",
            "composite",
            "glass",
            "copper",
            "silicone",
        )
        finishes = (
            "matte",
            "polished",
            "brushed",
            "coated",
            "textured",
            "anodized",
            "satin",
            "natural",
        )
        roles = (
            "connector",
            "housing",
            "bracket",
            "panel",
            "adapter",
            "frame",
            "terminal",
            "mount",
        )
        parts = []
        size = 0
        while size < self.body_padding_bytes:
            ordinal = len(parts)
            sku = hashlib.sha256(f"synthetic-catalogue:{page}:{ordinal}".encode()).hexdigest()[:24]
            width, height, depth = (rng.randrange(10, 900) for _ in range(3))
            material, finish, role = rng.choice(materials), rng.choice(finishes), rng.choice(roles)
            metadata = json.dumps(
                {
                    "fixture": True,
                    "sku": sku,
                    "batch": rng.randrange(100000, 999999),
                    "dimensions_mm": [width, height, depth],
                    "material": material,
                    "finish": finish,
                },
                separators=(",", ":"),
            )
            section = (
                f'<section data-reference="{sku}"><h2>Specification {ordinal + 1}: {finish} {role}</h2>'
                f"<p>This synthetic catalogue record describes a {material} {role} with a {finish} finish. "
                f"The measured fixture dimensions are {width} by {height} by {depth} millimetres. "
                "Every entry is generated for repeatable storage and parsing measurements; it is not a commercial offer.</p>"
                f"<table><tr><th>Reference</th><td>{sku}</td></tr><tr><th>Material</th><td>{material}</td></tr>"
                f"<tr><th>Envelope</th><td>{width} / {height} / {depth} mm</td></tr></table>"
                f'<script type="application/json">{metadata}</script></section>'
            )
            parts.append(section)
            size += len(section.encode("utf-8"))
        return '<article data-fixture="catalogue-v1">' + "".join(parts) + "</article>"

    def _page(self, page: int) -> bytes:
        if page < 0 or page >= self.pages:
            return b"<html><head><title>Absent</title></head><body>Absent</body></html>"
        links = "".join(
            f"<a href='/p/{(page + hop) % self.pages}'>Next catalogue page {hop}</a>"
            for hop in range(1, self.links_per_page + 1)
        )
        title = "" if page % 997 == 0 else f"Synthetic catalogue page {page}"
        description = "" if page % 991 == 0 else f"Deterministic description for page {page}."
        canonical = f"https://{HOST}/p/{page}"
        extra = "".join(
            f"<form method='post' action='/lead/{form}'><input name='email'></form>"
            for form in range(self.forms_per_page)
        )
        if page % 4096 == 0:
            extra += "<img src='/assets/pixel.png'><form method='post' action='/lead'><input name='email'></form>"
        return (
            "<!doctype html><html lang='en'><head>"
            + (f"<title>{title}</title>" if title else "")
            + (f"<meta name='description' content='{description}'>" if description else "")
            + f"<link rel='canonical' href='{canonical}'>"
            + "<meta name='viewport' content='width=device-width, initial-scale=1'>"
            + "</head><body><main>"
            + f"<h1>Product family {page % 101}</h1><p>Owned synthetic content for URL {page}.</p>"
            + links
            + extra
            + self._body_extra(page)
            + "</main></body></html>"
        ).encode("utf-8")

    @staticmethod
    def _response(
        request: httpx.Request, status: int, content: bytes = b"", **headers: str
    ) -> httpx.Response:
        """Keep mock bytes unread for the collector's real streaming path."""
        return httpx.Response(
            status,
            stream=httpx.ByteStream(content),
            headers=headers,
            request=request,
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/robots.txt":
            self.requests["robots"] += 1
            content = (f"User-agent: *\nAllow: /\nSitemap: {SITEMAP_INDEX}\n").encode()
            return self._response(request, 200, content, **{"content-type": "text/plain"})
        if path == "/sitemap-index.xml":
            self.requests["sitemap_index"] += 1
            return self._response(
                request, 200, self._index(), **{"content-type": "application/xml"}
            )
        if path.startswith("/sitemaps/") and path.endswith(".xml"):
            self.requests["sitemap_shard"] += 1
            try:
                shard = int(path.rsplit("/", 1)[-1].removesuffix(".xml"))
            except ValueError:
                return self._response(request, 404)
            content = self._shard(shard)
            return self._response(
                request,
                200 if content else 404,
                content,
                **{"content-type": "application/xml"},
            )
        if path.startswith("/p/"):
            self.page_requests += 1
            if self.interrupt_after is not None and self.page_requests > self.interrupt_after:
                raise KeyboardInterrupt("intentional synthetic collection interruption")
            try:
                page = int(path.rsplit("/", 1)[-1])
            except ValueError:
                return self._response(request, 404)
            status = 404 if page and page % 100_003 == 0 else 200
            return self._response(
                request,
                status,
                self._page(page),
                **{
                    "content-type": "text/html; charset=utf-8",
                    "cache-control": "max-age=60",
                },
            )
        return self._response(request, 404)


def _body_profile_summary(origin: SyntheticOrigin) -> dict[str, Any]:
    histogram: Counter[int] = Counter()
    digest = hashlib.sha256()
    sizes = []
    for page in range(min(origin.pages, 32)):
        body = origin._page(page)
        histogram.update(body)
        digest.update(len(body).to_bytes(8, "big"))
        digest.update(body)
        sizes.append(len(body))
    total = sum(sizes)
    entropy = -sum((count / total) * math.log2(count / total) for count in histogram.values())
    return {
        "profile": origin.body_profile,
        "sampled_pages": len(sizes),
        "sample_rule": "first up to 32 generated fixture pages, including markup and scripts",
        "sample_sha256": digest.hexdigest(),
        "sample_min_body_bytes": min(sizes),
        "sample_max_body_bytes": max(sizes),
        "sample_mean_body_bytes": round(total / len(sizes), 2),
        "sample_byte_entropy_bits": round(entropy, 6),
        "entropy_note": "Empirical byte-frequency entropy of the declared sample, not a claim of real-site representativeness.",
    }


def _peak_rss_mib() -> float:
    from resource import RUSAGE_SELF, getrusage

    rss = float(getrusage(RUSAGE_SELF).ru_maxrss)
    return round(rss / (1024 * 1024) if sys.platform == "darwin" else rss / 1024, 2)


def _disk_bytes(path: Path) -> dict[str, int]:
    candidates = {
        "scan": path,
        "scan_wal": Path(str(path) + "-wal"),
        "scan_shm": Path(str(path) + "-shm"),
        "audit_v2": path.with_name(path.name + ".audit-v2.sqlite"),
    }
    result = {
        name: candidate.stat().st_size if candidate.exists() else 0
        for name, candidate in candidates.items()
    }
    result["total"] = sum(result.values())
    return result


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _revision() -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=PROJECT_ROOT, check=True, text=True, capture_output=True
    ).stdout.strip()


def _runtime() -> dict[str, str]:
    import bs4
    import lxml

    return {
        "python": platform.python_version(),
        "sqlite": sqlite3.sqlite_version,
        "httpx": httpx.__version__,
        "lxml": getattr(lxml, "__version__", "unknown"),
        "beautifulsoup4": getattr(bs4, "__version__", "unknown"),
    }


def _loaded_code() -> dict[str, dict[str, str]]:
    """Bind a capacity result to the modules Python actually imported."""
    import hashlib
    import importlib

    modules = (
        "seohead.sf.core.inlinks",
        "seohead.servers.scan_handlers",
        "seohead.crawl.sqlite_adapter",
        "seohead.storage.native_scan",
    )
    loaded: dict[str, dict[str, str]] = {}
    for name in modules:
        path = Path(str(importlib.import_module(name).__file__)).resolve()
        if PROJECT_ROOT not in (path, *path.parents):
            raise RuntimeError(f"capacity run imported {name} outside this worktree: {path}")
        loaded[name] = {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return loaded


def _loaded_callable_code() -> dict[str, dict[str, str]]:
    """Record the callable bodies loaded before the crawl begins, not later files."""
    import hashlib
    import inspect
    import marshal

    from seohead.crawl import sqlite_adapter
    from seohead.servers import handlers, scan_handlers
    from seohead.sf.core import inlinks

    callables = {
        "inlinks._emit_discovery_paths": inlinks._emit_discovery_paths,
        "handlers._audit_crawl_result": handlers._audit_crawl_result,
        "scan_handlers.crawl_site_scan": scan_handlers.crawl_site_scan,
        "sqlite_adapter.crawl_to_scan": sqlite_adapter.crawl_to_scan,
    }
    return {
        name: {
            "file": str(Path(str(callable.__code__.co_filename)).resolve()),
            "source_sha256": hashlib.sha256(inspect.getsource(callable).encode()).hexdigest(),
            "code_sha256": hashlib.sha256(marshal.dumps(callable.__code__)).hexdigest(),
        }
        for name, callable in callables.items()
    }


@contextmanager
def _discovery_path_trace() -> Iterator[list[dict[str, int]]]:
    """Observe the real discovery-path branch without changing emitted findings."""
    from seohead.sf.core import inlinks

    original = inlinks._emit_discovery_paths
    trace: list[dict[str, int]] = []

    def observed(ctx, path_for, *args, **kwargs):
        depths = args[0] if args else kwargs.get("depths")
        population = 0
        resolved = 0
        deep = 0
        if depths is not None:
            for page in ctx.indexable_html_pages():
                population += 1
                key = inlinks.norm_url(page.url)
                depth = depths(key) if callable(depths) else depths.get(key)
                resolved += int(depth is not None)
                deep += int(depth is not None and depth > inlinks._MAX_PATH_URLS)
        path_calls = 0
        longest = 0

        def recorded_path(key):
            nonlocal path_calls, longest
            path = path_for(key)
            path_calls += 1
            longest = max(longest, len(path or ()))
            return path

        original(ctx, recorded_path, *args, **kwargs)
        trace.append(
            {
                "indexable_pages": population,
                "resolved_depths": resolved,
                "missing_depths": population - resolved,
                "deep_depths": deep,
                "path_for_calls": path_calls,
                "longest_path_for_result": longest,
            }
        )

    inlinks._emit_discovery_paths = observed
    try:
        yield trace
    finally:
        inlinks._emit_discovery_paths = original


@contextmanager
def _synthetic_transport(origin: SyntheticOrigin) -> Iterator[None]:
    """Inject one transport beneath real fetch and sitemap code for this run only."""
    from seohead.crawl import collect, sqlite_adapter
    from seohead.sf.core import sitemap_coverage
    from seohead.tools import sitemap

    @contextmanager
    def client_context(_settings: dict[str, Any], _fetcher: Any, _proxy_route: Any = None):
        client = httpx.Client(transport=httpx.MockTransport(origin.handle), follow_redirects=False)
        try:
            yield client
        finally:
            client.close()

    def sitemap_client(*_args: Any, **_kwargs: Any) -> tuple[httpx.Client, bool]:
        return httpx.Client(
            transport=httpx.MockTransport(origin.handle), follow_redirects=True
        ), False

    with (
        patch.object(sqlite_adapter, "_client_context", client_context),
        # ``fetch_one`` normally validates a DNS target before it reaches an
        # opened client. The mock client opens no socket, and this reserved
        # origin has no DNS record to validate; all parsing/dispatch/storage
        # code below this boundary remains the production implementation.
        patch.object(collect, "validate_url", lambda _url, **_kwargs: None),
        patch.object(collect, "pinned_target", lambda url, **_kwargs: (url, {}, {})),
        patch.object(sitemap, "http_client", sitemap_client),
        patch.object(sitemap_coverage, "http_client", sitemap_client),
        # The transport is the owned origin; this is the only no-DNS test seam.
        patch.object(sitemap_coverage, "validate_url", lambda _url: None),
    ):
        yield


def _settings(pages: int) -> dict[str, Any]:
    from seohead.crawl.settings import load

    return load(
        overrides={
            "limits.max_urls": pages,
            # One request per page plus robots, sitemap shards and bounded retries.
            "limits.max_requests": min(2_000_000, pages + max(10_000, pages // 10)),
            "limits.max_depth": 1,
            "speed.min_delay_seconds": 0,
            "speed.concurrency": 8,
            "robots.policy": "respect",
            "sitemaps.auto_discover": False,
            "cache.mode": "off",
        }
    )


def _assert_conservation(scan: Path, pages: int) -> dict[str, int]:
    from seohead.storage import open_scan

    con = open_scan(scan, require_audit=False)
    try:
        counts = {
            name: int(con.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
            for name in ("pages", "links", "forms", "frontier")
        }
        counts["sitemap_members"] = int(
            con.execute(
                "SELECT COUNT(*) FROM context_items WHERE kind='sitemap_declared_url'"
            ).fetchone()[0]
        )
        counts["retained_page_documents"] = con.execute(
            "SELECT COUNT(*) FROM pages p JOIN documents d ON d.document_id=p.document_id WHERE d.body_state='complete' AND d.body_sha256 IS NOT NULL"
        ).fetchone()[0]
        for table in ("responses", "documents", "bodies"):
            counts[table] = con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        sizes = con.execute(
            "SELECT COALESCE(SUM(decoded_bytes),0),COALESCE(SUM(stored_bytes),0),COALESCE(MIN(decoded_bytes),0),COALESCE(MAX(decoded_bytes),0) FROM bodies"
        ).fetchone()
        counts.update(
            zip(
                (
                    "body_decoded_bytes",
                    "body_stored_bytes",
                    "smallest_body_bytes",
                    "largest_body_bytes",
                ),
                sizes,
                strict=True,
            )
        )
        frontier = dict(
            con.execute("SELECT state,COUNT(*) FROM frontier GROUP BY state").fetchall()
        )
    finally:
        con.close()
    if (
        counts["pages"] != pages
        or counts["sitemap_members"] != pages
        or counts["retained_page_documents"] != pages
    ):
        raise AssertionError(f"retained population disagrees with declared scope: {counts}")
    if frontier.get("done", 0) != pages or frontier.get("queued", 0) or frontier.get("inflight", 0):
        raise AssertionError(f"frontier is not complete: {frontier}")
    return {**counts, **{f"frontier_{state}": int(count) for state, count in frontier.items()}}


def _collector_summary(result: dict[str, Any]) -> dict[str, Any]:
    """Persist only bounded acceptance facts, never the large audit document."""
    summary = result.get("summary") if isinstance(result.get("summary"), dict) else {}
    return {
        key: result.get(key)
        for key in (
            "scan",
            "urls_collected",
            "links_collected",
            "forms_collected",
            "partial",
            "finish_reason",
            "resumed",
            "audit_available",
            "audit_reason",
            "finalized",
            "corpus_partial",
        )
    } | {
        "audit_totals": summary.get("totals"),
        "audit_coverage": summary.get("check_coverage"),
        "audit_health_score": summary.get("health_score"),
    }


def _recheck_consumers(scan: Path, output: Path, revision: str, pages: int) -> dict[str, Any]:
    """Prove refusal for reused evidence and resolution from a fresh one-page capture."""
    from datetime import datetime, timezone

    from seohead.servers import handlers
    from seohead.servers.scan_handlers import crawl_site_scan
    from seohead.storage.audit_v2 import AuditV2Reader

    with AuditV2Reader(scan) as reader:
        finding_id = next(
            item["id"]
            for item in reader.iter_collection("/issues")
            if item["check"] == "TITLE_MISSING" and item["target_url"] == START_URL
        )
        generated = reader.header["run"]["generated_at"]
    negative = handlers.verify_fixes(
        baseline=str(scan),
        finding_ids=[finding_id],
        after=str(scan),
        out_dir=str(output / "same-observation"),
    )
    if [item["status"] for item in negative["findings"]] != ["not_verifiable"]:
        raise AssertionError("same-observation recheck falsely verified a fix")

    class FixedOrigin(SyntheticOrigin):
        def _page(self, page):
            return (
                super()
                ._page(page)
                .replace(b"</head>", b"<title>Corrected synthetic page title</title></head>")
            )

    baseline_time = datetime.fromisoformat(generated.replace("Z", "+00:00")).timestamp()
    delay = baseline_time + 1.01 - datetime.now(timezone.utc).timestamp()
    if delay > 0:
        time.sleep(delay)
    settings = _settings(pages)
    settings["limits"]["max_urls"] = 1
    fresh_path = output / "fresh-observation.sqlite"
    with _synthetic_transport(FixedOrigin(1, 1)):
        fresh = crawl_site_scan(
            START_URL, scan_out=str(fresh_path), settings=settings, producer_build=revision
        )
    if not fresh.get("audit_available") or not fresh.get("finalized") or fresh.get("partial"):
        raise AssertionError("fresh bounded recheck observation did not complete")
    positive = handlers.verify_fixes(
        baseline=str(scan),
        finding_ids=[finding_id],
        after=str(fresh_path),
        out_dir=str(output / "fresh-recheck"),
    )
    if [item["status"] for item in positive["findings"]] != ["resolved"]:
        raise AssertionError(
            f"fresh local title correction did not resolve selected finding: {positive['findings']!r}"
        )
    return {
        "same_observation": negative["summary"],
        "fresh_observation": positive["summary"],
        "negative_path": negative["verification"],
        "positive_path": positive["verification"],
    }


def _consumers(
    scan: Path, output: Path, revision: str, *, comparison_compression: str = "none"
) -> dict[str, Any]:
    from seohead.servers import handlers
    from seohead.sf.tasks import build_tasks_from_audit_v2
    from seohead.storage.audit_v2 import AuditV2Reader

    output.mkdir(parents=True, exist_ok=True)
    with AuditV2Reader(scan) as reader:
        audit_v2 = {pointer: reader.count(pointer) for pointer in reader.collections}
        if audit_v2.get("/pages") != 0 and audit_v2.get("/issues") is None:
            raise AssertionError("audit.v2 lacks its finding collection")
        payload_bytes = reader.con.execute(
            "SELECT COALESCE(SUM(length(CAST(value_json AS BLOB))),0),COALESCE(MAX(length(CAST(value_json AS BLOB))),0) FROM items"
        ).fetchone()
        header_bytes = reader.con.execute(
            "SELECT length(CAST(header_json AS BLOB)) FROM audit_meta WHERE singleton=1"
        ).fetchone()[0]
        audit_sizes = {
            "collection_payload_bytes": payload_bytes[0],
            "largest_item_bytes": payload_bytes[1],
            "header_bytes": header_bytes,
        }
        summary = reader.header["summary"]
        health = {
            "score": summary.get("health_score"),
            "scope": summary.get("health_score_scope"),
            "check_coverage": summary.get("check_coverage"),
        }
        coverage = health["check_coverage"]
        if not isinstance(coverage, dict) or sum(
            coverage.get(key, 0)
            for key in ("checks_fired", "checks_silent", "checks_skipped", "checks_disabled")
        ) != coverage.get("checks_total"):
            raise AssertionError("retained check coverage denominator is not conserved")
    before_hash = _file_hash(scan)
    companion_path = scan.with_name(scan.name + ".audit-v2.sqlite")
    before_audit_hash = _file_hash(companion_path)
    task_backlog = build_tasks_from_audit_v2(str(scan))
    export = handlers.scan_export(
        input_path=str(scan),
        out=str(output / "scan-export.csv"),
        format="csv",
        records=["pages"],
        fields={"pages": ["url", "status_code", "title", "canonical"]},
    )
    report = handlers.report_build(audit=str(scan), fmt="csv", out=str(output / "audit-report.csv"))
    status = handlers.scan_status(input_path=str(scan))
    diagnosis = handlers.crawl_diagnose(scan=str(scan))
    consistency = handlers.log_scan(run=str(scan))
    if not consistency.get("ok") or consistency.get("read", {}).get("pages") != audit_v2["/pages"]:
        raise AssertionError(
            f"consistency consumer failed to read full population: {consistency!r}"
        )
    reanalysis = handlers.scan_reanalyze(
        input_path=str(scan), out=str(output / "reanalysis.sqlite"), producer_build=revision
    )
    compare_options = (
        {"compression": comparison_compression} if comparison_compression != "none" else {}
    )
    comparison = handlers.compare_crawls(
        before=str(scan),
        after=str(output / "reanalysis.sqlite"),
        out_dir=str(output / "comparison"),
        **compare_options,
    )
    from seohead.sf.core.compare_store import iter_compare_rows

    comparison_roundtrip = {
        name: sum(1 for _ in iter_compare_rows(comparison["manifest"], name))
        for name in comparison["files"]
    }
    if any(
        comparison_roundtrip[name] != item["rows"] for name, item in comparison["files"].items()
    ):
        raise AssertionError("comparison package did not round-trip every retained row")
    if (
        comparison.get("conservation", {}).get("state") != "complete"
        or comparison["conservation"]["before_issues"] != audit_v2["/issues"]
    ):
        raise AssertionError(f"bounded comparison did not conserve source findings: {comparison!r}")
    recheck = _recheck_consumers(scan, output, revision, audit_v2["/pages"])
    after_hash = _file_hash(scan)
    after_audit_hash = _file_hash(companion_path)
    if before_hash != after_hash or before_audit_hash != after_audit_hash:
        raise AssertionError("read-only consumers or reanalysis changed the source scan")
    with AuditV2Reader(output / "reanalysis.sqlite") as derived:
        if derived.count("/pages") != audit_v2["/pages"]:
            raise AssertionError("offline reanalysis did not conserve the page population")
    if not export.get("ok") or not report.get("ok") or not reanalysis.get("audit_available"):
        raise AssertionError(
            f"consumer failure: export={export!r}; report={report!r}; reanalysis={reanalysis!r}"
        )
    return {
        "audit_v2": audit_v2,
        "audit_sizes": audit_sizes,
        "source_sha256_before": before_hash,
        "source_sha256_after": after_hash,
        "source_audit_sha256_before": before_audit_hash,
        "source_audit_sha256_after": after_audit_hash,
        "tasks": task_backlog["summary"],
        "health": health,
        "comparison": comparison,
        "comparison_roundtrip": comparison_roundtrip,
        "export": export,
        "report": report,
        "status": status,
        "diagnosis_codes": [item["code"] for item in diagnosis.get("diagnoses", [])],
        "consistency": consistency,
        "reanalysis": reanalysis,
        "recheck": recheck,
    }


def run_stage(
    output: Path,
    *,
    pages: int,
    shard_size: int,
    interrupt_after: int | None,
    consumers: bool,
    links_per_page: int = 1,
    forms_per_page: int = 0,
    body_padding_bytes: int = 0,
    comparison_compression: str = "none",
    body_profile: str = "padding",
) -> dict[str, Any]:
    """Run one measured stage; exceptions intentionally make its status failed."""
    from seohead.servers.scan_handlers import crawl_site_scan
    from seohead.storage import open_scan
    from seohead.storage.native_scan import NativeScan
    from seohead.tools import sitemap

    if pages > sitemap.MAX_URLS:
        raise RuntimeError(
            f"standard sitemap parser limit is {sitemap.MAX_URLS:,}, below requested {pages:,}; "
            "a native 1m crawl through the normal sitemap route is not admitted"
        )
    output.mkdir(parents=True, exist_ok=True)
    scan = output / "native.sqlite"
    origin = SyntheticOrigin(
        pages,
        shard_size,
        interrupt_after,
        links_per_page=links_per_page,
        forms_per_page=forms_per_page,
        body_padding_bytes=body_padding_bytes,
        body_profile=body_profile,
    )
    settings = _settings(pages)
    revision = _revision()
    loaded_code = _loaded_code()
    loaded_callable_code = _loaded_callable_code()
    started = time.monotonic()
    checkpoint: dict[str, Any] | None = None
    with _synthetic_transport(origin), _discovery_path_trace() as discovery_path_trace:
        if interrupt_after is not None:
            try:
                crawl_site_scan(
                    START_URL,
                    scan_out=str(scan),
                    settings=settings,
                    sitemap=SITEMAP_INDEX,
                    producer_build=revision,
                )
            except KeyboardInterrupt:
                con = open_scan(scan, require_audit=False)
                try:
                    row = con.execute("SELECT lifecycle,finish_reason FROM scan").fetchone()
                    committed = int(con.execute("SELECT COUNT(*) FROM pages").fetchone()[0])
                finally:
                    con.close()
                if not 0 < committed < pages or row["lifecycle"] == "finished":
                    raise AssertionError(
                        f"interruption did not leave a resumable prefix: {committed}, {dict(row)}"
                    ) from None
                checkpoint = {
                    "pages": committed,
                    "lifecycle": row["lifecycle"],
                    "finish_reason": row["finish_reason"],
                }
            else:
                raise AssertionError("synthetic interruption did not interrupt the collector")
            origin.resume()
        result = crawl_site_scan(
            START_URL,
            scan_out=str(scan),
            settings=settings,
            sitemap=SITEMAP_INDEX,
            producer_build=revision,
        )
    finalization_retried = False
    if result.get("audit_available") and not result.get("finalized"):
        # A real interruption can leave the final WAL checkpoint temporarily
        # blocked even after the resumed collector and audit both completed.
        # Re-open through the public native writer and prove that its explicit
        # recovery path finalizes the exact same artifact before calling it done.
        with NativeScan.open(scan) as writer:
            result["finalized"] = writer.finish_capture(reason="finished")
        finalization_retried = True
    if not result.get("audit_available") or not result.get("finalized"):
        raise AssertionError(f"collection did not publish an audit: {result!r}")
    if result.get("partial") or result.get("corpus_partial"):
        raise AssertionError(
            f"requested scope or retained corpus is incomplete: {_collector_summary(result)!r}"
        )
    counts = _assert_conservation(scan, pages)
    if (
        counts["links"] != pages * origin.links_per_page
        or counts["forms"] != pages * forms_per_page + (pages + 4095) // 4096
    ):
        raise AssertionError(f"retained links/forms disagree with generated source: {counts}")
    record: dict[str, Any] = {
        "status": "passed",
        "pages_requested": pages,
        "shard_size": shard_size,
        "body_profile_sample": _body_profile_summary(origin),
        "fixture": {
            "schema": "seohead.synthetic-crawl.v3",
            "body_profile": body_profile,
            "links_per_page": origin.links_per_page,
            "forms_per_page": forms_per_page,
            "body_padding_bytes": body_padding_bytes,
        },
        "source_revision": revision,
        "runtime": _runtime(),
        "loaded_code": loaded_code,
        "loaded_callable_code": loaded_callable_code,
        "discovery_path_trace": discovery_path_trace,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "peak_rss_mib": _peak_rss_mib(),
        "disk_bytes": _disk_bytes(scan),
        "transport_requests": dict(origin.requests),
        "page_requests": origin.page_requests,
        "checkpoint": checkpoint,
        "finalization_retried": finalization_retried,
        "collector": _collector_summary(result),
        "conservation": counts,
        "body_storage": {
            "stored_to_decoded_ratio": round(
                counts["body_stored_bytes"] / counts["body_decoded_bytes"], 6
            )
            if counts["body_decoded_bytes"]
            else None,
            "note": "Body-codec ratio only; excludes SQLite indexes, audit and consumer files.",
        },
    }
    record["capture_audit_seconds"] = record["elapsed_seconds"]
    if consumers:
        record["consumers"] = _consumers(
            scan, output / "consumers", revision, comparison_compression=comparison_compression
        )
    record["elapsed_seconds"] = round(time.monotonic() - started, 3)
    record["peak_rss_mib"] = _peak_rss_mib()
    (output / "result.json").write_text(
        json.dumps(record, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8"
    )
    return record


def _parse_stages(value: str) -> list[int]:
    try:
        stages = [int(item) for item in value.split(",") if item]
    except ValueError as exc:
        raise argparse.ArgumentTypeError("stages must be comma-separated integers") from exc
    if not stages or any(stage < 1 for stage in stages):
        raise argparse.ArgumentTypeError("stages must contain positive counts")
    return stages


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        required=True,
        type=Path,
        help="empty or new report directory outside the repository",
    )
    parser.add_argument("--stages", type=_parse_stages, default=[50_000, 100_000, 1_000_000])
    parser.add_argument("--shard-size", type=int, default=50_000)
    parser.add_argument("--interrupt-after", type=int, default=25_000)
    parser.add_argument("--skip-consumers", action="store_true")
    parser.add_argument("--links-per-page", type=int, default=1)
    parser.add_argument("--forms-per-page", type=int, default=0)
    parser.add_argument("--body-padding-bytes", type=int, default=0)
    parser.add_argument("--comparison-compression", choices=("none", "gzip"), default="none")
    parser.add_argument("--body-profile", choices=("padding", "catalogue-v1"), default="padding")
    args = parser.parse_args(argv)
    if args.interrupt_after < 1:
        parser.error("--interrupt-after must be positive")
    if args.out.exists() and any(args.out.iterdir()):
        parser.error("--out must be an empty or new directory so evidence cannot be mixed")
    status = subprocess.run(
        ["git", "status", "--porcelain"],
        cwd=PROJECT_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    if status.stdout.strip():
        parser.error("capacity acceptance requires a clean, frozen source checkout")
    args.out.mkdir(parents=True, exist_ok=True)
    results: list[dict[str, Any]] = []
    for pages in args.stages:
        interruption = min(args.interrupt_after, max(1, pages // 2))
        try:
            results.append(
                run_stage(
                    args.out / str(pages),
                    pages=pages,
                    shard_size=args.shard_size,
                    interrupt_after=interruption,
                    consumers=not args.skip_consumers,
                    links_per_page=args.links_per_page,
                    forms_per_page=args.forms_per_page,
                    body_padding_bytes=args.body_padding_bytes,
                    comparison_compression=args.comparison_compression,
                    body_profile=args.body_profile,
                )
            )
        except BaseException as exc:
            import traceback

            (args.out / f"{pages}-failure.txt").write_text(traceback.format_exc(), encoding="utf-8")
            failed = {
                "status": "failed",
                "pages_requested": pages,
                "error": f"{type(exc).__name__}: {exc}",
                "elapsed_seconds": None,
            }
            results.append(failed)
            break
    manifest = {"format": "seohead.million-crawl-acceptance.v1", "results": results}
    (args.out / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return (
        0
        if len(results) == len(args.stages) and all(row["status"] == "passed" for row in results)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
