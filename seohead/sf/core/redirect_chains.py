"""Redirect chain resolution and loop detection over a stored redirect map.

Screaming Frog's own Redirect Chains report needs the full-profile export;
without it — a light-profile export, or a native seohead crawl — the toolkit
still has everything a chain needs: every redirecting URL's own Location
header, already collected in ``AuditContext.redirect_map``. Walking that map
costs no requests, which is the point: a hop's terminal status is unknown
until every hop is fetched, so this can only run as a second pass over the
finished crawl, and it must be free to re-run when a threshold changes.
"""

from __future__ import annotations

# Real chains rarely run more than a few hops; the cap turns a malformed or
# cyclic map into a bounded walk instead of one proportional to site size.
DEFAULT_HOP_CAP = 20


def resolve_redirect_chains(
    redirect_map: dict[str, str], hop_cap: int = DEFAULT_HOP_CAP
) -> dict[str, dict[str, object]]:
    """Walk every redirecting URL in ``redirect_map`` to its terminus.

    Returns one entry per URL that itself redirects (every key of
    ``redirect_map``), keyed by that URL::

        {"kind": "single" | "chain" | "loop" | "unresolved",
         "hops": int, "final_url": str | None}

    - ``single``: an ordinary one-hop redirect — not a chain.
    - ``chain``: two or more hops to a resolved, non-redirecting terminus.
    - ``loop``: the walk revisited a URL already in its own chain — the only
      way a loop is provable, since a target is only proven cyclic once it
      reappears.
    - ``unresolved``: the walk hit ``hop_cap`` without proving either
      outcome; the caller should not report a finding it cannot support.
    """
    results: dict[str, dict[str, object]] = {}
    for start in redirect_map:
        chain = [start]
        current = start
        outcome: tuple[str, int, str | None] | None = None
        while True:
            target = redirect_map.get(current)
            if target is None:
                hops = len(chain) - 1
                outcome = ("chain" if hops >= 2 else "single", hops, current)
                break
            if target in chain:
                outcome = ("loop", len(chain), None)
                break
            if len(chain) > hop_cap:  # hop_cap hops taken and another one is needed
                break
            chain.append(target)
            current = target
        if outcome is None:
            outcome = ("unresolved", len(chain) - 1, None)
        kind, hops, final_url = outcome
        results[start] = {"kind": kind, "hops": hops, "final_url": final_url}
    return results


def redirect_hop_path(
    redirect_map: dict[str, str], start: str, hop_cap: int = DEFAULT_HOP_CAP
) -> list[str]:
    """Every URL the redirect walk from ``start`` visits, in order.

    ``[start, hop1, hop2, ...]`` for a resolved chain, ``[..., X, ..., X]`` when the
    walk closes a loop back on ``X``, and a prefix of at most ``hop_cap + 1`` URLs when
    the cap is hit. This is the evidence a fixer needs (which hop to replace), and it is
    the same bounded walk ``resolve_redirect_chains`` performs, so the two never disagree
    about where a chain ends.
    """
    path = [start]
    seen = {start}
    while True:
        target = redirect_map.get(path[-1])
        if target is None:
            break
        if target in seen:
            path.append(target)
            break
        if len(path) > hop_cap:
            break
        path.append(target)
        seen.add(target)
    return path
