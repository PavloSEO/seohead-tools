# Scenario 40 — Blocked resources: the stylesheet and the bundle the crawler was refused

## The question

> Our robots.txt only blocks a few directories. Could that be why the page looks broken to
> Google?

Yes, and it is the cheapest rendering defect there is to find. A crawler that is allowed to fetch
the HTML but not the JavaScript that fills it renders the same empty page a non-rendering crawler
would have received, having paid for a browser to do it.

## Covers

- **JavaScript** — Pages with Blocked Resources

## The chain

**1. Read the rules as written.**

```bash
seohead robots-check --url https://example.com
```

Every group with its user agents, its `Allow` and `Disallow` lists, its crawl delay, and the
sitemaps it declares. A rule blocking `/assets/`, `/static/`, `/js/`, `/css/`, `/_next/` or
anything ending `.js` or `.css` is the one this scenario is about.

**2. See which resources the page actually depends on.**

```bash
seohead asset-weight-check --url https://example.com/page
```

Every `<link rel=stylesheet>` and `<script src>` the document references, with the ones in
`<head>` marked render-blocking. Cross-referencing that list against step 1's `Disallow` rules
turns "we block `/assets/`" into "we block the stylesheet this template cannot paint without".

**3. Let the crawl report the block instead of obeying it silently.**

```bash
seohead crawl-site --url https://example.com --robots report_only --out-dir ./run
```

`report_only` records what robots.txt would have prevented and crawls anyway, so the audit lists
blocked URLs rather than a shorter crawl with no explanation. `respect` is the default, and stays
the default: a crawler that ignores robots.txt by accident is a crawler nobody should run.

**4. Get the finding as a check id, in the audit.**

```bash
seohead sf run --exports-dir docs/examples/exports --out report --sitemap https://example.com/sitemap.xml
```

The sitemap stage fetches `robots.txt` itself and raises `ROBOTS_BLOCKS_RESOURCES` when a
`Disallow` matches a render-critical resource path. It is a notice by severity and a serious
finding by consequence, which is a mismatch worth reading past.

**5. Verify what came out.**

```bash
seohead log-scan --run ./run
```

## What comes out

One finding, carrying the rules that triggered it:

```json
{
  "check": "ROBOTS_BLOCKS_RESOURCES",
  "target_url": "https://example.com",
  "details": {"rules": ["/assets/", "/static/js/"]}
}
```

Beside it, the resource list from step 2 is what makes the task actionable: naming the two
directories is a report, naming the stylesheet the product template loads from one of them is a
job.

## What it costs

One request for `robots.txt`, one for the page, and one per referenced stylesheet or script — the
resource fetch is capped and concurrent. Nothing paid.

## What it cannot answer

- **Which pages are affected, page by page.** This finding is derived from robots.txt patterns
  and attaches to the site, not to a list of URLs whose own subresources were tested one at a
  time. Step 2 gives you the per-page evidence, one page at a time.
- **Another host's rules.** A resource served from a CDN domain or a third party is governed by
  that host's robots.txt, and this toolkit does not go and ask somebody else's server about its
  own rules.
- **Whether Google actually failed to render.** Blocking a resource makes incomplete rendering
  possible; only Search Console's own rendered screenshot shows what happened.
- **A resource blocked by something other than robots.txt.** A firewall, a bot filter or a
  geo-rule that answers Googlebot differently is invisible to a fetch from your machine.
