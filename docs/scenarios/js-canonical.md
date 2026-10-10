# Scenario 39 — The canonical only rendering reveals: an indexing signal a script decides

## The question

> We set canonicals correctly in the CMS. Why is the wrong URL being reported as canonical?

Because something rewrote it after the response left the server. A canonical is a hint, but it is
a hint about which URL exists at all, and one that appears or changes during rendering is a hint
half the crawlers reading the page never receive.

## Covers

- **JavaScript** — Canonical Mismatch · Canonical Only in Rendered HTML

## The chain

**1. Read the canonical the server sent.**

```bash
seohead parse --url https://example.com/page
```

An empty `canonical` here, on a template the CMS is supposed to fill, is the whole finding
already: whatever a browser later shows was added by a script.

**2. Compare the two documents.**

```bash
seohead render-check --url https://example.com/page
```

The canonical is compared field by field with no materiality threshold, and any difference
produces its own finding. The message is deliberately about the class of defect rather than the
two strings, because both cases — a canonical that appears only after rendering, and one that
rendering changes — have the same fix: emit it server-side.

**3. Establish how far the pattern spreads.**

```bash
seohead crawl-site --url https://example.com --config ./crawl.json --out-dir ./run
```

With `rendering.mode` set to `js`, an escalated page's canonical is re-read from the rendered
DOM, and the canonical checks run against it: canonical to a redirect, canonical
to a non-indexable URL, a canonical carrying a fragment. The static run of the same site answers
the same questions about the document a non-rendering crawler received; the two together are the
mismatch.

**4. Diff them, so the mismatch is a list rather than an impression.**

```bash
seohead compare-crawls --before ./old-audit.json --after ./new-audit.json
```

**5. Check both runs.**

```bash
seohead log-scan --run ./run
```

`log-scan` has a rule for exactly the shape this area produces: a canonical called a redirect
while that same URL answered 2xx in the same run. A site that serves both slash forms of a URL
made that fire on seventy-eight live pages once, and it was the tool that was wrong.

## What comes out

```json
{
  "js_dependent": true,
  "raw": {"canonical": ""},
  "rendered": {"canonical": "https://example.com/page?variant=blue"},
  "findings": [
    "The canonical URL is injected or changed by JavaScript; this indexing directive should not depend on rendering"
  ]
}
```

A canonical that a script builds from the current query string is the common version of this, and
it is worth naming in the task: the value differs per visit, which is not a property any indexing
signal should have.

## What it costs

One request per `parse`, one request plus one render per `render-check`, and two crawls if you
want the site-wide mismatch list. Nothing paid.

## What it cannot answer

- **Which canonical Google selected.** Google treats the tag as one input among several and
  routinely picks a different URL. Only Search Console reports the choice, and there is no Search
  Console data in this loop.
- **A canonical sent as an HTTP header that differs by user agent.** One fetch, one answer.
- **Whether the two URLs are really duplicates.** That is a content question —
  the [content scenario](content.md) and `duplicate-check` — not a directive question.
- **A canonical written after the capture milestone.** The DOM is read at `load` by default.
