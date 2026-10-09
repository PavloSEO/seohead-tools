# Scenario 43 — Server response time: the wait before anything can start

## The question

> Every speed tool tells us to improve the server response. Which pages, and is it even the
> server?

Time to first byte is the one part of a page load that no front-end work can recover, and it is
also the number most easily corrupted by the measurement itself. This chain separates the two.

## Covers

- **PageSpeed** — Document Request Latency

## The chain

**1. Measure one page, alone, with nothing else running.**

```bash
seohead headers-check --url https://example.com/page
```

`ttfb_ms` here is a single request from your machine with no crawl competing for the origin. It
also reports the HTTP version and the cache headers, which is context the number needs: an origin
answering in 900 ms over HTTP/1.1 with no `Cache-Control` is a different problem from the same
900 ms behind a caching CDN.

**2. Ask whether anything is in front of the origin.**

```bash
seohead cdn-check --url https://example.com
```

Whether a CDN is present, and whether it is actually caching rather than passing every request
through to the origin. A pass-through CDN measures like no CDN and is a different conversation
from an absent one.

**3. Measure every page, from the crawl.**

```bash
seohead crawl-site --url https://example.com --out-dir ./run
```

Each page record carries `response_time`. Compare it with the 1.5-second threshold yourself, or
read the distribution from the run. `SLOW_RESPONSE` findings come only from `seohead sf run` over
an export whose Internal:All has a Response Time column. A whole-site picture matters more than a single page here,
because the shape of the distribution names the cause: uniformly slow is infrastructure, slow
only on one template is that template's queries.

**4. Set the rate deliberately, and treat the breaker as a finding about the rate.** A WordPress
blog crawled at ten URLs per second began returning 502 after 249 pages and the circuit breaker
stopped the run; the same six URLs re-fetched one every three seconds all answered normally, and
at three URLs per second the same site completed all 3387 of them. Reporting "this host returns
502 under load" when the load was yours is the most embarrassing thing an audit can do.

Latency, not status, is the signal that matters. On a shared-hosting catalogue under a polite 1.5
URLs per second, the origin degraded from 1196 ms to 16455 ms of TTFB and then began refusing TLS
handshakes — without ever returning an error status. That is why the crawler widens its delay on
latency rather than waiting for a non-200, and why `speed.min_delay_seconds` is a floor you set
yourself.

**5. Check the run before quoting a single millisecond of it.**

```bash
seohead log-scan --run ./run
```

**6. Hand it over with the numbers in it.**

```bash
seohead report-build --audit ./run/audit.json --format md --out ./latency.md
```

## What comes out

The single-page measurement, with the delivery context beside it:

```json
{
  "ttfb_ms": 940,
  "http_version": "HTTP/1.1",
  "findings": ["no Cache-Control header"]
}
```

and, from an `sf run` over an export, one `SLOW_RESPONSE` per page over the threshold, each carrying its own
measurement and the threshold it passed:

```json
{"check": "SLOW_RESPONSE", "details": {"response_time": 2.41, "max_s": 1.5}}
```

## What it costs

One request per single-page check, one per crawled page. No paid API. The real cost is somebody
else's origin, which is the reason the default delay is half a second and the reason to raise it
rather than lower it.

## What it cannot answer

- **What your visitors experience.** This is one machine, on one connection, from one place, at
  one moment. Field data comes from the Chrome UX Report and is not inferable from these numbers.
- **Why the server is slow.** Application, database, origin capacity, TLS negotiation and the
  network are not separable from outside. The measurement says where to look, not what to fix.
- **Whether the number is yours.** A crawl measures the origin under load you are applying. Read
  `response_time` next to the rate the run used, both of which the run manifest records.
- **Anything about the rest of the load.** First byte is the start of the page, not the page.
  Render-blocking resources and payload are the [speed render blocking scenario](speed-render-blocking.md) and
  the [speed delivery and weight scenario](speed-delivery-and-weight.md).
