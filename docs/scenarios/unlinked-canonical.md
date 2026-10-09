# Scenario 12 — The canonical nobody links to: a preferred URL with no way in

## The question

> We consolidated the duplicates with canonical tags months ago and nothing improved. What did
> we actually tell a search engine?

A canonical names a preferred URL. It does not create a route to it. When every hyperlink on
the site points at the duplicates and only the tag points at the preferred version, the page
you chose is reachable by declaration alone.

## Covers

- **Canonicals** — Unlinked · Canonicalised
- **Hreflang** — Unlinked Hreflang URLs

## The chain

**1. Crawl first, so you know what the link graph contains.**

```bash
seohead crawl-site --url https://example.com --out-dir ./run
```

This gives you `CANONICALISED` — every page that defers to another URL — and the crawl's own
record of every hyperlink it saw.

**2. Notice that the crawl declares this check skipped, and why.**

`audit.json`'s `run.checks_skipped` will contain:

```json
{"id": "UNLINKED_CANONICAL", "reason": "no Inlinks column in Internal:All"}
```

That is the honest half of the design. The check needs a per-URL inlink count to assert
"nothing points here", the native crawl's evidence frame does not carry one, and a check with
no evidence is declared absent rather than reported clean. A skipped check and a clean check
look identical in a summary that does not distinguish them, which is how a report comes to say
a site has no problem it never looked for.

**3. Run the audit over Screaming Frog's exports, where the column exists.**

```bash
seohead sf run --exports-dir ./exports --out report --tasks
```

`UNLINKED_CANONICAL` now fires for each canonical target that no hyperlink in the whole crawl
points at.

**4. Check `run.crawl_partial` before you believe any of it.**

```bash
seohead log-scan --run ./run
```

"Nothing links here" is a claim about the entire site, and it is unprovable when the crawl
stopped early — the missing link may be in the part nobody fetched. `UNLINKED_CANONICAL` is one
of five findings withheld and re-declared as a named skip on a partial crawl, alongside
`ORPHAN_PAGE`, `SITEMAP_ORPHAN`, `UNLINKED_PAGINATION_SERIES` and `HREFLANG_UNLINKED_TARGET`.

The hreflang twin reads the same way: `HREFLANG_UNLINKED_TARGET` fires for each hreflang alternate
that no ordinary hyperlink reaches, with a self-reference never counted. An alternate reached only
through `<link rel="alternate" hreflang>` is discoverable by annotation alone, the same gap as a
canonical target nobody links to.

**5. Compare with the sitemap answer rather than repeating it.**

Reachability against a sitemap is the [structure scenario](structure.md)'s job, and it is a different
comparison: sitemap-declared minus reachable. This one is canonical targets minus link
destinations. Running both is useful. Confusing them is not.

## What comes out

```json
{
  "check": "UNLINKED_CANONICAL",
  "severity": "warning",
  "target_url": "https://example.com/catalog/pump-cdm",
  "message": "Canonical target has no hyperlink pointing to it anywhere in the crawl",
  "fix_hint": "Add an ordinary internal link to the canonical target, or confirm relying on the canonical alone for discovery is intentional."
}
```

## What it costs

- One crawl. Steps 3 and 4 read files already on disk.
- Nothing paid.
- The All Inlinks export is the large one in any Screaming Frog run; on a big site it is the
  reason this chain takes longer to prepare than to execute.

## What it cannot answer

- **Whether the missing link is deliberate.** Some canonical targets are meant to be reached by
  the tag alone. The finding says so in its own fix hint, and that is the honest phrasing.
- **Whether a search engine found the page anyway.** Discovery happens through sitemaps,
  external links and history too. This chain sees one site's own hyperlinks.
- **Anything about a URL the crawl never fetched.** Cross-check a target against `pages.jsonl`
  before it reaches a client, or let `log-scan` do it for you.

The population discipline underneath that last limit is the part worth carrying out of the
scenario. A sibling check, `URL_NOT_IN_SITEMAP`, once produced **392 findings on a 124-page
site** by comparing a sitemap of pages against every destination in the link graph: 362 image
files a gallery linked to directly, five off-host links, and 30 URLs the crawl never fetched.
Link destinations are not pages. Three-quarters of that report was arithmetic, and it buried
the findings that were real.
