"""Network entry points of the cdn, security and mirrors checks, with a stubbed client.

The three MCP tools call these functions directly. No socket is opened: each test
replaces ``http_client`` (and ``doh`` for mirrors) in the module under test.
"""

import httpx
import pytest

from seohead.recon import cdn, mirrors, security


class _Client:
    """Answers GET by URL; an unlisted URL fails like an unreachable host."""

    def __init__(self, responses):
        self._responses = responses

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False

    def get(self, url, headers=None):
        if url not in self._responses:
            raise httpx.ConnectError(f"no route to {url}")
        return self._responses[url]

    def build_request(self, *_args, **_kwargs):
        raise RuntimeError("brotli probe is not stubbed")


def _response(url, status=200, headers=None):
    return httpx.Response(status, headers=headers or {}, request=httpx.Request("GET", url))


def _offline(*_args, **_kwargs):
    raise AssertionError("the entry point must not open a socket when the URL is invalid")


# ── check_cdn ───────────────────────────────────────────────────────────────


def test_cdn_rejects_an_empty_url_without_a_request(monkeypatch):
    monkeypatch.setattr(cdn, "http_client", _offline)
    result = cdn.check_cdn("")
    assert result["ok"] is False
    assert result["error"].startswith("not a valid URL")


def test_cdn_reports_cloudflare_cache_hit_from_the_response_headers(monkeypatch):
    url = "https://example.com/"
    headers = {"server": "cloudflare", "cf-cache-status": "HIT", "cache-control": "max-age=60"}
    client = _Client({url: _response(url, headers=headers)})
    monkeypatch.setattr(cdn, "http_client", lambda *_a, **_k: (client, False))

    result = cdn.check_cdn(url)

    assert result["ok"] is True
    assert result["cdn"] == "Cloudflare"
    assert result["cache"]["status_first"] == "cf-cache-status: HIT"
    assert result["cache"]["hit_first"] == "hit"
    assert result["cache"]["cache_control"]["max_age"] == 60
    # The transport section says the HTTP/2 negotiation could not be measured.
    assert result["transport"]["http_version_measurable"] is False
    assert result["transport"]["brotli_probe"] is None


def test_cdn_turns_a_network_failure_into_result_data(monkeypatch):
    client = _Client({})
    monkeypatch.setattr(cdn, "http_client", lambda *_a, **_k: (client, True))

    result = cdn.check_cdn("https://unreachable.example/")

    assert result["ok"] is False
    assert result["url"] == "https://unreachable.example/"
    assert "no route" in result["error"]


# ── check_security ──────────────────────────────────────────────────────────


def test_security_rejects_an_empty_url_without_a_request(monkeypatch):
    monkeypatch.setattr(security, "http_client", _offline)
    result = security.check_security("")
    assert result["ok"] is False
    assert result["error"].startswith("not a valid URL")


def test_security_full_header_set_scores_a_and_counts_csp_frame_ancestors(monkeypatch):
    url = "https://example.com/"
    headers = {
        "strict-transport-security": "max-age=31536000",
        "content-security-policy": "default-src 'self'; frame-ancestors 'none'",
        "x-content-type-options": "nosniff",
        "referrer-policy": "strict-origin",
        "permissions-policy": "geolocation=()",
        "server": "nginx",
    }
    client = _Client(
        {
            url: _response(url, headers=headers),
            "http://example.com/": _response("https://example.com/"),
        }
    )
    monkeypatch.setattr(security, "http_client", lambda *_a, **_k: (client, False))

    result = security.check_security(url)

    assert result["ok"] is True
    assert result["score"] == 100
    assert result["grade"] == "A"
    assert result["headers_missing"] == []
    assert result["headers_present"]["x-frame-options"] == "covered by CSP frame-ancestors"
    assert result["version_disclosure"] == {"server": "nginx"}
    assert result["https_redirect"]["upgrades"] is True
    assert result["exposed_paths"] is None  # probing is off unless asked for


def test_security_failed_http_redirect_probe_is_data_not_an_error(monkeypatch):
    url = "https://example.com/"
    client = _Client({url: _response(url)})  # the http:// probe is unlisted, so it fails
    monkeypatch.setattr(security, "http_client", lambda *_a, **_k: (client, False))

    result = security.check_security(url)

    assert result["ok"] is True
    assert result["grade"] == "F"
    assert result["https_redirect"]["checked"] is False
    assert "no route" in result["https_redirect"]["reason"]


def test_security_network_failure_returns_ok_false(monkeypatch):
    client = _Client({})
    monkeypatch.setattr(security, "http_client", lambda *_a, **_k: (client, False))

    result = security.check_security("https://unreachable.example/")

    assert result["ok"] is False
    assert "no route" in result["error"]


# ── check_mirrors ───────────────────────────────────────────────────────────


def test_mirrors_rejects_an_empty_url_without_dns_or_requests(monkeypatch):
    monkeypatch.setattr(mirrors, "doh", _offline)
    monkeypatch.setattr(mirrors, "http_client", _offline)
    result = mirrors.check_mirrors("")
    assert result["ok"] is False
    assert result["error"].startswith("not a valid URL")


@pytest.mark.parametrize("www_records", [([], []), (["203.0.113.5"], [])])
def test_mirrors_unreachable_site_is_reported_not_raised(monkeypatch, www_records):
    a, cname = www_records

    def fake_doh(_name, record_type):
        return {"A": a, "CNAME": cname}[record_type]

    monkeypatch.setattr(mirrors, "doh", fake_doh)
    monkeypatch.setattr(mirrors, "http_client", lambda *_a, **_k: (_Client({}), False))

    result = mirrors.check_mirrors("https://example.com")

    assert result["ok"] is True
    assert result["www_dns"]["resolvable"] is bool(a or cname)
    assert result["canonical_origin"] is None
