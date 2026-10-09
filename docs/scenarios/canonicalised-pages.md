# Scenario 11 — Canonicalised pages: how much of the site is deliberately not itself

## The question

> Most of our URLs come back marked "canonicalised". Is that the tidy result of a cleanup, or
> is it the problem we were sent to find?

Both, usually. A canonicalised page is a page the site fetched, rendered, linked to, and then
asked search engines to ignore in favour of another one. Counting them is how you find out
whether that was a decision or an accident of the URL scheme.

## Covers

- **Canonicals** — Canonicalised · Non-Indexable Canonical

## The chain

**1. Crawl once, and read the two totals side by side.**

```bash
seohead crawl-site --url https://example.com --out-dir ./run
```

`summary.totals` carries `html_pages` and `html_indexable`. The gap between them is every page
excluded from the indexable population, and a canonical pointing elsewhere is one of the three
ways in (the others are a `noindex` directive and a non-200 status). `CANONICALISED` is a
notice, not a warning, precisely because the tool cannot tell a consolidation from a mistake.

**2. Find out what the canonicalised pages point at.**

`CANONICAL_NON_INDEXABLE` is the finding that turns a notice into a defect: page A defers to
page B, and B is itself excluded. Nothing in that pair is eligible, which is a different
outcome from the one whoever wrote the tag intended.

**3. Ask how much internal linking is spent on them.**

```bash
seohead links-check --url https://example.com --internal-only
```

A site that links heavily to URLs it then canonicalises away is paying twice: once in crawl
requests, once in internal link equity that lands on a page asking not to be ranked. This is
the number that turns "canonicalised" from a status into a cost.

**4. Confirm the run is internally consistent.**

```bash
seohead log-scan --run ./run
```

**5. Export the list for whoever owns the templates.**

```bash
seohead report-build --audit ./run/audit.json --format csv --out ./canonicalised.csv
```

A CSV rather than a document, because the useful form of this answer is a column of URLs
somebody can group by path prefix. Canonicalised pages almost never arrive one at a time; they
arrive as a facet, a sort order, a print view, or a pagination scheme.

## What comes out

The shape that makes the decision, from `summary.totals`:

```json
"totals": {
  "urls_crawled": 6,
  "html_pages": 5,
  "html_indexable": 4
}
```

Read the third number against the second before reading any finding. A site where
`html_indexable` is a small fraction of `html_pages` is not a site with a canonical bug; it is
a site whose URL scheme generates pages nobody wants, and the fix is upstream of the tag.

## What it costs

- One request per crawled page, plus one per internal link destination in step 3.
- Nothing paid.
- `links-check` fetches destinations, so on a large site scope it deliberately: it is the most
  expensive step here by a wide margin.

## What it cannot answer

- **Whether the consolidation was correct.** The tool counts canonicalised pages. Whether the
  duplicate should have been a canonical, a redirect, or a page that never existed is a
  decision about the site, not a measurement of it.
- **Whether the canonicalised URL still receives traffic.** There is no analytics or Search
  Console data in this loop. A page that is canonicalised and still ranking is invisible here.
- **Whether the target is the same content.** Canonicalisation to an unrelated page is a real
  and common defect, and this chain will call it clean. The [duplicate-content scenario](duplicate-content.md) compares what
  the pages actually contain.
- **Anything about pages the crawl did not reach.** `run.crawl_partial` decides how much of the
  ratio above is a fact about the site rather than about the URL budget.
