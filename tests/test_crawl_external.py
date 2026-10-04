"""Bounded external-destination checks after the internal frontier (#746).

Every test is synthetic and offline: destinations live in in-memory response
maps or dummy clients, never on a socket.
"""

import json

import pytest

from seohead.crawl import settings as crawl_config
from seohead.crawl.external import ExternalCheck, ExternalPolicy, run_external_checks
from seohead.crawl.spider import LinkEdge, crawl_site

ROBOTS_OK = "User-agent: *\nDisallow:\n"


class FakeResponse:
    def __init__(self, text="", status_code=200, headers=None):
        self.text = text
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/html; charset=utf-8"}


def page(*links: str, title: str = "t") -> FakeResponse:
    body = "".join(f'<a href="{href}">{href}</a>' for href in links)
    return FakeResponse(
        f"<html><head><title>{title}</title></head><body><h1>{title}</h1>{body}</body></html>"
    )


def redirect(location: str, status_code: int = 301) -> FakeResponse:
    return FakeResponse("", status_code=status_code, headers={"location": location})


def _fetcher(mapping, calls=None):
    def fetch(url):
        if calls is not None:
            calls.append(url)
        value = mapping.get(url)
        if value is None:
            return FakeResponse("", status_code=404)
        if isinstance(value, BaseException):
            raise value
        return value

    return fetch


SITE = {
    "https://example.com/robots.txt": FakeResponse(
        ROBOTS_OK, headers={"content-type": "text/plain"}
    ),
    # The same external destination linked twice on the start page, plus a
    # second destination linked from /a — three edges, two targets.
    "https://example.com/": page("/a", "https://other.example/x", "https://other.example/x"),
    "https://example.com/a": page("https://cdn.example/y"),
    "https://other.example/x": page(title="other"),
    "https://cdn.example/y": page(title="cdn"),
}


def _crawl(mapping=SITE, calls=None, **kw):
    kw.setdefault("sleeper", lambda _s: None)
    kw.setdefault("min_delay", 0)
    return crawl_site("https://example.com/", fetcher=_fetcher(mapping, calls), **kw)


def _external(calls=None, **kw):
    kw.setdefault("crawl_external_links", True)
    return _crawl(calls=calls, **kw)


def test_external_check_is_off_by_default():
    calls: list[str] = []
    result = _crawl(calls=calls)
    assert result.external_checks == []
    assert result.external_summary == {}
    # The edges are still recorded — only the checking is opt-in.
    assert "https://other.example/x" in {e.destination for e in result.links}
    assert "https://other.example/x" not in calls
    assert "https://cdn.example/y" not in calls


def test_each_distinct_destination_checked_once_with_source_provenance():
    result = _external()
    by_url = {check.url: check for check in result.external_checks}
    assert set(by_url) == {"https://other.example/x", "https://cdn.example/y"}
    linked_twice = by_url["https://other.example/x"]
    assert linked_twice.outcome == "fetched"
    assert linked_twice.status_code == 200
    assert linked_twice.sources == ["https://example.com/"]
    assert linked_twice.source_count == 2
    assert by_url["https://cdn.example/y"].sources == ["https://example.com/a"]


def test_summary_distinguishes_internal_and_external_coverage():
    result = _external()
    summary = result.external_summary
    assert summary["enabled"] is True and summary["ran"] is True
    assert summary["finish_reason"] == "finished"
    # The internal frontier's own verdict travels inside the external
    # summary — two measurements, never one conflated number.
    assert summary["internal_finish_reason"] == "finished"
    assert result.finish_reason == "finished"
    assert summary["destinations"] == 2
    assert summary["fetched"] == 2
    assert summary["robots_policy"] == "not_evaluated"
    assert summary["policy"]["max_targets"] == 100


def test_cross_origin_redirect_chain_is_recorded_and_bounded():
    calls: list[str] = []
    site = dict(SITE)
    site["https://other.example/x"] = redirect("https://cdn.example/y")
    result = _external(mapping=site, calls=calls, external_policy={"max_redirects": 5})
    [check] = [c for c in result.external_checks if c.url == "https://other.example/x"]
    assert check.outcome == "fetched"
    assert check.redirect_url == "https://cdn.example/y"
    assert check.redirect_chain == [
        {
            "url": "https://cdn.example/y",
            "status_code": 200,
            "redirect_url": "",
            "error": "",
        }
    ]
    assert check.status_code == 200
    assert check.final_url == "https://cdn.example/y"
    assert result.external_summary["hosts_contacted"] == 2


def test_redirect_budget_marks_an_unresolved_chain():
    site = dict(SITE)
    site["https://other.example/x"] = redirect("https://other.example/r1")
    site["https://other.example/r1"] = redirect("https://other.example/r2")
    site["https://other.example/r2"] = page(title="landed")
    result = _external(mapping=site, external_policy={"max_redirects": 1})
    [check] = [c for c in result.external_checks if c.url == "https://other.example/x"]
    assert check.outcome == "fetched"
    assert check.reason == "redirect_budget"
    assert len(check.redirect_chain) == 1
    assert check.status_code == 301
    # The chain stopped unresolved: no final answer is recorded as one.
    assert check.final_url == ""


def test_redirect_loop_is_recorded_not_crashed():
    site = dict(SITE)
    site["https://other.example/x"] = redirect("https://other.example/x")
    result = _external(mapping=site, external_policy={"max_redirects": 3})
    [check] = [c for c in result.external_checks if c.url == "https://other.example/x"]
    assert check.outcome == "fetched"
    assert check.reason == "redirect_loop"


def test_request_budget_stops_the_phase_and_marks_the_rest():
    result = _external(external_policy={"max_requests": 1})
    outcomes = {c.url: c.outcome for c in result.external_checks}
    assert outcomes["https://other.example/x"] == "fetched"
    assert outcomes["https://cdn.example/y"] == "skipped"
    assert result.external_summary["finish_reason"] == "request_budget"
    assert result.external_summary["requests"] == 1
    assert result.external_summary["skipped_reasons"] == {"request_budget": 1}
    # An external budget stop is partial coverage, not an internal failure.
    assert result.partial is True
    assert result.finish_reason == "finished"


def test_host_budget_skips_origins_beyond_the_cap():
    result = _external(external_policy={"max_hosts": 1})
    by_url = {c.url: c for c in result.external_checks}
    assert by_url["https://other.example/x"].outcome == "fetched"
    assert by_url["https://cdn.example/y"].outcome == "skipped"
    assert by_url["https://cdn.example/y"].reason == "host_budget"
    assert result.external_summary["hosts_contacted"] == 1


def test_target_budget_skips_destinations_beyond_the_cap():
    result = _external(external_policy={"max_targets": 1})
    outcomes = {c.url: (c.outcome, c.reason) for c in result.external_checks}
    assert outcomes["https://other.example/x"] == ("fetched", "")
    assert outcomes["https://cdn.example/y"] == ("skipped", "target_budget")


def test_max_depth_follows_same_host_links_only():
    site = dict(SITE)
    site["https://other.example/x"] = page("https://other.example/deep", "https://third.example/z")
    site["https://other.example/deep"] = page(title="deep")
    calls: list[str] = []
    result = _external(mapping=site, calls=calls, external_policy={"max_depth": 1})
    by_url = {c.url: c for c in result.external_checks}
    assert by_url["https://other.example/deep"].outcome == "fetched"
    assert by_url["https://other.example/deep"].depth == 1
    assert by_url["https://other.example/deep"].sources == ["https://other.example/x"]
    # An off-host link on an external page is never followed — that is the
    # unrestricted traversal the phase exists to avoid.
    assert "https://third.example/z" not in by_url
    assert "https://third.example/z" not in calls
    # And with the default depth the continuation itself stays unchecked.
    shallow = _external()
    assert "https://other.example/deep" not in {c.url for c in shallow.external_checks}


def test_failed_fetch_records_a_failed_outcome():
    site = dict(SITE)
    site["https://cdn.example/y"] = TimeoutError("slow origin")
    result = _external(mapping=site)
    [check] = [c for c in result.external_checks if c.url == "https://cdn.example/y"]
    assert check.outcome == "failed"
    assert check.reason == "request_failed"
    assert check.error_kind == "timeout"


def test_excluded_host_seed_is_skipped_by_policy():
    result = _external(scope={"exclude_hosts": ["cdn.example"]})
    [check] = [c for c in result.external_checks if c.url == "https://cdn.example/y"]
    assert check.outcome == "skipped"
    assert check.reason == "excluded_host"


def test_redirect_to_an_excluded_host_stops_the_chain():
    site = dict(SITE)
    site["https://other.example/x"] = redirect("https://cdn.example/y")
    calls: list[str] = []
    result = _external(
        mapping=site,
        calls=calls,
        scope={"exclude_hosts": ["cdn.example"]},
    )
    [check] = [c for c in result.external_checks if c.url == "https://other.example/x"]
    # The seed answered; the onward hop was refused by the never-fetch list,
    # so the chain stays unresolved rather than fetching the excluded host.
    assert check.outcome == "fetched"
    assert check.reason == "excluded_host"
    assert check.status_code == 301
    assert "https://cdn.example/y" not in calls
    [cdn] = [c for c in result.external_checks if c.url == "https://cdn.example/y"]
    assert cdn.outcome == "skipped"
    assert cdn.reason == "excluded_host"


def test_outcomes_persist_to_the_sidecar(tmp_path):
    out = tmp_path / "checks.jsonl"
    site = dict(SITE)
    site["https://cdn.example/y"] = TimeoutError("slow origin")
    result = _external(mapping=site, external_path=str(out))
    lines = [json.loads(line) for line in out.read_text().splitlines() if line.strip()]
    assert len(lines) == len(result.external_checks)
    by_url = {line["url"]: line for line in lines}
    assert by_url["https://other.example/x"]["outcome"] == "fetched"
    assert by_url["https://cdn.example/y"]["outcome"] == "failed"


# ── resume ────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("spool", [False, True])
def test_resume_never_rechecks_a_decided_destination(tmp_path, spool):
    """An interrupted external phase resumes where it stopped: decided
    destinations are not re-requested and their budget spend is not
    reopened."""
    state = tmp_path / "crawl_state.json"
    links = tmp_path / "links.jsonl"
    pages = tmp_path / "pages.jsonl"
    checks_file = tmp_path / "external_checks.jsonl"

    crashing = dict(SITE)
    crashing["https://cdn.example/y"] = KeyboardInterrupt()
    calls1: list[str] = []
    first = _external(
        mapping=crashing,
        calls=calls1,
        state_path=str(state),
        links_path=str(links),
        out_path=str(pages),
        external_path=str(checks_file),
        forms_path=str(tmp_path / "forms.jsonl"),
        spool_evidence=spool,
    )
    assert first.external_summary["finish_reason"] == "interrupted"
    assert [c.url for c in first.external_checks] == ["https://other.example/x"]

    calls2: list[str] = []
    second = _external(
        calls=calls2,
        state_path=str(state),
        links_path=str(links),
        out_path=str(pages),
        external_path=str(checks_file),
        forms_path=str(tmp_path / "forms.jsonl"),
        spool_evidence=spool,
    )
    assert second.resumed is True
    # The decided destination is restored, never re-requested.
    assert "https://other.example/x" not in calls2
    # The interrupted one is checked now — exactly once.
    assert calls2.count("https://cdn.example/y") == 1
    assert second.external_summary["finish_reason"] == "finished"
    assert second.external_summary["resumed"] == 1
    assert second.external_summary["records_carried"] == 1
    outcomes = {c.url: c.outcome for c in second.external_checks}
    assert outcomes == {
        "https://other.example/x": "fetched",
        "https://cdn.example/y": "fetched",
    }
    # The sidecar holds one line per decided destination — not re-emitted.
    lines = [line for line in checks_file.read_text().splitlines() if line.strip()]
    assert len(lines) == 2
    # Both phases complete now, so nothing is left to resume into.
    assert not state.exists()


def test_an_exhausted_budget_stays_exhausted_across_resume(tmp_path):
    state = tmp_path / "crawl_state.json"
    links = tmp_path / "links.jsonl"
    pages = tmp_path / "pages.jsonl"
    checks_file = tmp_path / "external_checks.jsonl"
    policy = {"max_requests": 1}

    calls1: list[str] = []
    first = _external(
        calls=calls1,
        external_policy=policy,
        state_path=str(state),
        links_path=str(links),
        out_path=str(pages),
        external_path=str(checks_file),
    )
    assert first.external_summary["finish_reason"] == "request_budget"
    assert state.exists()

    calls2: list[str] = []
    second = _external(
        calls=calls2,
        external_policy=policy,
        state_path=str(state),
        links_path=str(links),
        out_path=str(pages),
        external_path=str(checks_file),
    )
    # The budget the first run spent was reconstructed, not reopened: the
    # still-undecided destination keeps its recorded "skipped" decision
    # instead of silently becoming a fresh request.
    assert calls2.count("https://cdn.example/y") == 0
    assert second.external_summary["requests"] == 1


def test_resume_under_a_lowered_request_budget_still_closes(tmp_path):
    """Spend reconstructed from the sidecar can exceed a budget the operator
    lowered between runs: the restore clamps to the cap instead of crashing,
    and the still-pending destination records request_budget."""
    state = tmp_path / "crawl_state.json"
    links = tmp_path / "links.jsonl"
    pages = tmp_path / "pages.jsonl"
    checks_file = tmp_path / "external_checks.jsonl"

    site = dict(SITE)
    # The first destination spends two requests — the fetch plus one redirect
    # hop — then the run is interrupted before the second one is decided.
    site["https://other.example/x"] = redirect("https://other.example/landed")
    site["https://other.example/landed"] = page(title="landed")
    site["https://cdn.example/y"] = KeyboardInterrupt()

    first = _external(
        mapping=site,
        state_path=str(state),
        links_path=str(links),
        out_path=str(pages),
        external_path=str(checks_file),
    )
    assert first.external_summary["finish_reason"] == "interrupted"
    assert [c.url for c in first.external_checks] == ["https://other.example/x"]

    calls2: list[str] = []
    second = _external(
        calls=calls2,
        external_policy={"max_requests": 1},
        state_path=str(state),
        links_path=str(links),
        out_path=str(pages),
        external_path=str(checks_file),
    )
    # Prior spend (two requests) exceeded the lowered budget: clamped, not
    # reopened — the external gate dispatched nothing this run (the internal
    # re-fetch of robots.txt on resume is a different budget).
    assert "https://cdn.example/y" not in calls2
    assert second.external_summary["requests"] == 1
    assert second.external_summary["finish_reason"] == "request_budget"
    [pending] = [c for c in second.external_checks if c.url == "https://cdn.example/y"]
    assert pending.outcome == "skipped"
    assert pending.reason == "request_budget"


# ── the pinned-network guard on the live path ─────────────────────────────────


class _Client:
    """A no-network stand-in for the real client: answers from a map and
    remembers what was asked, so "never contacted" is assertable."""

    def __init__(self, mapping):
        self.mapping = mapping
        self.sent: list[str] = []

    def get(self, url, *, headers=None, extensions=None):
        self.sent.append(url)
        return self.mapping[url]


def _edges(*destinations: str) -> list[LinkEdge]:
    return [
        LinkEdge(source="https://example.com/", destination=d, anchor="", nofollow=False)
        for d in destinations
    ]


def test_private_seed_target_is_blocked_without_a_request():
    client = _Client({})
    checks: list[ExternalCheck] = []
    # fetcher=None selects the guarded live path: validate_url runs before
    # any socket is opened, so a private literal target is refused offline.
    summary = run_external_checks(
        _edges("http://127.0.0.1/x"),
        policy=ExternalPolicy(),
        is_internal=lambda _url: False,
        emit=checks.append,
        client=client,
    )
    [check] = checks
    assert check.outcome == "blocked"
    assert check.reason == "private_target"
    assert client.sent == []
    assert summary["blocked"] == 1


def test_cross_origin_redirect_to_a_private_target_is_blocked():
    public = "http://93.184.216.34/x"  # a literal public address: no DNS needed
    client = _Client({public: redirect("http://127.0.0.1/x")})
    checks: list[ExternalCheck] = []
    summary = run_external_checks(
        _edges(public),
        policy=ExternalPolicy(max_redirects=3),
        is_internal=lambda _url: False,
        emit=checks.append,
        client=client,
    )
    [check] = checks
    assert check.outcome == "blocked"
    assert check.reason == "private_target"
    assert check.status_code == 301
    assert "private" in check.redirect_chain[0]["error"].lower()
    # Only the public hop was contacted; the private one was refused upfront.
    assert client.sent == [public]
    assert summary["blocked"] == 1


# ── settings wiring ──────────────────────────────────────────────────────────


def _load(**overrides):
    return crawl_config.load(overrides=overrides)


def test_discovery_external_crawl_defaults_off():
    assert _load()["discovery"]["external"]["crawl"] is False


def test_external_crawl_requires_stored_edges():
    # load() validates: the refusal happens while the config is being built.
    with pytest.raises(crawl_config.ConfigError, match="store"):
        _load(**{"discovery.external.store": False, "discovery.external.crawl": True})


def test_external_checks_budgets_must_be_nonnegative_integers():
    for name in (
        "max_targets",
        "max_hosts",
        "max_requests",
        "max_depth",
        "max_redirects",
    ):
        with pytest.raises(crawl_config.ConfigError, match=f"external_checks\\.{name}"):
            _load(**{f"external_checks.{name}": -1})


def test_external_checks_max_redirects_has_the_chain_ceiling():
    with pytest.raises(crawl_config.ConfigError, match=r"external_checks\.max_redirects"):
        _load(**{"external_checks.max_redirects": 11})
    assert _load(**{"external_checks.max_redirects": 10})["external_checks"]["max_redirects"] == 10


def test_external_settings_are_results_affecting():
    for path in (
        "discovery.external.crawl",
        "external_checks.max_targets",
        "external_checks.max_hosts",
        "external_checks.max_requests",
        "external_checks.max_depth",
        "external_checks.max_redirects",
    ):
        assert path in crawl_config.RESULTS_AFFECTING


def test_handler_threads_external_settings_into_the_spider(monkeypatch, tmp_path):
    import seohead.crawl.spider as spider_mod
    from seohead.crawl.spider import SpiderResult
    from seohead.servers import handlers

    captured: dict = {}

    def fake(*args, **kwargs):
        captured.update(kwargs)
        return SpiderResult()

    monkeypatch.setattr(spider_mod, "crawl_site", fake)
    config = tmp_path / "crawl.json"
    config.write_text(
        json.dumps(
            {
                "discovery": {"external": {"crawl": True}},
                "external_checks": {"max_hosts": 3, "max_requests": 7},
            }
        )
    )
    out_dir = tmp_path / "legacy"

    handlers.crawl_site(url="https://example.com/", config=str(config), out_dir=str(out_dir))

    assert captured["crawl_external_links"] is True
    assert captured["external_policy"]["max_hosts"] == 3
    assert captured["external_policy"]["max_requests"] == 7
    assert captured["external_path"] == str(out_dir / "external_checks.jsonl")


def test_scan_route_refuses_external_crawl(tmp_path):
    from seohead.servers import handlers

    config = tmp_path / "crawl.json"
    config.write_text(json.dumps({"discovery": {"external": {"crawl": True}}}))
    with pytest.raises(ValueError, match=r"discovery\.external\.crawl"):
        handlers.crawl_site(
            url="https://example.com/",
            config=str(config),
            scan_out=str(tmp_path / "scan.sqlite"),
        )


def test_list_mode_refuses_external_crawl(tmp_path):
    from seohead.servers import handlers

    # List mode keeps no link edges, so the phase has no destinations to
    # check — refusing by name beats silently dropping the option.
    config = tmp_path / "crawl.json"
    config.write_text(json.dumps({"discovery": {"external": {"crawl": True}}}))
    with pytest.raises(ValueError, match=r"discovery\.external\.crawl"):
        handlers.crawl_site(
            urls=["https://example.com/a"],
            config=str(config),
            out_dir=str(tmp_path / "legacy"),
        )
