"""Explicit URL-list spooling through the existing native scan writer.

This adapter owns storage only. ``collect_urls`` still owns requests, parsing,
robots policy, optional destination walks and their failure semantics.
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict
from pathlib import Path

from seohead.crawl.collect import CrawlResult, PageRecord, collect_urls
from seohead.crawl.sqlite_adapter import _runtime
from seohead.storage.exports import _page_rows
from seohead.storage.native_scan import NativeScan


def context(kind, payload, key="run"):
    return {
        "kind": kind,
        "item_key": key,
        "payload_version": "scan_context.v1",
        "payload_json": json.dumps(payload),
        "completeness": "complete",
        "reason": "",
    }


def is_list_scan(con) -> bool:
    return (
        con.execute(
            "SELECT 1 FROM context_items WHERE kind='list_mode' AND item_key='run'"
        ).fetchone()
        is not None
    )


class _Pages:
    def __init__(self, owner):
        self.owner = owner
        self.count = owner.scan.con.execute("SELECT COUNT(*) FROM pages").fetchone()[0]

    def __len__(self):
        return self.count

    def __iter__(self):
        return (PageRecord(**row) for row in _page_rows(self.owner.scan.con))

    def append(self, record):
        self.owner.scan.commit_page(
            self.owner.lease,
            asdict(record),
            runtime=self.owner.runtime(),
            captures=self.owner.captures,
        )
        self.count += 1
        self.owner.captures.clear()


class _Blocked:
    def __init__(self, owner):
        self.owner = owner
        self.count = owner.scan.con.execute(
            "SELECT COUNT(*) FROM context_items WHERE kind='robots_blocked_url'"
        ).fetchone()[0]

    def __len__(self):
        return self.count

    def __iter__(self):
        return (
            row[0]
            for row in self.owner.scan.con.execute(
                "SELECT u.url FROM frontier f JOIN urls u USING(url_id) JOIN context_items c "
                "ON c.kind='robots_blocked_url' AND c.item_key='url:'||u.url_id ORDER BY f.queue_ordinal"
            )
        )

    def append(self, url):
        owner = self.owner
        item = context(
            "robots_blocked_url",
            {
                "url_id": owner.lease.url_id,
                "token": owner.settings["robots"]["user_agent_token"],
                "policy": owner.settings["robots"]["policy"],
            },
            f"url:{owner.lease.url_id}",
        )
        exists = owner.scan.read_context("robots_blocked_url", item["item_key"]) is not None
        if owner.settings["robots"]["policy"] == "respect":
            owner.scan.exclude_lease(
                owner.lease, "blocked_by_robots", runtime=owner.runtime(), context=[item]
            )
        else:
            owner.scan.write_context([item])
        self.count += not exists


class _Robots:
    def __init__(self, owner):
        self.owner = owner
        self.scan = owner.scan
        self.reason = ""

    def fetch(self, url, **kwargs):
        from seohead.crawl.capture import decode_entity
        from seohead.crawl.collect import fetch_one

        self.reason = ""
        captured = []
        limit = self.owner.settings["limits"]["max_response_bytes"]
        record, _ = fetch_one(
            url,
            capture_observer=captured.append,
            capture_max_bytes=limit,
            max_response_bytes=limit,
            **kwargs,
        )
        if (
            record.error
            or record.body_unavailable
            or record.status_code is None
            or record.status_code >= 429
        ):
            self.reason = "robots response unavailable: " + (
                record.body_unavailable or record.error or str(record.status_code)
            )
            return ""
        if not 200 <= record.status_code < 300 or not captured or captured[-1].entity_bytes is None:
            return ""
        return decode_entity(captured[-1].entity_bytes, captured[-1].content_type)[0]

    def __contains__(self, origin):
        return (
            self.scan.read_context("list_robots", json.dumps(origin, separators=(",", ":")))
            is not None
        )

    def __getitem__(self, origin):
        return self.scan.read_context("list_robots", json.dumps(origin, separators=(",", ":")))[
            "parsed"
        ]

    def __setitem__(self, origin, parsed):
        item = context(
            "list_robots",
            {"origin": list(origin), "parsed": parsed},
            json.dumps(origin, separators=(",", ":")),
        )
        if self.reason:
            item.update(completeness="unavailable", reason=self.reason)
        self.scan.write_context([item])


class ListScan:
    """One native writer, bounded enqueue batches, and one in-flight list row."""

    def __init__(
        self, path, urls, settings, *, version, revision, runtime_versions, clock=time.monotonic
    ):
        if settings["cache"]["mode"] != "off":
            raise ValueError("native list collection requires cache.mode=off")
        if settings["rendering"]["mode"] != "raw" or settings["resources"]["fetch"]:
            raise ValueError(
                "native list collection is static-only; rendering and resource fetching are unavailable"
            )
        self.path = Path(path)
        self.settings = settings
        self.clock = clock
        self.started = clock()
        self.resumed = self.path.exists()
        self.urls = urls
        self.scan = (
            NativeScan.open(
                path,
                expected_start_url="url-list",
                expected_config=settings,
                expected_writer_revision=revision,
            )
            if self.resumed
            else NativeScan.create(
                path,
                start_url="url-list",
                config=settings,
                writer_version=version,
                writer_revision=revision,
                runtime_versions=runtime_versions,
                format_version=settings["storage"]["format_version"],
                limitations=[
                    "list mode: explicit URLs only; no link discovery or graph/form observations"
                ],
            )
        )
        self.captures = []
        self.capture_bytes = 0
        self.lease = None
        self.result = CrawlResult()

    def __enter__(self):
        try:
            if self.settings["cache"]["mode"] != "off":
                raise ValueError("native list collection requires cache.mode=off")
            if not self.resumed:
                self.scan.write_context([context("list_mode", {"mode": "list"})])
            elif not is_list_scan(self.scan.con):
                raise ValueError("scan is not an explicit URL-list capture")
            saved_input = self.scan.read_context("list_input")
            saved = self.scan.read_context("list_elapsed") or {"seconds": 0.0, "active": False}
            runtime = self.scan.resume_snapshot()["runtime"]
            self.elapsed_before = max(saved["seconds"], runtime["elapsed_seconds"])
            self.interrupted_clock = saved["active"]
            if self.resumed and saved_input is None:
                raise ValueError("list input preparation was interrupted; use a new output")
            if self.urls is not None:
                digest = hashlib.sha256()
                count = 0
                excluded = {"blank": 0, "url_too_long": 0}
                batch = []
                for raw in self.urls:
                    maximum = self.settings["limits"]["max_crawl_seconds"]
                    if maximum and self.elapsed() >= maximum:
                        self.scan.interrupt("input_duration_limit")
                        self.save_elapsed(False)
                        raise ValueError(
                            "list input preparation exceeded the cumulative time budget; no request was issued"
                        )
                    url = (raw or "").strip()
                    digest.update((json.dumps(url, ensure_ascii=False) + "\n").encode())
                    count += 1
                    if not url:
                        excluded["blank"] += 1
                        continue
                    if (
                        self.settings["limits"]["max_url_length"]
                        and len(url) > self.settings["limits"]["max_url_length"]
                    ):
                        excluded["url_too_long"] += 1
                        continue
                    if not self.resumed:
                        batch.append((url, 0))
                        if len(batch) == 256:
                            self.scan.enqueue(batch)
                            batch.clear()
                identity = {"sha256": digest.hexdigest(), "raw_count": count}
                if self.resumed and any(identity[key] != saved_input[key] for key in identity):
                    raise ValueError("explicit list input changed; refusing mixed-input resume")
                if not self.resumed:
                    if batch:
                        self.scan.enqueue(batch)
                    accepted = self.scan.resume_snapshot()["counts"]["queued"]
                    identity["counts"] = {
                        **excluded,
                        "accepted": accepted,
                        "duplicate": count - sum(excluded.values()) - accepted,
                    }
                    self.scan.write_context([context("list_input", identity)])
            elif not self.resumed:
                raise ValueError("a new list scan requires explicit input")
            self.pages = _Pages(self)
            self.blocked = _Blocked(self)
            self.robots = _Robots(self)
            self.requests_used = runtime["throttle"]["requests_used"]
            self.saved_runtime = runtime
            return self
        except BaseException:
            self.scan.close()
            raise
        finally:
            close = getattr(self.urls, "close", None)
            if close is not None:
                close()

    def __exit__(self, *_):
        self.scan.close()

    def elapsed(self):
        return self.elapsed_before + max(0, self.clock() - self.started)

    def save_elapsed(self, active):
        if self.interrupted_clock and self.settings["limits"]["max_crawl_seconds"]:
            active = True  # Unknown final interval must not become fresh time on a second resume.
        self.scan.write_context(
            [
                context(
                    "list_elapsed",
                    {
                        "schema_version": "list_elapsed.v1",
                        "seconds": self.elapsed(),
                        "active": active,
                    },
                )
            ]
        )

    def bind(self, result, throttle, dispatch_gate):
        self.result, self.throttle, self.dispatch_gate = result, throttle, dispatch_gate
        state = dict(self.saved_runtime["throttle"])
        state.pop("requests_used")
        throttle.restore_state(state)
        throttle.timeouts = self.saved_runtime["circuit_timeout_streak"]
        throttle.server_errors = self.saved_runtime["circuit_server_error_streak"]
        result.pages = self.pages
        result.robots_blocked = self.blocked
        result.resumed = self.resumed

    def runtime(self):
        return _runtime(
            self.throttle,
            max_depth=0,
            elapsed=self.elapsed(),
            timeouts=self.throttle.timeouts,
            server_errors=self.throttle.server_errors,
            robots_delay=None,
            dispatch_gate=self.dispatch_gate,
        )

    def dispatched(self, _name, _payload):
        # The gate calls this before sleeping or issuing a request, so a killed
        # worker cannot regain its reserved HTTP attempt on reopen.
        self.requests_used += 1
        self.scan.record_request_count(self.requests_used)

    def pending(self):
        while leases := self.scan.claim(1):
            self.lease = leases[0]
            self.captures.clear()
            self.capture_bytes = 0
            yield self.lease.url

    def capture(self, event):
        self.capture_bytes += len(event.entity_bytes or b"")
        if self.capture_bytes > 64 * 1024 * 1024 or len(self.captures) >= 1000:
            raise ValueError("list row response capture exceeds the native atomic budget")
        self.captures.append(event)

    def collect(self, **kwargs):
        self.scan.recover_inflight()
        self.scan.begin_collection(links_available=False)
        self.save_elapsed(True)
        try:
            result = collect_urls((), spool=self, clock=self.clock, **kwargs)
        except KeyboardInterrupt:
            result = self.result
            result.partial, result.finish_reason, result.stopped_reason = (
                True,
                "interrupted",
                "operator interrupted (SIGINT)",
            )
        finally:
            self.scan.recover_inflight()
            # Cancellation may arrive after the transaction committed but
            # before the adapter advanced its cached scalar counters.
            self.pages.count = self.scan.resume_snapshot()["counts"]["pages"]
            self.blocked.count = self.scan.con.execute(
                "SELECT COUNT(*) FROM context_items WHERE kind='robots_blocked_url'"
            ).fetchone()[0]
            self.save_elapsed(False)
        if result.partial:
            self.scan.interrupt(result.finish_reason)
        result.limitations = [
            "list mode: no link discovery, no sitemap expansion",
            "static HTML only: no JavaScript rendering",
            "explicit URL-list capture retains no link-edge or form observations",
        ]
        missing_robots = self.scan.con.execute(
            "SELECT COUNT(*) FROM context_items WHERE kind='list_robots' AND completeness='unavailable'"
        ).fetchone()[0]
        if missing_robots:
            result.limitations.append(
                f"robots policy unavailable for {missing_robots} list origins; those hosts retain the legacy unrestricted fallback"
            )
        return result
