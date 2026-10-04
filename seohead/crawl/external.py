"""Bounded validation of external link destinations recorded during a crawl.

The crawler records outbound edges (``discovery.external.store``) but
deliberately never fetches them: chasing every outlink becomes a crawl of
everyone else's site. ``discovery.external.crawl`` is the opt-in middle
ground. Once the internal frontier closes, each distinct recorded destination
is checked once — status, content type and redirect behaviour — inside its
own target, host, request, depth and redirect budgets, all separate from the
internal frontier's. It never becomes unrestricted traversal: the only
discovery allowed is ``external_checks.max_depth`` hops of *same-host* links
on a page that actually answered, and every hop still counts against the same
budgets.

External hosts' robots.txt is never fetched — a crawler that asks somebody
else's server about its own rules is a crawler that wanders. The check reports
what the destination answered to bounded requests made under the same pinned
network policy, private-target guard and host-bound credentials as the
internal crawl. Every recorded destination ends with a written outcome —
fetched, failed, blocked, or skipped with the budget or rule that declined it —
so external-check coverage is explicit evidence, never implied by absence.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit, urlunsplit

from seohead.crawl.collect import MAX_REDIRECT_CHAIN_HOPS, MAX_RESPONSE_BYTES, fetch_one
from seohead.crawl.throttle import DispatchGate, RequestBudgetExhausted, Throttle

# How many distinct linking pages one check keeps as provenance. The full count
# still lands in ``source_count``; the sample exists so a destination linked
# from thousands of pages does not carry a thousand-entry list.
_SOURCE_SAMPLE = 8


def target_key(url: str) -> str:
    """The identity a destination is deduplicated by: fragment dropped (it
    never selects a distinct server resource), path normalized to "/", query
    kept — the same identity the internal frontier deduplicates by."""
    parts = urlsplit(url.strip())
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path or "/", parts.query, "")
    )


def _drop_fragment(url: str) -> str:
    parts = urlsplit(url.strip())
    return urlunsplit((parts.scheme, parts.netloc, parts.path, parts.query, ""))


@dataclass
class ExternalCheck:
    """The recorded outcome for one external destination.

    ``outcome`` is one of:

    - ``"fetched"`` — the destination answered at least once. When its final
      response was a redirect the check could not (or was not asked to)
      resolve, ``reason`` names what stopped the chain and ``final_url``
      stays empty — the 3xx itself is the recorded answer, not a failure.
    - ``"failed"`` — the last attempt produced no response (timeout, refused
      connection, unresolved host); ``error``/``error_kind`` carry the detail.
    - ``"blocked"`` — a safety guard refused the target before it answered:
      a private or non-public destination, an invalid or non-HTTP(S) URL, or
      a redirect chain whose final hop refused on the same grounds.
    - ``"skipped"`` — a configured bound or rule declined it before any
      request: ``reason`` names which one.
    """

    url: str
    # 0 = an outlink recorded on a crawled page; >0 = a same-host link found
    # on an external page the check itself fetched (external_checks.max_depth).
    depth: int = 0
    sources: list[str] = field(default_factory=list)
    source_count: int = 0
    outcome: str = ""
    reason: str = ""
    status_code: int | None = None
    content_type: str = ""
    # Location header of the first response when it was a redirect — the
    # destination this check was asked about pointing onward.
    redirect_url: str = ""
    # Hops followed after the first response, each {url, status_code,
    # redirect_url, error} — the same evidence shape list mode's
    # resolve_redirect_destination chain uses.
    redirect_chain: list[dict[str, Any]] = field(default_factory=list)
    # The URL that gave the last terminal (non-3xx) answer; "" means the chain
    # stopped unresolved and ``reason`` says on which budget or guard.
    final_url: str = ""
    error: str = ""
    error_kind: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> ExternalCheck:
        record = cls(url=str(payload.get("url") or ""))
        for name in (
            "depth",
            "sources",
            "source_count",
            "outcome",
            "reason",
            "status_code",
            "content_type",
            "redirect_url",
            "redirect_chain",
            "final_url",
            "error",
            "error_kind",
        ):
            if name in payload:
                setattr(record, name, payload[name])
        return record


@dataclass(frozen=True)
class ExternalPolicy:
    """The bounds a check runs under — every one separate from the internal
    frontier's budgets of the same name."""

    # Distinct external URLs the phase may fetch at most: recorded
    # destinations plus same-host continuations admitted under max_depth.
    max_targets: int = 100
    # Distinct external origins the phase may contact at most, redirect hops
    # included: a redirect is only ever a request to another host, and the
    # host budget is a budget of contacted origins, not of link labels.
    max_hosts: int = 20
    # HTTP attempts the phase may spend; 0 = no phase-specific cap (the
    # internal crawl's limits.max_requests is a separate budget).
    max_requests: int = 0
    # 0 = check recorded destinations only; >0 also follows that many hops of
    # same-host links on external pages that answered — never off-host, so
    # never unrestricted traversal.
    max_depth: int = 0
    # Redirect hops followed per destination; capped at MAX_REDIRECT_CHAIN_HOPS.
    max_redirects: int = 5

    @classmethod
    def from_config(cls, config: dict[str, Any] | None) -> ExternalPolicy:
        config = config or {}
        return cls(
            max_targets=int(config.get("max_targets", 100)),
            max_hosts=int(config.get("max_hosts", 20)),
            max_requests=int(config.get("max_requests", 0)),
            max_depth=int(config.get("max_depth", 0)),
            max_redirects=min(int(config.get("max_redirects", 5)), MAX_REDIRECT_CHAIN_HOPS),
        )

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _refusal(error: str) -> tuple[str, str]:
    """Map a ``validate_url`` refusal message to (outcome, reason)."""
    lowered = error.lower()
    if "private" in lowered or "non-public" in lowered or "localhost" in lowered:
        return "blocked", "private_target"
    if "could not be resolved" in lowered:
        return "failed", "dns_unresolved"
    return "blocked", "invalid_target"


def _shape(url: str) -> bool:
    """Fetchable shape: http(s) and a hostname — cheap enough to check for
    every candidate before any budget is spent."""
    parts = urlsplit(url)
    return parts.scheme.lower() in {"http", "https"} and bool(parts.hostname)


def _host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def _prior_spend(records: Iterable[ExternalCheck]) -> tuple[int, set[str]]:
    """Reconstruct what a persisted record set already spent, so a resumed
    check does not reopen its request and host budgets: each fetched or
    failed record is one attempt plus one per followed redirect hop, and every
    hop's host was contacted. A timeout retried inside ``fetch_one`` is
    undercounted by one request here — the budget can reopen by at most that
    margin, never by the whole chain."""
    requests = 0
    hosts: set[str] = set()
    for record in records:
        if record.status_code is not None or record.error_kind:
            requests += 1
            hosts.add(_host(record.url))
        for hop in record.redirect_chain:
            requests += 1
            hosts.add(_host(str(hop.get("url") or "")))
    return requests, hosts


def run_external_checks(
    edges: Iterable[Any],
    *,
    policy: ExternalPolicy,
    is_internal: Callable[[str], bool],
    emit: Callable[[ExternalCheck], None],
    excluded_host: Callable[[str], bool] | None = None,
    done: Iterable[ExternalCheck] | None = None,
    client: Any = None,
    fetcher: Callable[[str], Any] | None = None,
    headers_for_url: Callable[[str], dict[str, str]] | None = None,
    user_agent: str = "",
    max_response_bytes: int = MAX_RESPONSE_BYTES,
    retry_on_timeout: int = 0,
    parse_options: dict[str, Any] | None = None,
    dispatch_gate: DispatchGate | None = None,
    time_exhausted: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Check every distinct recorded external destination once, in discovery
    order, inside ``policy``. Returns the coverage summary the crawl result
    persists — the explicit record of what this phase did and did not reach.

    ``emit`` is invoked once per decided destination (fetched, failed,
    blocked or skipped) in decision order; the caller owns appending it to the
    result and writing the durable record. ``done`` carries the records a
    previous invocation already persisted: their targets are never
    re-requested or re-recorded, and the requests and hosts they already
    spent count against this invocation's budgets — a resume must not reopen
    them. When a ``dispatch_gate`` is supplied its request count is set to
    the reconstructed prior spend, not added to.
    """
    done_records = list(done or ())
    done_keys = {target_key(record.url) for record in done_records}
    prior_requests, prior_hosts = _prior_spend(done_records)
    gate = dispatch_gate or DispatchGate(
        Throttle(min_delay=0, max_delay=0, max_concurrency=1, adaptive=False),
        lambda _s: None,
        max_requests=policy.max_requests,
    )
    if prior_requests:
        # A resume under a lowered request budget must not crash on the
        # restored spend: clamp to the gate's own cap — the next dispatch then
        # refuses and the phase ends request_budget, which is the honest
        # outcome for "the budget was already spent".
        restored = min(prior_requests, gate.max_requests) if gate.max_requests else prior_requests
        gate.restore_requests_used(restored)
    headers_for_url = headers_for_url or (lambda _url: {})
    time_exhausted = time_exhausted or (lambda: False)
    excluded_host = excluded_host or (lambda _url: False)

    # Distinct recorded destinations in first-seen order, provenance kept.
    slots: dict[str, dict[str, Any]] = {}
    for edge in edges:
        dest = (getattr(edge, "destination", "") or "").strip()
        if not dest or is_internal(dest):
            continue
        key = target_key(dest)
        slot = slots.get(key)
        if slot is None:
            slot = slots[key] = {
                "url": _drop_fragment(dest),
                "sources": [],
                "source_count": 0,
            }
        slot["source_count"] += 1
        source = getattr(edge, "source", "") or ""
        if source and source not in slot["sources"] and len(slot["sources"]) < _SOURCE_SAMPLE:
            slot["sources"].append(source)

    counts = {"fetched": 0, "failed": 0, "blocked": 0, "skipped": 0}
    skipped_reasons: dict[str, int] = {}
    # Two different ledgers on purpose. ``planned_hosts`` enforces the host
    # budget at admission time, so a cap on distinct origins is honoured even
    # for destinations queued behind a request budget that never lets them
    # dispatch. ``contacted_hosts`` only gains a host once an HTTP attempt
    # actually left the gate — reporting the planned set as contacted would
    # claim coverage a budget stop never produced.
    planned_hosts: set[str] = set(prior_hosts)
    contacted_hosts: set[str] = set(prior_hosts)
    # ``done`` keys are part of the seen set too: a destination a previous
    # invocation already recorded — including a depth>0 discovery that never
    # was an edge — must not be re-admitted by same-host continuation.
    seen_keys = set(slots) | done_keys
    resumed = 0
    finish_reason = "finished"

    def close(check: ExternalCheck) -> None:
        counts[check.outcome] += 1
        if check.outcome == "skipped":
            skipped_reasons[check.reason] = skipped_reasons.get(check.reason, 0) + 1
        emit(check)

    queue: deque[dict[str, Any]] = deque()
    admitted = 0
    for key, slot in slots.items():
        url = slot["url"]
        if key in done_keys:
            resumed += 1
            continue
        if not _shape(url):
            close(
                ExternalCheck(
                    url=url,
                    sources=list(slot["sources"]),
                    source_count=slot["source_count"],
                    outcome="blocked",
                    reason="invalid_target",
                )
            )
            continue
        if excluded_host(url):
            close(
                ExternalCheck(
                    url=url,
                    sources=list(slot["sources"]),
                    source_count=slot["source_count"],
                    outcome="skipped",
                    reason="excluded_host",
                )
            )
            continue
        host = _host(url)
        if host not in planned_hosts and len(planned_hosts) >= policy.max_hosts:
            close(
                ExternalCheck(
                    url=url,
                    sources=list(slot["sources"]),
                    source_count=slot["source_count"],
                    outcome="skipped",
                    reason="host_budget",
                )
            )
            continue
        if admitted >= policy.max_targets:
            close(
                ExternalCheck(
                    url=url,
                    sources=list(slot["sources"]),
                    source_count=slot["source_count"],
                    outcome="skipped",
                    reason="target_budget",
                )
            )
            continue
        admitted += 1
        planned_hosts.add(host)
        queue.append(
            {
                "url": url,
                "depth": 0,
                "sources": slot["sources"],
                "source_count": slot["source_count"],
            }
        )

    def fetch(url: str) -> tuple[Any, dict[str, Any] | None]:
        record, parsed = fetch_one(
            url,
            client=client,
            fetcher=fetcher,
            throttle=gate.throttle,
            extra_headers=headers_for_url(url),
            user_agent=user_agent,
            max_response_bytes=max_response_bytes,
            retry_on_timeout=retry_on_timeout,
            parse_options=parse_options,
            wait=gate.wait_turn,
        )
        # Only a dispatched attempt counts as contact: a RequestBudgetExhausted
        # raised inside the gate above never reaches this line, so a host the
        # budget refused is never reported as contacted.
        contacted_hosts.add(_host(url))
        return record, parsed

    while queue and finish_reason == "finished":
        item = queue.popleft()
        check = ExternalCheck(
            url=item["url"],
            depth=item["depth"],
            sources=list(item["sources"]),
            source_count=item["source_count"],
        )
        if time_exhausted():
            queue.appendleft(item)
            finish_reason = "duration_limit"
            break
        try:
            record, parsed = fetch(check.url)
        except RequestBudgetExhausted:
            queue.appendleft(item)
            finish_reason = "request_budget"
            break
        check.status_code = record.status_code
        check.content_type = record.content_type
        check.redirect_url = record.redirect_url
        check.error = record.error
        check.error_kind = record.error_kind

        # The first attempt produced no response at all: a transport failure
        # (error_kind) or a guard refusal before the request (error only).
        if record.status_code is None and record.error:
            if record.error_kind:
                check.outcome, check.reason = "failed", "request_failed"
            else:
                check.outcome, check.reason = _refusal(record.error)
            close(check)
            continue

        # Bounded redirect chain: each hop is another request, validated like
        # the seed (fetch_one calls validate_url on the live path), paced by
        # the same gate, and counted against host and request budgets.
        current, current_url = record, check.url
        chain_seen = {target_key(check.url)}
        while (
            current.status_code is not None
            and 300 <= current.status_code < 400
            and current.redirect_url
            and len(check.redirect_chain) < policy.max_redirects
        ):
            next_url = _drop_fragment(urljoin(current_url, current.redirect_url))
            if not _shape(next_url):
                check.reason = "invalid_target"
                break
            if target_key(next_url) in chain_seen:
                check.reason = "redirect_loop"
                break
            next_host = _host(next_url)
            if excluded_host(next_url):
                # The operator's never-fetch list applies to onward hops too —
                # a redirect is "what links to it" in the strictest sense.
                check.reason = "excluded_host"
                break
            if next_host not in planned_hosts and len(planned_hosts) >= policy.max_hosts:
                check.reason = "host_budget"
                break
            planned_hosts.add(next_host)
            if time_exhausted():
                check.reason = "duration_limit"
                finish_reason = "duration_limit"
                break
            try:
                hop, hop_parsed = fetch(next_url)
            except RequestBudgetExhausted:
                check.reason = "request_budget"
                finish_reason = "request_budget"
                break
            check.redirect_chain.append(
                {
                    "url": next_url,
                    "status_code": hop.status_code,
                    "redirect_url": hop.redirect_url,
                    "error": hop.error,
                }
            )
            current, current_url, parsed = hop, next_url, hop_parsed
            chain_seen.add(target_key(next_url))
            if hop.status_code is None:
                break
        else:
            # The while ended because the last response was not a redirect or
            # carried no Location — the chain resolved — or the redirect
            # budget ran out. The loop condition already distinguishes them.
            if (
                current.status_code is not None
                and 300 <= current.status_code < 400
                and current.redirect_url
            ):
                check.reason = "redirect_budget"

        if current.status_code is not None:
            check.outcome = "fetched"
            check.status_code = current.status_code
            check.content_type = current.content_type
            if not (300 <= current.status_code < 400 and current.redirect_url):
                check.final_url = current_url
        elif current.error_kind:
            # The last hop died in transport: the destination's final answer
            # is unknown even though earlier hops answered.
            check.outcome, check.reason = "failed", "request_failed"
            check.error, check.error_kind = current.error, current.error_kind
        else:
            # The last hop was refused by the guard (private redirect target,
            # invalid URL, unresolved host) — contact happened, but the chain
            # ended on a refusal rather than an answer.
            check.outcome, check.reason = _refusal(current.error)
            check.error = current.error
        close(check)

        # Same-host continuation only: a fetched HTML page may offer its own
        # links up to max_depth hops deep. Off-host links on an external page
        # are deliberately not followed — that is the unrestricted traversal
        # this phase exists to avoid.
        if check.outcome == "fetched" and item["depth"] < policy.max_depth and parsed:
            for link in parsed.get("links") or []:
                if link.get("external"):
                    continue
                href = (link.get("href") or "").strip()
                if not href:
                    continue
                next_url = _drop_fragment(href)
                if not _shape(next_url):
                    continue
                key = target_key(next_url)
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                if admitted >= policy.max_targets:
                    skipped_reasons["target_budget"] = skipped_reasons.get("target_budget", 0) + 1
                    counts["skipped"] += 1
                    continue
                admitted += 1
                queue.append(
                    {
                        "url": next_url,
                        "depth": item["depth"] + 1,
                        "sources": [check.final_url or check.url],
                        "source_count": 1,
                    }
                )

    if finish_reason != "finished":
        # Whatever never got attempted still gets a written decision — the
        # coverage this phase promised is per destination, not per success.
        while queue:
            item = queue.popleft()
            close(
                ExternalCheck(
                    url=item["url"],
                    depth=item["depth"],
                    sources=list(item["sources"]),
                    source_count=item["source_count"],
                    outcome="skipped",
                    reason=finish_reason,
                )
            )

    return {
        "enabled": True,
        "ran": True,
        "policy": policy.as_dict(),
        # External hosts' robots.txt is never fetched: the check reports what
        # a destination answered to a bounded request, not what its own
        # ruleset would have told us.
        "robots_policy": "not_evaluated",
        "destinations": len(slots),
        # Decisions restored from a previous invocation's sidecar: part of this
        # logical run's coverage, decided before it started.
        "records_carried": len(done_records),
        "resumed": resumed,
        "fetched": counts["fetched"],
        "failed": counts["failed"],
        "blocked": counts["blocked"],
        "skipped": counts["skipped"],
        "skipped_reasons": skipped_reasons,
        "hosts_contacted": len(contacted_hosts),
        "requests": gate.requests_used,
        "finish_reason": finish_reason,
    }
