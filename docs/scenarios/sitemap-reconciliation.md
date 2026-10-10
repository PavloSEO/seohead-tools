# Scenario 29 — Sitemap reconciliation: what the sitemap forgot, and what nothing links to

## The question

> The sitemap says we have 900 pages. The crawl found 740. Which number is wrong?

Neither. A sitemap is what the CMS believes it published; a crawl is what the site's own links
lead to. They are two independent claims, and the interesting output is not either total but
the two disagreements between them, which have different causes and different fixes.

## Covers

- **Sitemaps** — URLs Not In Sitemap · Orphan URLs

## The chain

**1. Read the sitemap on its own first, index files expanded.**

```bash
seohead sitemap-crawl --url https://example.com/sitemap.xml
```

Child sitemaps are followed and every `loc` is returned with its `lastmod`, normalized so that
the comparison later is not defeated by a trailing slash. Knowing the declared count before the
crawl is what makes a shortfall afterwards a fact rather than a suspicion.

**2. Crawl, seeded from that sitemap.**

```bash
seohead crawl-site --url https://example.com --sitemap https://example.com/sitemap.xml --out-dir ./run
```

Seeding fetches every declared URL *and* follows the links it finds, so both claims are
measured in one run at one moment.

**3. Scan the run before reading the reconciliation.**

```bash
seohead log-scan --run ./run
```

**4. Read the disjoint sets in `summary.sitemap`.**

```json
"sitemap": {
  "urls_in_sitemap": 124,
  "urls_reached_by_links": 124,
  "in_sitemap_not_linked": ["https://example.com/services/legacy"],
  "linked_not_in_sitemap": ["https://example.com/services/new"]
}
```

`SITEMAP_ORPHAN` names a declared URL that no internal link reaches, and `ORPHAN_PAGE` names any
indexable page with no inlinks at all, except the home page; it comes only from `seohead sf run` over an
export with an Inlinks column, not from `crawl-site`. `URL_NOT_IN_SITEMAP` names a real, indexable page the
sitemap never declared. One needs a link; the other needs a sitemap entry.

**5. Hand over one list per fix.**

```bash
seohead report-build --audit ./run/audit.json --format xlsx --out ./sitemap.xlsx
```

## What comes out

Two short lists instead of two large totals. Pages nothing links to are usually a navigation
that stopped including a section; pages missing from the sitemap are usually a generator that
filters by a field somebody stopped filling in.

There is a fourth set beside them, `linked_not_comparable`, and it exists because of a real
defect. `URL_NOT_IN_SITEMAP` once fired 392 times on a 124-page site — 74% of the whole report
— because it compared *link destinations* against the sitemap rather than pages, so images and
`wa.me` links were reported as missing sitemap entries (issue #94). Keeping the incomparable
population visibly separate is what stopped it, and the dominance rule is what caught it: any
single check above roughly half the findings is a bug hypothesis before it is a finding.

## What it costs

One request per declared URL plus one per URL discovered by links, at the configured rate.
Nothing paid.

## What it cannot answer

- **Whether a page is indexed.** Reachable is not indexed, and declared is not indexed. That
  answer is only in Search Console.
- **Why a page is orphaned.** The finding says nothing links to it. Whether that is a broken
  menu or a deliberate unlisting is a person's call.
- **Orphan status from a partial crawl.** "Nothing links here" cannot be proven by a crawl that
  stopped early, so those findings are withheld rather than guessed — check `run.crawl_partial`.
- **Links added by JavaScript.** A menu assembled client-side is invisible to a static crawl,
  and every orphan below it is a false one. See the [rendering scenario](rendering.md) first.
- **Whether the sitemap should contain the missing pages.** Some pages are deliberately not
  declared. The list is a question for the person who owns the site structure.
