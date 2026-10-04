# Scenario 7 — External links: where we point, and what answers

## The question

> We link out from every article. Some of those sites are years old now. What have we been
> sending people into?

Outbound links are the part of a site nobody owns and everybody forgets. They rot silently, and
the page that carries a dead reference is a page that reads as unmaintained.

## Covers

- **Response Codes** — External No Response · External Client Error (4XX) · External Server Error (5XX) · External Blocked Resource
- **Links** — Outlinks To Localhost

## The chain

**1. Crawl the site. External destinations are recorded and not fetched.**

```bash
seohead crawl-site --url https://example.com --config ./crawl.json --out-dir ./run
```

That is deliberate: a crawl of your site that follows every outbound link becomes a crawl of
everyone else's, at your rate, from your address. The crawl gives you the inventory —
`discovery.external.store` keeps it — and the checking is a separate, explicit step.

**2a. Opt into a bounded check at crawl time, when you mean to check them all.**

```json
{"discovery": {"external": {"store": true, "crawl": true}},
 "external_checks": {"max_targets": 100, "max_hosts": 20, "max_requests": 300, "max_redirects": 5}}
```

With `discovery.external.crawl` on, the same crawl runs a second, separate phase once the
internal frontier closes: each distinct recorded destination is requested once — status, content
type, redirect chain — inside `external_checks.*` budgets that share nothing with the internal
frontier's own limits. It is bounded, not a second-origin crawl: `max_depth` hops of *same-host*
links on a destination that answered is the only recursion it ever does, off-host links found on
an external page are never followed, and a redirect to a private or non-public target is refused
by the same guard the internal crawl runs under.

Every destination ends with a written outcome in `./run/external_checks.jsonl` — `fetched`,
`failed`, `blocked`, or `skipped` with the budget that declined it — plus the pages that linked
to it, and the run's own coverage summary lands in `discovery.external_checks` in the result
(finish reason, per-outcome counts, requests and hosts spent), deliberately distinct from the
internal crawl's `finish_reason`. An interrupted or budget-stopped phase resumes where it
stopped: decided destinations are not re-requested, and spent budgets are not reopened. The
phase exists on the legacy directory route (`--out-dir`) of a site crawl only; a `--scan-out`
run or a list-mode run with the option set refuses by name rather than silently dropping it.

**2b. Or check the destinations of one page, deliberately.**

```bash
seohead links-check --url https://example.com/page
```

Without `--internal-only`, every destination on that page is requested once and reported with
what it answered:

```json
{
  "ok": true,
  "links_found": 4,
  "checked": 4,
  "truncated": false,
  "ok_count": 4,
  "broken": [],
  "redirects": []
}
```

`truncated` matters more than it looks: a page with hundreds of destinations is checked up to a
ceiling, and a report built from a truncated check is a partial one that must say so.

**3. Compare that against the internal picture, which is a different job.**

```bash
seohead links-check --url https://example.com --internal-only
```

**4. Read the crawl's own external finding.** `BROKEN_EXTERNAL_LINK` covers both 4xx and 5xx
destinations, because from the reader's side the distinction between "gone" and "failing" is
the same broken reference. The pages that carry it are in the finding's locations.

`OUTLINK_TO_LOCALHOST` comes from the same crawl, free: any recorded destination resolving to a
loopback address (`localhost`, `127.0.0.1`, `::1`) — a dev or staging link that survived into
production markup, not something a status-code check would ever flag, since it usually just
times out or connects to whatever the reader's own machine happens to be running.

**5. Report the ones worth editing.**

```bash
seohead report-build --audit ./run/audit.json --format csv --out ./external-links.csv
```

## What comes out

A per-page list of outbound destinations and their status, and a site-level list of the broken
ones with every page that references them.

The editorial decision follows from the status, not from the tool: a 404 on a source you cited
needs a replacement citation or an archive link; a 5xx may be a bad afternoon on somebody
else's server and worth re-checking next week before touching the article.

## What it costs

One request per external destination you check — and, with `discovery.external.crawl` on, one
per destination the crawl recorded plus each redirect hop it follows, all inside the
`external_checks.*` budgets. This is the one chain here that sends requests to servers that did
not ask for them, so check a sample of pages rather than the whole archive, and do it once.

Nothing paid.

## What it cannot answer

- **Whether a 403 is a broken link.** Many sites answer 403 to anything that looks automated.
  The link works for a human. Verify before deleting a citation.
- **Whether a destination is still the right destination.** A URL that answers 200 may now be a
  parked domain or an unrelated page. Status is not meaning.
- **Blocked external resources.** A third-party script or stylesheet blocked by *its own*
  host's rules is not detected: this toolkit does not fetch another host's `robots.txt`, on
  purpose, because a crawler that goes and asks somebody else's server about its own rules is a
  crawler that wanders.
- **Link rot as a trend.** One measurement is one day. The same chain, repeated, is a trend;
  a single run is not.
- **Destinations that vary by visitor.** Geography, language and rate limits can give a
  crawler a different answer than a reader gets.
