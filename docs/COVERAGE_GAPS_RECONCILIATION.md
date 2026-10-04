# Coverage gap reconciliation

Generated from `docs/COVERAGE_GAPS.md` by `scripts/generate_coverage_gap_reconciliation.py`.
A row is covered only when the map names a shipped check/tool or this reconciliation carries a reviewed registry override; partial and out-of-scope are not readiness claims.

**100 rows:** covered_registry_or_tool=18, missing=70, out_of_scope=8, partial=4.

| Row | Name | Reconciled state |
|---|---|---|
| 1.1 | Real Core Web Vitals (LCP/INP/CLS) | partial |
| 1.2 | TTFB separate from `response_time` | missing |
| 1.3 | FCP / render speed | missing |
| 1.4 | Response compression (Brotli/gzip) | partial |
| 1.5 | Cache-Control / cacheability | partial |
| 1.6 | Render-blocking resources | missing |
| 1.7 | Page weight (total) | missing |
| 2.1 | Authorship (byline) | covered_registry_or_tool |
| 2.2 | Publish/update dates | covered_registry_or_tool |
| 2.3 | About / Contact pages | covered_registry_or_tool |
| 2.4 | Privacy Policy / Terms | covered_registry_or_tool |
| 2.5 | Outbound citations | covered_registry_or_tool |
| 2.6 | YMYL detection | partial |
| 2.7 | Trust signals / disclaimers | missing |
| 3.1 | ARIA roles and labels | out_of_scope |
| 3.2 | Color contrast | out_of_scope |
| 3.3 | Focus-visible / keyboard | out_of_scope |
| 3.4 | Form labels | out_of_scope |
| 3.5 | Heading order (a11y) | out_of_scope |
| 3.6 | Skip link | out_of_scope |
| 3.7 | Link text (descriptive) | missing |
| 3.8 | Touch-target size | out_of_scope |
| 3.9 | Table headers | out_of_scope |
| 4.1 | raw/render diff (title, desc, h1, canonical, noindex) | missing |
| 4.2 | content/links diff | missing |
| 4.3 | SSR vs CSR detect | missing |
| 4.4 | Render-blocking resources for bots | missing |
| 5.1 | JS redirect | missing |
| 5.2 | URL case normalization | missing |
| 5.3 | Soft 404 | missing |
| 6.1 | Canonical chain | missing |
| 6.2 | Canonical -> redirect | missing |
| 6.3 | Canonical -> 4xx/5xx | covered_registry_or_tool |
| 6.4 | Canonical -> homepage (stamp) | covered_registry_or_tool |
| 6.5 | Canonical header vs tag | missing |
| 6.6 | Canonical contains a fragment | missing |
| 6.7 | Canonical outside `<head>` | missing |
| 6.8 | Invalid attribute in canonical annotation | missing |
| 7.1 | No x-default | missing |
| 7.2 | No self-reference | missing |
| 7.3 | lang ≠ page language | missing |
| 7.4 | Relative URL in hreflang | missing |
| 7.5 | No return link | missing |
| 7.6 | hreflang -> non-canonical | missing |
| 7.7 | hreflang -> noindex | missing |
| 7.8 | hreflang -> redirect/4xx | missing |
| 7.9 | Duplicate lang per target | missing |
| 7.10 | Duplicate lang per source | missing |
| 7.11 | Malformed language/region code | missing |
| 7.12 | Outside `<head>` | missing |
| 8.1 | follow/nofollow conflict per target | missing |
| 8.2 | Nofollow onto an indexable page | covered_registry_or_tool |
| 8.3 | External without nofollow | missing |
| 8.4 | HTTP links on an HTTPS page | covered_registry_or_tool |
| 8.5 | Localhost/127.0.0.1 in links | missing |
| 8.6 | Generic anchor text | missing |
| 8.7 | Anchor without title (duplicating) | missing |
| 9.1 | "no alt" vs "empty alt" | missing |
| 9.2 | Long alt (>100 chars) | missing |
| 9.3 | `<picture>` without `<img>` | missing |
| 9.4 | Modern format (WebP/AVIF) | missing |
| 9.5 | Responsiveness (srcset) | missing |
| 9.6 | Lazy-loading | missing |
| 9.7 | Filename quality | missing |
| 10.1 | Tracking parameters | missing |
| 10.2 | Session ID in URL | covered_registry_or_tool |
| 10.3 | Stop words in slug | missing |
| 10.4 | Trailing-slash desync | covered_registry_or_tool |
| 10.5 | WWW canonicalization | missing |
| 11.1 | Duplicate `id`s | covered_registry_or_tool |
| 11.2 | No `<!DOCTYPE>` | covered_registry_or_tool |
| 11.3 | No charset | covered_registry_or_tool |
| 11.4 | Multiple `<head>`/structural dupes | covered_registry_or_tool |
| 11.5 | Block elements in `<head>` | covered_registry_or_tool |
| 11.6 | Lorem ipsum / placeholder | covered_registry_or_tool |
| 11.7 | MIME vs extension | covered_registry_or_tool |
| 12.1 | Invalid rel=next/prev | missing |
| 12.2 | Canonical chain on pagination | missing |
| 12.3 | Pagination loop | missing |
| 12.4 | Sequence gap | missing |
| 12.5 | Pagination orphan | missing |
| 13.1 | Required fields per type | missing |
| 13.2 | Type-specific scenarios | missing |
| 13.3 | Schema drift | missing |
| 14.1 | No CSP | missing |
| 14.2 | No X-Content-Type-Options | missing |
| 14.3 | No Referrer-Policy | missing |
| 14.4 | No Permissions-Policy | missing |
| 14.5 | SSL certificate lifetime | missing |
| 14.6 | Secrets leaked in HTML | missing |
| 14.7 | Forms on HTTP | missing |
| 15.1 | OG:title/description/url/image present | missing |
| 15.2 | OG:image pixel size | missing |
| 15.3 | OG:url vs canonical | missing |
| 15.4 | Twitter Card | missing |
| 16.1 | Cookie-consent / CMP | missing |
| 17.1 | AI bots: training vs retrieval | missing |
| 17.2 | Semantic structure / citability | missing |
| 18.1 | Keyword stuffing | missing |
| 18.2 | Title = brand only | missing |

## Remaining priority order

1. Required schema fields per type (13.1) — live `schema-check` remains broader than audit-registry parity.
2. JavaScript redirects (5.1) — navigation provenance is captured; a registered audit finding still needs a reviewed emission policy.
3. Disclaimer/editorial-policy evidence (2.7) — no safe generic signal is shipped.
4. Hreflang language/relative/multi-language gaps (7.3, 7.4, 7.9) — raw declaration or graph evidence is still required.
5. Explicit policy-dependent rows such as external dofollow and slug stop words (8.3, 10.3).
