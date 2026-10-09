"""Offline tests for the llms.txt scorer."""

from seohead.checks import llms_txt

GOOD = """# Acme Analytics Platform

> Acme helps teams understand user behavior through real-time dashboards,
> audience segments, and API reports.

## Product
- [Acme Analytics](https://example.com/product): the core analytics platform
- [Pricing](https://example.com/pricing): plans for teams of every size

## Evidence
- [Customer cases](https://example.com/cases): how Acme accelerates growth

## Documentation
- [Docs](https://example.com/docs): integration guide
- [API](https://example.com/api): API reference
"""


def test_good_llms_txt_scores_high():
    r = llms_txt.score_llms_txt(GOOD, brand="Acme")
    assert r["ok"] is True
    assert r["score"] >= 7.0
    assert r["stats"]["sections"] >= 3
    assert r["stats"]["links"] >= 3
    assert r["stats"]["mentions_brand"] is True


def test_empty_content_returns_error():
    r = llms_txt.score_llms_txt("")
    assert r["ok"] is False


def test_missing_sections_lower_score():
    bare = "# Just a title\n\nOne link [home](https://example.org).\n"
    r = llms_txt.score_llms_txt(bare, brand="X")
    assert r["score"] < 5.0
    assert r["stats"]["sections"] == 0


def test_brand_mention_check():
    r = llms_txt.score_llms_txt(GOOD, brand="Acme")
    brand_check = r["checks"][3]
    assert "Acme" in brand_check["name"]
    assert brand_check["passed"] is True


def test_oversized_content_fails_size_check():
    big = "# T\n\n" + ("x" * (61 * 1024))
    r = llms_txt.score_llms_txt(big)
    assert r["stats"]["size_ok"] is False
    size_check = r["checks"][-1]
    assert size_check["passed"] is False


def test_grade_thresholds():
    assert llms_txt.score_llms_txt(GOOD, brand="Acme")["grade"] in ("A", "B")
    assert llms_txt.score_llms_txt("# x\n")["grade"] in ("D", "F")


# ── A missing file is a measured result, not a tool failure ──────────────────


def test_missing_file_is_a_measured_result_not_a_tool_failure(monkeypatch):
    """A 404 means the file is absent; it must remain distinct from network failure.

    The repository invariant reserves ``ok=False`` for results that were not measured.
    """
    import seohead.recon.net as net
    from seohead.checks import llms_txt

    class _Resp:
        status_code = 404
        text = ""

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp()

    monkeypatch.setattr(net, "http_client", lambda timeout, **kw: (_Client(), False))
    r = llms_txt.check_llms_txt("https://example.com/")
    assert r["ok"] is True, "The tool completed successfully; the file is simply absent."
    assert r["exists"] is False
    assert r["score"] == 0
    assert any("llms.txt" in finding for finding in r["findings"])


def _mock_status(monkeypatch, status_code: int, text: str = ""):
    import seohead.recon.net as net

    class _Resp:
        pass

    _Resp.status_code = status_code
    _Resp.text = text

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _Resp()

    monkeypatch.setattr(net, "http_client", lambda timeout, **kw: (_Client(), False))


def test_401_is_an_unmeasured_error_not_a_missing_file(monkeypatch):
    """An access gate never revealed whether the file exists -- it must not be
    reported as a confident absence finding (issue #231)."""
    _mock_status(monkeypatch, 401)
    r = llms_txt.check_llms_txt("https://example.com/")
    assert r["ok"] is False
    assert r["status_code"] == 401
    assert "exists" not in r


def test_403_is_an_unmeasured_error_not_a_missing_file(monkeypatch):
    _mock_status(monkeypatch, 403)
    r = llms_txt.check_llms_txt("https://example.com/")
    assert r["ok"] is False
    assert r["status_code"] == 403
    assert "exists" not in r


def test_429_is_an_unmeasured_error_not_a_missing_file(monkeypatch):
    """A rate limit withholds the body the same way a network error does."""
    _mock_status(monkeypatch, 429)
    r = llms_txt.check_llms_txt("https://example.com/")
    assert r["ok"] is False
    assert r["status_code"] == 429
    assert "exists" not in r


def test_500_is_an_unmeasured_error_not_a_missing_file(monkeypatch):
    _mock_status(monkeypatch, 500, text="upstream failure")
    r = llms_txt.check_llms_txt("https://example.com/")
    assert r["ok"] is False
    assert r["status_code"] == 500
    assert "exists" not in r


def test_h1_check_is_named_as_mere_presence_without_a_brand():
    """With no expected project name to compare against, the check must not
    claim to verify one (issue #231)."""
    content = "# An unrelated heading\n\n## A\n## B\n## C\n[a](/a) [b](/b) [c](/c)\n"
    r = llms_txt.score_llms_txt(content)
    h1_check = r["checks"][0]
    assert "project name" not in h1_check["name"].lower()
    assert h1_check["passed"] is True


def test_h1_check_requires_the_brand_when_one_is_supplied():
    """A heading unrelated to the supplied brand must fail check one, even
    though check four (brand mention anywhere) might still find the name
    elsewhere in the document (issue #231)."""
    content = (
        "# An unrelated heading\n\nAcme is a product platform.\n\n"
        "## Product\n[Pricing](/pricing)\n## Evidence\n[Customer cases](/cases)\n"
        "## Documentation\n[Docs](/docs)\n"
    )
    r = llms_txt.score_llms_txt(content, brand="Acme")
    h1_check = r["checks"][0]
    assert "Acme" in h1_check["name"]
    assert h1_check["passed"] is False


def test_h1_check_passes_when_the_brand_is_in_the_heading():
    r = llms_txt.score_llms_txt(GOOD, brand="Acme")
    assert r["checks"][0]["passed"] is True


def test_network_failure_still_reports_not_measured(monkeypatch):
    """A network failure stays unmeasured instead of scoring an unreachable site zero."""
    import seohead.recon.net as net
    from seohead.checks import llms_txt

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            raise OSError("connection refused")

    monkeypatch.setattr(net, "http_client", lambda timeout, **kw: (_Client(), False))
    r = llms_txt.check_llms_txt("https://example.com/")
    assert r["ok"] is False and "error" in r
    assert "exists" not in r
