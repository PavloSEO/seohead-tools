# Canonical destinations: where a valid-looking tag actually lands

## The question

> Which pages canonicalize to a response that is broken, and are distinct sections of the site all pointing at the homepage?

`CANONICAL_NON_INDEXABLE` already covers an observed target whose indexability says it cannot be indexed. `CANONICAL_TO_REDIRECT` covers a fetched 3xx destination. This scenario adds the missing status-specific 4xx/5xx case and a conservative homepage-group signal, without guessing what pagination or filter URLs should do.

## Covers

- `CANONICAL_TARGET_ERROR` — a fetched canonical destination returns 4xx/5xx
- `CANONICAL_HOMEPAGE_GROUP` — distinct pages across sections point at the homepage

## The chain

**1. Audit an existing Screaming Frog export set without making requests.**

```bash
seohead sf run --exports-dir docs/examples/exports --out report --tasks
```

The same audit core is used by the `sf_audit_run` MCP tool and by native crawl audits. The graph reads only `Internal:All` canonical and status evidence; this check adds no destination fetch.

**2. Read `CANONICAL_TARGET_ERROR` as an observed response, not a guess.**

```json
{
  "check": "CANONICAL_TARGET_ERROR",
  "target_url": "https://shop.example.test/products/widget-copy",
  "status_code": 404,
  "details": {
    "canonical_target_url": "https://shop.example.test/products/widget",
    "canonical_target_responses": [
      {
        "url": "https://shop.example.test/products/widget",
        "status_code": 404,
        "indexability": "Non-Indexable"
      }
    ],
    "status_coverage": "complete"
  }
}
```

The source URL and exact canonical target stay together with response and indexability evidence. A canonical target absent from the crawl, or present without a response code, remains unavailable and is named under `checks_skipped` when no observed 4xx/5xx finding can be made. A known 2xx variant clears the error verdict; 3xx stays under `CANONICAL_TO_REDIRECT`. `CANONICAL_NON_INDEXABLE` continues to cover fetched 2xx pages whose indexability evidence is negative.

**3. Review a homepage group only when the crawl supports the pattern.**

`CANONICAL_HOMEPAGE_GROUP` fires for at least three fetched, indexable source pages from at least two top-level path sections, each with its own non-empty title and H1 distinct from the fetched homepage. The audit returns exact source URLs, their sections, title/H1 evidence, and homepage response/indexability. It excludes the homepage itself, query URLs, and recognizable `/page/2` or `/paged/2` paths. Configured pagination/filter patterns from the canonical policy contract are delegated to its shared matcher and excluded from this general pattern check.

Partial native crawls withhold this site-wide pattern and report a named skip. A missing or unindexable homepage target also prevents a group verdict. These thresholds make the result a review signal; they do not prove semantic irrelevance or replace the operator's configured policy.

## What it costs

- `sf run --exports-dir` reads local files only and makes no network requests.
- A native crawl reuses its captured pages and response statuses; these checks do not fetch canonical targets again.
- No provider or paid API is involved.

## What it cannot answer

- An uncaptured canonical destination has no known HTTP status; absence from this crawl is not a 4xx or 5xx result.
- A different title/H1 and path section is explicit structural evidence, not a semantic judgement that two pages are unrelated.
- Whether a canonical is preferred for a particular page remains a site-specific decision. Configure pagination and filter expectations explicitly when needed.
