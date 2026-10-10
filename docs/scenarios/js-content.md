# Scenario 36 — Content only JavaScript produces: what a non-rendering crawler receives

## The question

> The category page shows forty products and a full sub-menu in my browser. The audit says it
> has thirty-eight words and one internal link. Which of you is wrong?

Neither. The browser executed the JavaScript; the crawler read the server response. This chain
measures how far apart those two documents are on one page, then decides whether closing the
gap across the whole site is worth paying for.

## Covers

- **JavaScript** — Contains JavaScript Content · Contains JavaScript Links

## The chain

**1. Ask what the page is built on before spending a browser on it.**

```bash
seohead tech-detect --url https://example.com
```

A client-side framework signature is not proof that content is assembled in the browser, but its
absence is a good reason not to ask the question at all. On the WordPress site behind several of
these scenarios, raw and rendered were materially equivalent, and rendering the crawl would have
bought nothing but wall time.

**2. Compare the server response with the rendered DOM, one page per template.**

```bash
seohead render-check --url https://example.com/page
```

The same snapshot is built from both documents — words, internal links, title, H1, canonical,
JSON-LD types, and images including a CSS `background-image` that only computed styles resolve.
Differences are reported against a materiality threshold: 30% of the copy, or 30% of the
internal links. Five widget words do not make a page JavaScript-dependent, and a check that says
they do is a check nobody reads twice.

Two findings belong to this scenario. Copy that exists only after rendering is JavaScript
content. Hyperlinks that exist only after rendering are JavaScript links, and they cost more,
because a link a crawler never sees is a page it never reaches. A raw response with zero internal
links is reported whatever its share, since zero has no share to measure.

**3. Escalate the crawl for the templates that differ, not for the site.** The mechanics — probe
two URLs per detected URL pattern, escalate only the patterns that differ, cap the render budget
— are the [rendering scenario](rendering.md). What matters here is that the crawl carries the result forward:

```bash
seohead crawl-site --url https://example.com --config ./crawl.json --out-dir ./run
```

Every page records `representation` as `static`, `rendered` or `legacy_fragment`, so word counts
and outlink counts from two different measurement methods never end up averaged into one number.

**4. Check the run against itself before quoting any of it.**

```bash
seohead log-scan --run ./run
```

Words counted on a zero-byte page and pages measured two ways where only some say which are both
rules this scanner has, and both are exactly what a half-finished rendering escalation produces.

## What comes out

The shape of a page whose menu is hydrated client-side:

```json
{
  "js_dependent": true,
  "raw": {"words": 38, "links": 1},
  "rendered": {"words": 812, "links": 34},
  "findings": [
    "33 of 34 internal links appear only after JavaScript, reducing or preventing crawl discovery"
  ]
}
```

The link count is the finding to lead with. Copy that arrives late is copy that may be indexed
late; a navigation that arrives late is a site the crawler walks as a dead end.

## What it costs

One request for `tech-detect`, one request plus one headless render for each `render-check`, and
then the crawl. Escalation adds one probe render per sampled URL and one render per escalated
page, bounded by `rendering.escalation.max_render_urls`. Renders cost seconds and real memory
each; static fetches cost milliseconds. Nothing here is paid.

## What it cannot answer

- **Whether Google rendered this page, or when.** A headless Chromium is not Googlebot's
  renderer and does not share its queue or its budget. The question answered here is "is this
  content JavaScript-dependent", which is the half you can act on.
- **Whether the missing content cost traffic.** There is no search-engine data in this loop.
- **What content a user interaction would produce.** Nothing is clicked, scrolled or submitted;
  copy behind a tab that loads on click is not in either document.
- **Anything about a template that was never sampled.** Escalation probes patterns, so a page
  whose URL shape no sampled URL shares was measured statically. `pages_by_representation` says
  which population each number describes.
