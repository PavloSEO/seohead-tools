"""Canonical and hreflang targets no link reached are probed once after the crawl (#987)."""

from __future__ import annotations

from tests.test_scan_hreflang_graph import BASE, _capture, _html, _issues, _Response


def _canonical_head(target: str) -> str:
    return f'<link rel="canonical" href="{target}">'


def test_hreflang_target_no_link_reached_is_probed_and_reported_when_404(tmp_path):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(("ru", "/"), ("en", "/en/"), body="<p>home</p>")),
            BASE + "en/": _Response("", status=404),
        },
        max_urls=5,
    )
    broken = _issues(audit, "HREFLANG_BROKEN_TARGET")
    assert [issue["target_url"] for issue in broken] == [BASE]
    target = broken[0]["details"]["broken_targets"][0]
    assert target["status_code"] == 404
    assert target["target_url"] == BASE + "en/"


def test_probed_hreflang_target_that_answers_200_is_not_reported(tmp_path):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(("ru", "/"), ("en", "/en/"), body="<p>home</p>")),
            BASE + "en/": _Response("<html><head></head><body>en</body></html>", status=200),
        },
        max_urls=5,
    )
    assert not _issues(audit, "HREFLANG_BROKEN_TARGET")


def test_canonical_target_no_link_reached_is_probed_and_reported_when_404(tmp_path):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(body='<a href="/a/">a</a>')),
            BASE + "a/": _Response(
                _html(extra_head=_canonical_head(BASE + "gone/"), body="<p>a</p>")
            ),
            BASE + "gone/": _Response("", status=404),
        },
        max_urls=5,
    )
    errors = _issues(audit, "CANONICAL_TARGET_ERROR")
    assert [issue["target_url"] for issue in errors] == [BASE + "a/"]
    assert errors[0]["details"]["canonical_target_url"] == BASE + "gone/"


def test_canonical_target_that_redirects_is_reported_as_redirect(tmp_path):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(body='<a href="/a/">a</a>')),
            BASE + "a/": _Response(
                _html(extra_head=_canonical_head(BASE + "old/"), body="<p>a</p>")
            ),
            BASE + "old/": _Response(
                "", status=301, headers={"content-type": "text/html", "location": "/new/"}
            ),
        },
        max_urls=5,
    )
    redirects = _issues(audit, "CANONICAL_TO_REDIRECT")
    assert [issue["target_url"] for issue in redirects] == [BASE + "a/"]
    assert not _issues(audit, "CANONICAL_TARGET_ERROR")


def test_canonical_target_that_answers_200_is_not_reported(tmp_path):
    audit = _capture(
        tmp_path,
        {
            BASE: _Response(_html(body='<a href="/a/">a</a>')),
            BASE + "a/": _Response(_html(extra_head=_canonical_head(BASE + "b/"), body="<p>a</p>")),
            BASE + "b/": _Response(_html(body="<p>b</p>")),
        },
        max_urls=5,
    )
    assert not _issues(audit, "CANONICAL_TARGET_ERROR")
    assert not _issues(audit, "CANONICAL_TO_REDIRECT")
