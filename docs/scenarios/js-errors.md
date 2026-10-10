# Scenario 42 — JavaScript errors: the exception that stops a page halfway through building

## The question

> Some product pages have content and some do not, and it is not the same ones every time. What
> is different about them?

Often nothing about the page — something about the run. One uncaught exception during hydration
stops every line of script after it, and the page a crawler stores is whatever had been built at
that moment. This chain captures the errors and the picture, and is explicit about how far the
toolkit takes them.

## Covers

- **JavaScript** — Pages With JavaScript Errors

## The chain

**1. Establish that rendering matters here at all.**

```bash
seohead render-check --url https://example.com/page
```

If raw and rendered are materially equivalent, a console error is a bug for the front-end team
and not a crawl finding, and this chain ends here.

**2. Turn on the two rendering artifacts before crawling.** In the crawler config:

```json
{"rendering": {"mode": "js", "artifacts": {"console_errors": true, "screenshots": true}}}
```

Both default to off, because both cost: one holds a console listener open for every rendered
page, the other writes a full-page PNG per URL to disk.

**3. Crawl with rendering escalated.**

```bash
seohead crawl-site --url https://example.com --config ./crawl.json --out-dir ./run
```

Error-level console messages are captured per rendered URL, up to 100; anything beyond that is
counted in `console_errors_omitted`. Each message is truncated and has secrets redacted. Each
screenshot is written to `render_artifacts/` under the run directory, named by a truncated hash of
the URL plus a random suffix, so two URLs cannot collide and reruns never overwrite a file.

**4. Look at the pages that rendered short.** The audit's own thin-content and low-text-ratio
checks, read against pages whose `representation` is `rendered`, are the shortlist: a rendered
page with less copy than its template siblings is where an exception fired.

**5. Confirm the run is internally consistent.**

```bash
seohead log-scan --run ./run
```

Words counted on a zero-byte page and a finding about a URL the run never fetched are both rules
here, and a rendering pass that failed halfway produces exactly those shapes.

## What comes out

Per rendered URL, during the run:

```json
{
  "ok": true,
  "url": "https://example.com/page",
  "console_errors": ["TypeError: Cannot read properties of undefined (reading 'sku')"],
  "screenshot_path": "./run/render_artifacts/9f2c1a7b04e6d3f581bc2ad0-k3x8q1zv.png"
}
```

The screenshot is the part that survives the run on disk, and it is the part that ends an
argument: a stakeholder who does not read `audit.json` does read a picture of their own product
page with an empty grid where the products should be.

## What it costs

A headless browser per rendered URL — seconds and real memory each — plus a PNG per URL on disk,
which on a large escalation is the largest artifact the run produces. Console capture itself is
free once the browser is already open. Nothing is paid.

## What it cannot answer

- **There is no check id for this.** Console errors are captured per rendered URL while the
  browser holds the page; they are not aggregated into a finding, not scored, and not written
  into `audit.json`, which records only the escalation summary. Treat this as evidence you read,
  not a number you report.
- **Whether Googlebot hit the same error.** A different browser version, a different network, a
  different moment. An error that reproduces on every render is worth acting on; one that fires
  intermittently is a lead.
- **Which line of which bundle.** The message text is captured, not a stack trace or a source
  map resolution.
- **An error that never reaches the console.** Anything swallowed by a `try`/`catch` or reported
  only to a monitoring service is invisible here, and those are common in exactly the frameworks
  this matters for.
