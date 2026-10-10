"""yandex_cloud._Base._request retry behaviour: 429/500/503 back off and retry, a billed
network error fails without resending, and an unbilled one retries until exhausted.

Keys are passed explicitly so no credential file is read. time.sleep is stubbed.
"""

from __future__ import annotations

import io
import urllib.error

import pytest

from seohead.data_sources import yandex_cloud


class _OkResponse(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def _http_error(code: int, body: str):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body.encode()))


@pytest.fixture()
def sleeps(monkeypatch):
    calls: list[float] = []
    monkeypatch.setattr(yandex_cloud.time, "sleep", lambda seconds: calls.append(seconds))
    return calls


@pytest.fixture()
def client(monkeypatch, tmp_path):
    monkeypatch.setenv("SEOHEAD_SPEND_LOG", str(tmp_path / "spend.jsonl"))
    # rps this high makes the spacing wait negative, so only backoff sleeps are recorded.
    return yandex_cloud._Base(api_key="k", folder_id="f", rps=1e9)


@pytest.mark.parametrize("code", [429, 500, 503])
def test_transient_http_error_backs_off_then_succeeds(monkeypatch, sleeps, client, code):
    replies = [_http_error(code, "transient"), _OkResponse(b'{"done": true}')]

    def fake_urlopen(request, timeout, context):
        reply = replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(yandex_cloud, "open_no_redirect", fake_urlopen)

    assert client._request("https://x", method="GET") == (200, {"done": True})
    assert sleeps == [2]


def test_permanent_http_error_is_returned_not_retried(monkeypatch, sleeps, client):
    calls = []

    def fake_urlopen(request, timeout, context):
        calls.append(request)
        raise _http_error(400, '{"message": "bad request"}')

    monkeypatch.setattr(yandex_cloud, "open_no_redirect", fake_urlopen)

    status, payload = client._request("https://x", method="GET")

    assert status == 400
    assert payload == {"message": "bad request"}
    assert len(calls) == 1
    assert sleeps == []


def test_billed_network_error_is_not_resent(monkeypatch, sleeps, client):
    calls = []

    def fake_urlopen(request, timeout, context):
        calls.append(request)
        raise urllib.error.URLError("reset")

    monkeypatch.setattr(yandex_cloud, "open_no_redirect", fake_urlopen)

    with pytest.raises(yandex_cloud.NetworkAmbiguousError):
        client._request("https://x", body={"q": 1}, billed=True)

    assert len(calls) == 1
    assert sleeps == []


def test_unbilled_network_error_retries_until_exhausted(monkeypatch, sleeps, client):
    calls = []

    def fake_urlopen(request, timeout, context):
        calls.append(request)
        raise urllib.error.URLError("timed out")

    monkeypatch.setattr(yandex_cloud, "open_no_redirect", fake_urlopen)

    status, message = client._request("https://x", method="GET", retries=3)

    assert status == 0
    assert message.startswith("network:")
    assert len(calls) == 3
    assert sleeps == [2, 3]  # backoff only between the three attempts
