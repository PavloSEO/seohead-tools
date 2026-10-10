"""Navigation causes, bounds and retained identities stay explicit offline."""

from types import SimpleNamespace

import pytest

from seohead.checks.navigation import (
    EVENT_CAP,
    NavigationCapture,
    expand_navigation,
    retain_navigation,
    validate_navigation,
)


def capture():
    item = NavigationCapture("https://example.test/", "load", 10)
    item.main_frame_id = "main"
    item.cdp = True
    return item


def test_cdp_separates_script_anchor_history_fragment_and_unknown():
    item = capture()
    item.document({"frame": {"id": "main", "url": "https://example.test/"}})
    for reason, path in [("scriptInitiated", "script"), ("anchorClick", "anchor"), ("", "unknown")]:
        item.requested({"frameId": "main", "url": f"https://example.test/{path}", "reason": reason})
        item.document({"frame": {"id": "main", "url": f"https://example.test/{path}"}})
    item.same_document(
        {
            "frameId": "main",
            "url": "https://example.test/unknown?q=1",
            "navigationType": "historyApi",
        }
    )
    item.same_document(
        {
            "frameId": "main",
            "url": "https://example.test/unknown?q=1#part",
            "navigationType": "fragment",
        }
    )
    item.finish(success=True)
    assert [e["kind"] for e in item.data["events"]] == [
        "initial_http_navigation",
        "script_navigation",
        "anchor_navigation",
        "document_navigation",
        "spa_history_change",
        "fragment_navigation",
    ]
    assert all(e["user_click"] is None for e in item.data["events"])
    validate_navigation(item.data)


def test_failed_loop_keeps_bounded_events_and_correct_current_source():
    item = capture()
    for n in range(EVENT_CAP + 5):
        item.add(f"https://example.test/{n % 2}", "script_navigation", "cdp_requested")
    item.finish(success=False)
    assert len(item.data["events"]) == EVENT_CAP
    assert item.data["events_omitted"] == 5
    assert item.data["state"] == "partial"
    assert item.data["reason"] == "event_budget_exhausted"
    validate_navigation(item.data)


def test_redirect_response_has_http_status_and_does_not_include_iframes():
    item = capture()
    frame = object()
    item.page = SimpleNamespace(main_frame=frame)
    request = SimpleNamespace(
        url="https://example.test/", frame=frame, is_navigation_request=lambda: True
    )
    item.response(SimpleNamespace(request=request, status=302, headers={"location": "/new"}))
    request.frame = object()
    item.response(SimpleNamespace(request=request, status=301, headers={"location": "/iframe"}))
    assert len(item.data["events"]) == 1
    event = item.data["events"][0]
    assert (event["kind"], event["status_code"], event["destination"]) == (
        "http_redirect",
        302,
        "https://example.test/new",
    )


def test_retention_roundtrip_and_tampering_rejection():
    item = capture()
    item.add("https://example.test/one?x=1#frag", "spa_history_change", "cdp_history")
    item.finish(success=True)
    urls = {}

    def intern(url):
        return urls.setdefault(url, len(urls) + 1)

    stored = retain_navigation(item.data, intern)
    reverse = {number: url for url, number in urls.items()}
    expanded = expand_navigation(stored, reverse.get)
    assert expanded["events"] == item.data["events"]
    stored["events"][0]["destination_url_id"] = 900
    with pytest.raises(ValueError, match="URL"):
        expand_navigation(stored, reverse.get)


def test_legacy_is_unavailable_and_forged_click_is_rejected():
    legacy = retain_navigation({"events": [{"kind": "script_navigation"}]}, lambda _: 1)
    assert legacy["state"] == "unavailable"
    assert legacy["events"] == []
    item = capture()
    item.add("https://example.test/new", "document_navigation", "cdp_frame")
    item.finish(success=False)
    item.data["events"][0]["user_click"] = False
    with pytest.raises(ValueError, match="user-click"):
        validate_navigation(item.data)


def test_unmapped_requested_reasons_stay_valid_observations():
    item = capture()
    item.document({"frame": {"id": "main", "url": "https://example.test/"}})
    for reason, path in [("pageBlockInterstitial", "blocked"), ("reload", "reloaded")]:
        url = f"https://example.test/{path}"
        item.requested({"frameId": "main", "url": url, "reason": reason})
        item.document({"frame": {"id": "main", "url": url}})
    item.finish(success=True)
    assert [e["kind"] for e in item.data["events"]] == [
        "initial_http_navigation",
        "document_navigation",
        "refresh_navigation",
    ]
    validate_navigation(item.data)
