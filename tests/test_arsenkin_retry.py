"""ArsenkinClient._post retry behaviour: 429 backs off and retries, network errors on billed
endpoints fail without resending, and non-billed network errors retry until exhausted.

time.sleep is stubbed so the backoff schedule is asserted instead of waited for.
"""

from __future__ import annotations

import io
import json
import urllib.error

import pytest

from seohead.data_sources import arsenkin


class _OkResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _http_error(code: int, body: str):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body.encode()))


@pytest.fixture()
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(arsenkin.time, "sleep", lambda seconds: calls.append(seconds))
    return calls


@pytest.fixture()
def client():
    return arsenkin.ArsenkinClient(token="t", limiter=arsenkin.RateLimiter(max_calls=1000))


def test_http_429_backs_off_then_succeeds(monkeypatch, sleeps, client):
    replies = [
        _http_error(429, "slow down"),
        _http_error(429, "slow down"),
        _OkResponse(b'{"ok": 1}'),
    ]
    seen = []

    def fake_urlopen(request, timeout):
        seen.append(request)
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(arsenkin.urllib.request, "urlopen", fake_urlopen)

    assert client._post("ping", {}) == {"ok": 1}
    assert len(seen) == 3
    assert sleeps == [2, 3]  # 2**0+1, 2**1+1


def test_429_inside_json_body_is_retried(monkeypatch, sleeps, client):
    bodies = [json.dumps({"code": "429", "error": "limit"}), json.dumps({"code": "OK"})]

    def fake_urlopen(request, timeout):
        return _OkResponse(bodies.pop(0).encode())

    monkeypatch.setattr(arsenkin.urllib.request, "urlopen", fake_urlopen)

    assert client._post("ping", {}) == {"code": "OK"}
    assert sleeps == [2]


def test_billed_network_error_is_not_resent(monkeypatch, sleeps, journal_path, client):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise urllib.error.URLError("connection reset")

    monkeypatch.setattr(arsenkin.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(arsenkin.ArsenkinError) as exc:
        client._post("set", {}, billed=True)

    assert exc.value.code == "NETWORK"
    assert len(calls) == 1
    assert sleeps == []


def test_unbilled_network_error_retries_until_exhausted(monkeypatch, sleeps, client):
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request)
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(arsenkin.urllib.request, "urlopen", fake_urlopen)

    with pytest.raises(arsenkin.ArsenkinError) as exc:
        client._post("ping", {}, retries=3)

    assert exc.value.code == "NETWORK"
    assert len(calls) == 3
    assert sleeps == [2, 3, 5]


@pytest.fixture()
def journal_path(monkeypatch, tmp_path):
    path = tmp_path / "spend.jsonl"
    monkeypatch.setenv("SEOHEAD_SPEND_LOG", str(path))
    return path
