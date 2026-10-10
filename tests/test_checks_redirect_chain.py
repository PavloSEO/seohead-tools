"""Coverage for redirects.check_chain and _check_step via httpx.MockTransport.

No network: http_client is replaced with a client on MockTransport, follow_redirects=False.
"""

from __future__ import annotations

import httpx
import pytest

from seohead.checks import redirects


def _client_for(monkeypatch, handler):
    def _factory(timeout, **kwargs):
        assert kwargs.get("follow_redirects") is False
        return httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=False), False

    monkeypatch.setattr(redirects, "http_client", _factory)


def test_chain_301_then_200_stops_at_final_hop(monkeypatch):
    routes = {
        "https://site.test/old": (301, {"location": "https://site.test/new"}),
        "https://site.test/new": (200, {}),
    }

    def handler(request):
        status, headers = routes[str(request.url)]
        return httpx.Response(status, headers=headers)

    _client_for(monkeypatch, handler)

    chain = redirects.check_chain("https://site.test/old")

    assert [h["url"] for h in chain] == ["https://site.test/old", "https://site.test/new"]
    assert chain[0] == {
        "url": "https://site.test/old",
        "status": 301,
        "location": "https://site.test/new",
        "ok": True,
    }
    assert chain[-1]["status"] == 200 and chain[-1]["ok"] is True


def test_relative_location_is_resolved_against_current_url(monkeypatch):
    def handler(request):
        if request.url.path == "/start":
            return httpx.Response(302, headers={"location": "/landing"})
        return httpx.Response(200)

    _client_for(monkeypatch, handler)

    chain = redirects.check_chain("https://site.test/start")

    assert chain[1]["url"] == "https://site.test/landing"


def test_redirect_loop_is_detected_not_followed_forever(monkeypatch):
    def handler(request):
        other = "/b" if request.url.path == "/a" else "/a"
        return httpx.Response(301, headers={"location": other})

    _client_for(monkeypatch, handler)

    chain = redirects.check_chain("https://site.test/a", {"max_hops": 10})

    assert chain[-1]["error"] == "Redirect loop detected"
    assert chain[-1]["status"] == 0
    assert chain[-1]["ok"] is False
    assert len(chain) == 3  # /a -> /b -> /a (loop)


def test_hop_limit_is_enforced_and_reported(monkeypatch):
    counter = {"n": 0}

    def handler(request):
        counter["n"] += 1
        return httpx.Response(301, headers={"location": f"/step{counter['n']}"})

    _client_for(monkeypatch, handler)

    chain = redirects.check_chain("https://site.test/", {"max_hops": 2})

    assert len(chain) == 3
    assert chain[-1]["error"] == "Redirect limit exceeded (2)"
    assert counter["n"] == 2


def test_max_hops_is_capped_at_ten(monkeypatch):
    counter = {"n": 0}

    def handler(request):
        counter["n"] += 1
        return httpx.Response(301, headers={"location": f"/p{counter['n']}"})

    _client_for(monkeypatch, handler)

    redirects.check_chain("https://site.test/", {"max_hops": 500})

    assert counter["n"] == 10


def test_network_error_becomes_error_hop(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    _client_for(monkeypatch, handler)

    chain = redirects.check_chain("https://site.test/down")

    assert len(chain) == 1
    assert chain[0]["status"] == 0
    assert chain[0]["ok"] is False
    assert "connection refused" in chain[0]["error"]


def test_timeout_is_reported_as_timeout(monkeypatch):
    def handler(request):
        raise httpx.ReadTimeout("slow", request=request)

    _client_for(monkeypatch, handler)

    chain = redirects.check_chain("https://site.test/slow")

    assert chain[0]["error"] == "Timeout"


def test_redirect_without_location_ends_chain(monkeypatch):
    def handler(request):
        return httpx.Response(301)

    _client_for(monkeypatch, handler)

    chain = redirects.check_chain("https://site.test/noloc")

    assert len(chain) == 1
    assert chain[0]["status"] == 301
    assert chain[0]["location"] is None


def test_head_is_default_method_and_user_agent_is_sent(monkeypatch):
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["ua"] = request.headers["user-agent"]
        return httpx.Response(200)

    _client_for(monkeypatch, handler)

    redirects.check_chain("https://site.test/", {"user_agent": "UA-test/1"})

    assert seen == {"method": "HEAD", "ua": "UA-test/1"}


def test_check_step_reports_status_and_ok_for_4xx(monkeypatch):
    def handler(request):
        return httpx.Response(404)

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        step = redirects._check_step(client, "https://site.test/gone", {"method": "get"})

    assert step == {
        "url": "https://site.test/gone",
        "status": 404,
        "location": None,
        "ok": False,
    }


def test_setup_failure_returns_single_error_hop(monkeypatch):
    def _boom(timeout, **kwargs):
        raise RuntimeError("no client")

    monkeypatch.setattr(redirects, "http_client", _boom)

    chain = redirects.check_chain("https://site.test/x")

    assert chain == [
        {
            "url": "https://site.test/x",
            "status": 0,
            "location": None,
            "ok": False,
            "error": "no client",
        }
    ]


@pytest.mark.parametrize("bad", ["abc", None])
def test_bad_max_hops_falls_back_to_default(monkeypatch, bad):
    counter = {"n": 0}

    def handler(request):
        counter["n"] += 1
        return httpx.Response(301, headers={"location": f"/q{counter['n']}"})

    _client_for(monkeypatch, handler)

    redirects.check_chain("https://site.test/", {"max_hops": bad})

    assert counter["n"] == 10
