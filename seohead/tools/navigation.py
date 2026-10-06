"""Bounded observed navigation evidence; unknown causes are never inferred as JS."""

from __future__ import annotations

import time
from typing import Any
from urllib.parse import urljoin, urlsplit

SCHEMA = "seohead.navigation.v1"
EVENT_CAP = 32
KINDS = {
    "initial_http_navigation",
    "document_navigation",
    "script_navigation",
    "spa_history_change",
    "fragment_navigation",
    "http_redirect",
    "anchor_navigation",
    "form_navigation",
    "refresh_navigation",
}
OBSERVATIONS = {"playwright_frame", "http_response", "cdp_frame", "cdp_history", "cdp_requested"}


def validate_navigation(value: Any, *, stored: bool = False, resolve_url=None) -> None:
    """Validate the versioned adjunct, optionally checking retained URL references."""
    if not isinstance(value, dict) or value.get("schema") != SCHEMA:
        raise ValueError("navigation evidence schema is invalid")
    if stored and set(value) != {
        "schema",
        "state",
        "reason",
        "events_omitted",
        "interaction_policy",
        "policy",
        "events",
    }:
        raise ValueError("retained navigation fields are invalid")
    if value.get("state") not in {"complete", "partial", "unavailable"}:
        raise ValueError("navigation evidence state is invalid")
    if value.get("interaction_policy") != "no_clicks" or value.get("policy") != "pinned_http":
        raise ValueError("navigation evidence policy is invalid")
    if type(value.get("reason")) is not str or len(value["reason"]) > 128:
        raise ValueError("navigation evidence reason is invalid")
    events = value.get("events")
    if type(events) is not list or len(events) > EVENT_CAP:
        raise ValueError("navigation evidence events exceed the bound")
    if type(value.get("events_omitted")) is not int or value["events_omitted"] < 0:
        raise ValueError("navigation omitted count is invalid")
    if value["state"] == "complete" and (value["events_omitted"] or value["reason"]):
        raise ValueError("complete navigation evidence has omissions")
    if value["state"] != "complete" and not value["reason"]:
        raise ValueError("incomplete navigation evidence needs a reason")
    if value["state"] == "complete" and not events:
        raise ValueError("complete navigation evidence requires an observed event")
    previous = -1
    for event in events:
        names = {"source_url_id", "destination_url_id"} if stored else {"source", "destination"}
        if not isinstance(event, dict) or set(event) != names | {
            "elapsed_ms",
            "kind",
            "observation",
            "user_click",
            "status_code",
        }:
            raise ValueError("navigation event fields are invalid")
        elapsed = event["elapsed_ms"]
        if type(elapsed) is not int or elapsed < previous:
            raise ValueError("navigation event timing is invalid")
        previous = elapsed
        if event["kind"] not in KINDS or event["observation"] not in OBSERVATIONS:
            raise ValueError("navigation event cause is invalid")
        allowed = {
            "initial_http_navigation": {"cdp_frame", "playwright_frame"},
            "document_navigation": {"cdp_frame", "playwright_frame", "cdp_history"},
            "http_redirect": {"http_response"},
            "spa_history_change": {"cdp_history"},
            "fragment_navigation": {"cdp_history"},
        }.get(event["kind"], {"cdp_requested"})
        if event["observation"] not in allowed:
            raise ValueError("navigation cause disagrees with its observation")
        if event["user_click"] is not None:
            raise ValueError("navigation user-click cause was not observed")
        status = event["status_code"]
        if (
            event["kind"] == "http_redirect"
            and (type(status) is not int or not 300 <= status < 400)
        ) or (event["kind"] != "http_redirect" and status is not None):
            raise ValueError("navigation redirect status is invalid")
        for name in names:
            url = event[name]
            if stored:
                if type(url) is not int or url < 1:
                    raise ValueError("navigation URL reference is invalid")
                if resolve_url is None:
                    continue
                url = resolve_url(url)
            if type(url) is not str or len(url) > 16384:
                raise ValueError("navigation URL is invalid")
            parts = urlsplit(url)
            if (
                parts.scheme not in {"http", "https"}
                or not parts.hostname
                or parts.username
                or parts.password
            ):
                raise ValueError("navigation URL is unsafe")


def retain_navigation(raw: dict[str, Any], intern_url) -> dict[str, Any]:
    """Return storage-safe event URL identities without duplicating URL strings.

    Pre-versioned renderers are explicitly unavailable rather than retroactively
    asserting that their old heuristic event classification was observed.
    """
    if raw.get("schema") not in {None, SCHEMA}:
        raise ValueError("navigation evidence schema is unsupported")
    if raw.get("schema") is None:
        raw = {
            "schema": SCHEMA,
            "state": "unavailable",
            "reason": "legacy_navigation_not_captured",
            "events": [],
            "events_omitted": 0,
            "interaction_policy": "no_clicks",
            "policy": "pinned_http",
        }
    validate_navigation(raw)
    result = {
        name: raw[name]
        for name in ("schema", "state", "reason", "events_omitted", "interaction_policy", "policy")
    }
    result["events"] = [
        {
            **{key: item for key, item in event.items() if key not in {"source", "destination"}},
            "source_url_id": intern_url(event["source"]),
            "destination_url_id": intern_url(event["destination"]),
        }
        for event in raw["events"]
    ]
    return result


def expand_navigation(stored: dict[str, Any], resolve_url) -> dict[str, Any]:
    """Resolve retained identities for bounded public reads and exports."""
    validate_navigation(stored, stored=True, resolve_url=resolve_url)
    return {
        **stored,
        "events": [
            {
                **{
                    key: item
                    for key, item in event.items()
                    if key not in {"source_url_id", "destination_url_id"}
                },
                "source": resolve_url(event["source_url_id"]),
                "destination": resolve_url(event["destination_url_id"]),
            }
            for event in stored["events"]
        ],
    }


class NavigationCapture:
    """Observe the main document only, with a constant event and cause budget."""

    def __init__(self, target: str, wait: str, timeout: float):
        self.started = time.monotonic()
        self.current = target
        self.committed = False
        self.main_frame_id = None
        self.pending: tuple[str, str] | None = None
        self.cdp = False
        self.data = {
            "schema": SCHEMA,
            "requested_url": target,
            "final_url": target,
            "wait_until": wait,
            "timeout_seconds": timeout,
            "interaction_policy": "no_clicks",
            "policy": "pinned_http",
            "events": [],
            "events_omitted": 0,
            "state": "unavailable",
            "reason": "browser_not_started",
        }

    def add(self, destination: str, kind: str, observation: str, *, source=None, status=None):
        parts = urlsplit(destination)
        if (
            parts.scheme not in {"http", "https"}
            or not parts.hostname
            or parts.username
            or parts.password
            or len(destination) > 16384
        ):
            self.data["events_omitted"] += 1
            return
        if len(self.data["events"]) >= EVENT_CAP:
            self.data["events_omitted"] += 1
        else:
            self.data["events"].append(
                {
                    "source": source or self.current,
                    "destination": destination,
                    "elapsed_ms": max(0, round((time.monotonic() - self.started) * 1000)),
                    "kind": kind,
                    "observation": observation,
                    "status_code": status,
                    "user_click": None,
                }
            )
        self.current = destination
        self.data["final_url"] = destination

    def attach(self, context, page):
        self.page = page
        page.on("response", self.response)
        page.on("framenavigated", self.frame)
        try:
            session = context.new_cdp_session(page)
            session.send("Page.enable")
            tree = session.send("Page.getFrameTree")
            self.main_frame_id = tree["frameTree"]["frame"]["id"]
            session.on("Page.frameRequestedNavigation", self.requested)
            session.on("Page.frameNavigated", self.document)
            session.on("Page.navigatedWithinDocument", self.same_document)
            self.cdp = True
        except Exception:
            self.cdp = False
        self.data.update(state="partial", reason="capture_in_progress")

    def requested(self, event):
        if event.get("frameId") == self.main_frame_id:
            self.pending = (event.get("url", ""), event.get("reason", ""))

    def document(self, event):
        frame = event.get("frame", {})
        if frame.get("id") != self.main_frame_id or frame.get("parentId"):
            return
        destination = frame.get("url", "")
        reason = self.pending[1] if self.pending and self.pending[0] == destination else ""
        kind = {
            "scriptInitiated": "script_navigation",
            "anchorClick": "anchor_navigation",
            "formSubmissionGet": "form_navigation",
            "formSubmissionPost": "form_navigation",
            "metaTagRefresh": "refresh_navigation",
            "httpHeaderRefresh": "refresh_navigation",
        }.get(reason)
        kind = kind or ("document_navigation" if self.committed else "initial_http_navigation")
        self.add(destination, kind, "cdp_requested" if reason else "cdp_frame")
        self.committed = True
        self.pending = None

    def same_document(self, event):
        if event.get("frameId") != self.main_frame_id:
            return
        kind = {"historyApi": "spa_history_change", "fragment": "fragment_navigation"}.get(
            event.get("navigationType"), "document_navigation"
        )
        self.add(event.get("url", ""), kind, "cdp_history")
        self.pending = None

    def frame(self, frame):
        if self.cdp or frame is not getattr(self.page, "main_frame", None):
            return
        self.add(
            str(frame.url),
            "document_navigation" if self.committed else "initial_http_navigation",
            "playwright_frame",
        )
        self.committed = True

    def response(self, response):
        request = response.request
        if not request.is_navigation_request() or request.frame is not self.page.main_frame:
            return
        status = response.status
        if 300 <= status < 400:
            location = response.headers.get("location")
            if location:
                self.add(
                    urljoin(request.url, location),
                    "http_redirect",
                    "http_response",
                    source=request.url,
                    status=status,
                )

    def finish(self, *, success: bool):
        reason = (
            ""
            if success and self.cdp
            else "cause_observation_unsupported"
            if success
            else "render_incomplete"
        )
        if self.data["events_omitted"]:
            reason = "event_budget_exhausted"
        if not self.data["events"]:
            reason = "navigation_not_observed"
        self.data.update(
            state="partial"
            if reason and self.data["events"]
            else "unavailable"
            if reason
            else "complete",
            reason=reason,
        )
