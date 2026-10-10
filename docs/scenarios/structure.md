# Scenario 1 — Structure: what is unreachable, buried, or missing from the sitemap

## The question

> We publish constantly and nothing seems to get indexed. Is anything actually reachable?

## Covers

- **Sitemaps** — URLs Not In Sitemap · Orphan URLs
- **Links** — Pages With High Crawl Depth · Pages Without Internal Outlinks
- **Analytics** — Orphan URLs · Bounce Rate Above 70% · No GA Data · Non-Indexable with GA Data
- **Response Codes** — Internal Redirection (3XX)

## The chain

**1. Crawl, seeded from the sitemap, so both claims about the site are on the table.**

```bash
seohead crawl-site --url https://example.com --sitemap https://example.com/sitemap.xml --out-dir ./run
```

A sitemap and a crawl are two independent claims about which pages exist. Seeding from the
sitemap fetches every declared URL *and* follows its links, so the disagreement between the two
is measurable instead of assumed.

**2. Read the reconciliation.** `audit.json`'s `summary.sitemap` holds three disjoint sets:

| Set | Meaning |
|---|---|
| `in_sitemap_and_linked` | declared, and reachable by following links |
| `in_sitemap_not_linked` | declared, but nothing links to it — an orphan |
| `linked_not_in_sitemap` | a real page the sitemap forgot |

plus `linked_not_comparable`: everything reachable that a sitemap of pages is not supposed to
declare — images, off-host links, URLs never fetched. Those are facts about the site, not
sitemap defects, and keeping them separate is what stopped this check from producing 392
findings on a 124-page site.

**3. Check the links themselves.**

```bash
seohead links-check --url https://example.com --internal-only
```

**4. Confirm the redirects are single hops.**

```bash
seohead redirects-check --url https://example.com/old
```

On one live blog, 1450 of 3387 crawled URLs were 301s and 1448 of those were a plain missing
trailing slash: 43% of the crawl budget spent on one template's link format.

**5. Scan the run before reporting it.**

```bash
seohead log-scan --run ./run
```

**6. Join traffic to the crawl, if you have a URL-keyed export.** Use one row per URL with a
visits column and a bounce column (for example `url,visits,bounce`):

```bash
seohead crawl-enrich --audit ./run/audit.json --external-csv ./analytics.csv --visits-column visits --bounce-column bounce
```

It reports pages with traffic that nothing links to, pages with no analytics row, non-indexable
pages with visits, and matched pages with a bounce rate above 70%. A named column that is missing
from the CSV is refused by name, and an unreadable value is counted rather than read as zero.

## What comes out

```json
"sitemap": {
  "urls_in_sitemap": 124,
  "urls_reached_by_links": 124,
  "in_sitemap_not_linked": ["https://example.com/services/legacy"],
  "linked_not_in_sitemap": ["https://example.com/services/new"]
}
```

Two names, two different jobs: one page needs a link, the other needs a sitemap entry.

## What it costs

One request per page plus the sitemap. Nothing paid.

## What it cannot answer

- **Whether a page is indexed.** Reachability is not indexation. That needs Search Console.
- **Why a page is orphaned.** The tool says nothing links to it; whether that is a broken menu
  or a deliberate unlisting is a person's call.
- **Anything about links added by JavaScript.** See the [rendering scenario](rendering.md).
- **Live analytics.** The join reads an offline CSV you supply; it does not fetch Metrika or GA4
  data (#990, #984).
- **Orphan status on a partial crawl.** "Nothing links here" is unprovable when the crawl
  stopped early, so those findings are withheld rather than guessed — check
  `run.crawl_partial`.
