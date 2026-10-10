"""Network-independent soft-404 tests for ``probe_urls`` and classification."""

from urllib.parse import urlsplit

from seohead.checks import soft404 as S
from seohead.recon import net as recon_net
from seohead.recon.net import BlockedRedirectError


def test_probe_urls_deterministic_per_origin():
    a = S.probe_urls("https://shop.example.com/page")
    b = S.probe_urls("https://shop.example.com/other")
    assert a == b, "probes are deterministic by origin, not by path"
    assert len(a) == S.PROBE_COUNT
    assert all(
        urlsplit(url).path.startswith("/seo-audit-not-found-")
        and urlsplit(url).path.count("/") == 1
        for url in a
    )


def test_probe_urls_differ_per_origin():
    a = S.probe_urls("https://shop.example.com")
    b = S.probe_urls("https://other.example.org")
    assert a != b


def test_check_soft404_uses_ordinary_root_paths_not_a_well_known_middleware_route(monkeypatch):
    """An app fallback can answer 200 while ``/.well-known/`` is correctly intercepted as 404."""

    class _Response:
        def __init__(self, url, status_code):
            self.url = url
            self.status_code = status_code

    class _StubClient:
        def get(self, url):
            return _Response(url, 404 if "/.well-known/" in url else 200)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(recon_net, "http_client", lambda timeout: (_StubClient(), True))

    result = S.check_soft404("https://shop.example.com")

    assert result["verdict"] == "warning"
    assert all("/.well-known/" not in probe["url"] for probe in result["probes"])
    assert all(probe["status"] == 200 for probe in result["probes"])


def test_check_soft404_passes_when_ordinary_nonexistent_paths_return_404(monkeypatch):
    """The route change must retain a genuine-missing-page pass verdict."""

    class _Response:
        def __init__(self, url):
            self.url = url
            self.status_code = 404

    class _StubClient:
        def get(self, url):
            return _Response(url)

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(recon_net, "http_client", lambda timeout: (_StubClient(), True))

    result = S.check_soft404("https://shop.example.com")

    assert result["verdict"] == "pass"
    assert all(probe["status"] == 404 for probe in result["probes"])


def test_classify_pass_when_both_404():
    probes = [{"status": 404}, {"status": 410}]
    assert S.classify_soft404(probes) == "pass"


def test_classify_warning_when_both_200():
    probes = [{"status": 200}, {"status": 301, "final_url": "x"}]
    assert S.classify_soft404(probes) == "warning"


def test_classify_unknown_on_mixed():
    probes = [{"status": 200}, {"status": 404}]
    assert S.classify_soft404(probes) == "unknown"


def test_classify_unknown_when_probe_errored():
    probes = [{"status": 404}, {"error": "timeout"}]
    assert S.classify_soft404(probes) == "unknown"


def test_classify_respects_access_blocked():
    probes = [{"status": 404}, {"status": 200, "access_blocked": True}]
    # With only one conclusive probe, the result must remain unknown.
    assert S.classify_soft404(probes) == "unknown"


def test_classify_refused_when_a_probe_was_blocked_by_our_own_guard():
    """#175: a guard refusal is not the same fact as "the probes disagreed"."""
    probes = [
        {"status": 404},
        {"status": 301, "final_url": "http://169.254.169.254/", "blocked_by_guard": True},
    ]
    assert S.classify_soft404(probes) == "refused"


def test_classify_refused_takes_priority_over_a_conclusive_agreement():
    probes = [
        {"status": 404},
        {"status": 404, "blocked_by_guard": True, "final_url": "http://127.0.0.1/"},
    ]
    assert S.classify_soft404(probes) == "refused"


def test_check_soft404_records_a_guard_refusal_without_requesting_the_target(monkeypatch):
    """``check_soft404`` must classify a ``BlockedRedirectError`` the same way ``fetch_one`` does.

    ``http_client`` is monkeypatched to hand back a stub whose ``get`` raises exactly what the
    real guard raises for a redirect to a private address, so this stays a unit test of
    ``check_soft404``'s own exception handling rather than a second copy of the transport-level
    proof in ``test_redirect_guard_classification.py``.
    """
    calls: list[str] = []

    class _StubClient:
        def get(self, url):
            calls.append(url)
            raise BlockedRedirectError(
                "private or non-public network target blocked",
                status_code=301,
                location="http://169.254.169.254/",
            )

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    monkeypatch.setattr(recon_net, "http_client", lambda timeout: (_StubClient(), True))

    result = S.check_soft404("https://shop.example.com")
    assert result["ok"] is True
    assert result["verdict"] == "refused"
    assert len(calls) == S.PROBE_COUNT
    for probe in result["probes"]:
        assert probe["blocked_by_guard"] is True
        assert probe["status"] == 301
        assert probe["final_url"] == "http://169.254.169.254/"


# Stored-body classifier (issue #988, S1): pure, no network.
_FULL_PRODUCT = " ".join(
    ["Centrifugal pump with full specifications, price and delivery terms"] * 10
)


def test_stored_soft404_flags_empty_200_body():
    r = S.classify_stored_soft404(status=200, text="", template_simhash=None, page_simhash=None)
    assert r == {"verdict": "warning", "reason": "thin_text"}


def test_stored_soft404_flags_thin_text_with_error_phrase():
    r = S.classify_stored_soft404(
        status=200,
        text="Страница не найдена. Вернуться на главную.",
        template_simhash=None,
        page_simhash=None,
    )
    assert r == {"verdict": "warning", "reason": "error_phrase"}


def test_stored_soft404_does_not_flag_full_product_page():
    r = S.classify_stored_soft404(
        status=200, text=_FULL_PRODUCT, template_simhash=None, page_simhash=None
    )
    assert r == {"verdict": "pass", "reason": "none"}


def test_stored_soft404_never_flags_non_200_status():
    r = S.classify_stored_soft404(
        status=404, text="page not found", template_simhash=None, page_simhash=None
    )
    assert r == {"verdict": "pass", "reason": "none"}


def test_stored_soft404_matches_error_template_by_simhash():
    from seohead.checks.duplicate import simhash

    template = simhash(_FULL_PRODUCT)
    r = S.classify_stored_soft404(
        status=200, text=_FULL_PRODUCT, template_simhash=template, page_simhash=template
    )
    assert r == {"verdict": "warning", "reason": "template_match"}


def test_stored_soft404_survives_missing_template_simhash():
    r = S.classify_stored_soft404(
        status=200, text=_FULL_PRODUCT, template_simhash=None, page_simhash=12345
    )
    assert r == {"verdict": "pass", "reason": "none"}
