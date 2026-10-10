# Scenario 22 — Duplicate and near-duplicate content: which pages are the same page

## The question

> We have 4,000 product pages and I suspect most of them are the same three paragraphs. Prove
> it, without comparing every page to every other page.

Comparing N pages pairwise is quadratic: 4,000 pages is eight million comparisons. This chain
fingerprints each page once and only compares the candidates that land in the same bucket.

## Covers

- **Content** — Exact Duplicates · Near Duplicates

## The chain

**1. Collect page text scoped to the content region.** The crawl resolves each page's real
content area — `<main>`, then `[role="main"]`, then `<article>` — and records which strategy it
used.

```bash
seohead crawl-site --url https://example.com --out-dir ./run --max-urls 200
```

This step decides the whole answer. A shared mega-menu and footer are identical on every page by
construction; fingerprint the raw body and every page on the site is a near-duplicate of every
other. On one live WordPress post the whole-body default counted 126 template words out of 433 —
29% of the text was furniture.

**2. Scan the run.**

```bash
seohead log-scan --run ./run
```

**3. Fingerprint and cluster.** Each page becomes a 64-bit simhash; locality-sensitive hashing
groups likely matches; every pairwise similarity inside a reported cluster is verified against the
exact Hamming distance, so a cluster is never a false positive of the bucketing, nor of chaining
one candidate pair into another (a cluster is complete-linkage: every member pair clears the
threshold, not only the ones LSH happened to compare).

```bash
seohead duplicate-check --input '{"items": [{"url": "https://example.com/a", "text": "one paragraph of body copy"}, {"url": "https://example.com/b", "text": "one paragraph of body copy"}]}'
```

Two separate outputs, because they have two separate fixes:

- `exact_duplicates[]` — the extracted text hashes identically. Canonical, redirect, or noindex.
- `clusters[]` — near-duplicates above the threshold, each with its `min_similarity`.
  Differentiate or consolidate.

A cluster fully explained by exact duplication is reported once, under `exact_duplicates` only.

**4. Re-run at a different threshold for free.** The default is 0.92. Lower it to 0.85 to hunt
broadly, raise it to 0.97 for a strict list. Re-clustering the same items costs **zero
requests**, because the fingerprints are already computed.

```bash
seohead duplicate-check --threshold 0.85 --input '{"items": [{"url": "https://example.com/a", "text": "one paragraph of body copy"}, {"url": "https://example.com/b", "text": "one paragraph of body copy"}]}'
```

Non-indexable items are dropped before comparison by default: a canonicalised twin is not a
defect. Pass `--all-pages` when the canonical tags themselves are what you are auditing.

**5. Note the registry's own two checks, and where they come from.** In `audit.json`,
`DUPLICATE_BY_HASH` reports exact duplicates and `NEAR_DUPLICATE` reports near ones. Each reads
the Screaming Frog columns (`Hash`, `No. Near Duplicates`, `Closest Similarity Match`) when an
export carries them. Otherwise an SF audit computes the missing half from stored page HTML, but
only when `input.html_store_dir` is set. A native crawl never writes that store, so it names both
checks skipped instead of clean:

```json
{ "id": "DUPLICATE_BY_HASH", "reason": "no Hash/Page Hash column in Internal:All" }
```

The other skip reason for the same checks is `no stored HTML (input.html_store_dir not set)`.

So: `duplicate-check` computes; the registry checks import. Use whichever matches the evidence
you have, and say which one produced the number.

## What comes out

```json
{
  "count": 2,
  "threshold": 0.92,
  "clusters": [],
  "exact_duplicates": [
    {"hash": "fe05bcdcdc49...", "members": ["https://example.com/a", "https://example.com/b"]}
  ],
  "candidate_pairs_checked": 1
}
```

`candidate_pairs_checked` is the honest cost line: the number of pairs actually compared after
bucketing, against the quadratic number that was avoided.

## What it costs

The clustering itself is local and free of API cost, but "avoids pairwise comparison" is not
unconditional: on a templated site, shared boilerplate can dominate every page's fingerprint and
defeat LSH's bucketing (measured: 99.7%+ of a synthetic corpus in one bucket at every scale
tested, before the fix that damps such shingles). Damped, wall-clock time on that same synthetic
corpus was 0.4s/2.2s/8.9s at n=1,000/4,000/10,000 — a large improvement, but not "near-instant" at
the top of that range. The cost that usually dominates in practice is still collecting the text:
one request per page if it comes from a live crawl, none if it comes from an export you already
had.

## What it cannot answer

- **Which page of a cluster should survive.** The tool groups; a person picks the reference URL.
- **Semantic similarity.** Simhash compares shingles of words, not meaning. Two pages saying the
  same thing in different words are not near-duplicates here, and nothing in this chain finds
  them.
- **Anything reliable about pages under about 50 words.** Simhash is unstable at that length;
  treat such cluster membership as provisional and check it by eye.
- **Whether duplication is intended.** Pagination, filters and regional variants are duplicates
  by design.
- **Duplicate markup or layout.** The fingerprint is over visible text, not the DOM.
