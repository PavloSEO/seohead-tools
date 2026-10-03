# Provider capability and workflow matrix

Generated from `seohead/data_sources/providers.py` and the workflow catalogue in `seohead/provider_matrix.py` — do not edit by hand. Regenerate with:

```bash
python scripts/generate_provider_matrix.py
```

Issue #779 (epic #753). Related implementation issues: #716, #718, #719, #724, #730 — this matrix links them instead of duplicating their scopes.

## How to read the statuses

- **supported** — a shipped command or collection dispatch reaches the operation, with offline tests behind it.
- **partial** — the workflow works but the shipped surface is narrower than the declared registry entry; the gap is named in the limitations column.
- **unsupported** — declared or plausible work the code deliberately does not do; see *Unsupported work* below.
- **unverified** — no code path exists to confirm or deny; nothing in this matrix is labelled live-verified: a registry entry is a declared contract, a present credential is configuration state, and only an explicit `provider-verify` read returning `target_access=verified` proves live target access.

## Provider inventory

Rendered from the registry exactly as `provider-registry` reports it.

| Provider | Access | Operations | Credentials | Quota / cost | Privacy |
|---|---|---|---|---|---|
| `arsenkin` | read-only, paid | keyword_frequency, serp_clustering | api_token | paid limit credits | aggregate statistics |
| `yandex_cloud` | read-only, paid | wordstat, web_search | api_key, folder_id | provider quota and recorded spend | aggregate statistics |
| `gsc` | read-only | verify, properties, search_analytics, inspection, sitemaps | oauth_bearer, service_account | Google Search Console row and request limits | restricted site/account data |
| `crux` | read-only | current, history | api_key | Google Cloud API quota | aggregate statistics |
| `pagespeed` | read-only | mobile_samples, desktop_samples | api_key | Google API quota | aggregate statistics |
| `ga4` | read-only | landing_pages | oauth_bearer | GA4 Data API quota | restricted site/account data |
| `metrika` | read-only | counters, aggregate_report, ~~raw_logs~~ (excluded) | oauth_bearer | Yandex Metrika API quota | restricted site/account data |
| `yandex_webmaster` | read-only | hosts, indexing, crawl, sitemaps, search_performance, summary, diagnostics, sqi_history, search_history, query_history, in_search_history, events_history, indexing_history, important_urls, broken_links_history, external_links_history | oauth_bearer | Yandex Webmaster application quota | restricted site/account data |
| `bing_webmaster` | read-only | sites, crawl, links, keywords, search_performance | api_key | Bing Webmaster API quota | restricted site/account data |
| `dataforseo_backlinks` (off by default) | read-only, paid, off by default | backlinks_summary | login, password | paid per provider response | restricted site/account data |
| `indexnow` (off by default) | confirmed write, off by default | submit | submission_key | provider submission quota | caller-supplied URL list, public endpoint |
| `wayback` | read-only | history | none | public service pacing | public data |
| `crtsh` | read-only | subdomains | none | public service availability | public data |

## Specialist workflow matrix

| Workflow | Use case | Providers | Shipped surface | Status | Auth | Cost / quota | Privacy | Limitations | CSV fallback |
|---|---|---|---|---|---|---|---|---|---|
| `yandex-demand` | Expand a seed phrase and read demand seasonality for Yandex | `yandex_cloud` | `keywords-expand`, `keywords-seasonality`, `regions-tree` | supported | API key + folder ID | Paid; Wordstat hourly quota; charges journaled in spend-report | aggregate | Base frequency only — the API has no ! / + / [] operators and base counts run roughly 9x exact; use Arsenkin for exact values | not applicable — demand data is not URL-keyed |
| `yandex-exact-frequency` | Exact !W frequency the Wordstat API does not expose | `arsenkin` | `keywords-exact` | supported | API token | Paid; consumes Arsenkin account limits; task_id journaled at billing time | aggregate | Billed at task creation; a timed-out poll is retrievable by task_id | not applicable |
| `google-demand` | Search volume, seed expansion, and keyword difficulty for Google | `dataforseo_backlinks` | `google-keywords` | supported | Login + password | Paid per response; sandbox is the default and is free | restricted | Keyword endpoints run through the dedicated DataForSEO module, not the registry; Russia and Belarus locations are refused by the coverage guard before any paid call | not applicable |
| `serp-collection` | Fetch ranked results for queries on Yandex or Google | `yandex_cloud`, `dataforseo_backlinks` | `serp-fetch`, `google-serp` | supported | Yandex: API key + folder ID; DataForSEO: login + password | Metered; Yandex async endpoint only — the ~16x-costlier sync endpoint is excluded | aggregate | Queries billed but not returned before timeout stay visible in the spend journal as named operations | not applicable |
| `serp-clustering` | Cluster a keyword set by overlapping search results | `arsenkin` | `keywords-cluster` | partial | None for keywords-cluster; Arsenkin API token would be required for the declared provider operation | keywords-cluster is free; the declared Arsenkin operation is paid | aggregate | The registry declares arsenkin serp_clustering, but provider-collect deliberately refuses the paid Arsenkin contract and no dedicated handler ships it — the shipped surface is the free draft clustering only | a user-supplied keyword list can seed keywords-cluster; no provider CSV join |
| `search-console-evidence` | Clicks, impressions, position, indexing verdicts, and sitemap status for a property you own | `gsc` | `gsc-query`, `provider-auth`, `provider-verify`, `provider-collect` | supported | OAuth bearer or service account; durable grant via provider-auth | Free within Search Console row and request limits | restricted | Verification distinguishes an authenticated account from verified access to the requested property | yes — an exported Search Console CSV joins the crawl via provider-join |
| `webmaster-evidence` | Yandex and Bing webmaster data: hosts, indexing, diagnostics, search performance, history | `yandex_webmaster`, `bing_webmaster` | `provider-verify`, `provider-collect` | supported | Yandex: OAuth bearer; Bing: API key | Free within each webmaster API quota | restricted | Reachable only through the generic provider-verify/provider-collect commands — no dedicated CLI command ships for either provider | yes — URL-keyed exports join via provider-join |
| `traffic-analytics` | Counter configuration, aggregate reports, and landing-page evidence | `metrika`, `ga4` | `metrika-counters`, `metrika-setup`, `metrika-report`, `metrika-traffic-pdf`, `provider-collect` | partial | OAuth bearer for both providers | Free within Metrika and GA4 Data API quotas | restricted — may contain personal identifiers; kept out of reports and commits | Metrika raw Logs API is intentionally unreachable; GA4 ships only the landing_pages operation and only via provider-collect | yes — URL-keyed analytics exports join via provider-join |
| `field-vitals` | Core Web Vitals as real users measured them, at origin or URL level | `crux`, `pagespeed` | `crux-report`, `provider-collect` | supported | Google Cloud API key for both providers | Free within Google API quotas | aggregate | PageSpeed mobile_samples/desktop_samples run only via provider-collect — no dedicated command ships | no provider CSV join; CrUX/PSI have no user-export path here |
| `link-evidence` | Backlink summary for a target from a paid index | `dataforseo_backlinks` | `provider-collect` | partial | Login + password | Paid per provider response; provider is off by default | restricted | Only backlinks_summary is declared; discovering a competitor's full profile is out of scope — backlinks-check audits a caller-supplied donor list instead and is not a provider | a supplied donor-page list is the input to backlinks-check; no provider CSV join |
| `url-submission` | Notify Bing, Yandex, Naver, and Seznam that URLs changed | `indexnow` | `indexnow-submit` | supported | Self-generated key hosted on the target site | Free; provider submission quota; off by default | caller-supplied URL list sent to a public endpoint | Confirmed write, not a collection — provider-collect refuses it by contract; Google has not joined IndexNow | not applicable — write operation |
| `public-recon` | Snapshot history of a URL and subdomains named in public certificate logs | `wayback`, `crtsh` | `wayback-history`, `crtsh-subdomains` | supported | None — public services | Free; public service pacing and availability | public | provider-verify reports not_required — there is no access contract to verify | not applicable |
| `evidence-join` | Attach external URL-keyed rows to a crawl and surface orphan candidates for explicit review | `gsc`, `ga4`, `metrika`, `yandex_webmaster`, `bing_webmaster` | `provider-join`, `provider-replay` | supported | None — operates on already-collected or user-supplied rows | Free; local operation | restricted inputs stay local; artifact directory is created mode 0700 | Never changes the crawl frontier; unkeyable, crawl-only, and external-only populations stay named and separate | yes — this is the CSV fallback path itself |

## Unsupported work, stated explicitly

- **arsenkin serp_clustering via provider-collect** — declared in the registry but deliberately refused by the collection dispatch; tracked by the dedicated-operation contract rather than duplicated here
- **metrika raw_logs (Yandex Logs API)** — excluded by design — raw logs may contain personal identifiers and must not reach reports or commits
- **dedicated CLI commands for ga4, pagespeed, yandex_webmaster, and bing_webmaster** — these providers are reachable only through provider-verify/provider-collect
- **Google IndexNow submission** — Google has not joined IndexNow; no operation can claim it
- **competitor backlink discovery beyond backlinks_summary** — out of declared scope; backlinks-check covers a caller-supplied donor list only

## Phased first release

The first release phase covers the workflows whose evidence is free or already bounded by first-party quotas: `public-recon`, `evidence-join`, `search-console-evidence`, `webmaster-evidence`, `field-vitals`, `traffic-analytics`, and `url-submission` behind its existing confirmed-write gate. Phase two adds the metered demand and SERP workflows (`yandex-demand`, `yandex-exact-frequency`, `google-demand`, `serp-collection`) once the caller deliberately configures the paid credentials they require — sandbox remains the DataForSEO default. Deferred by design, not by omission: `serp-clustering` via the paid Arsenkin contract, Metrika raw logs, dedicated CLI commands for the collect-only providers, and any backlink discovery beyond `backlinks_summary`. This is a selected subset of the provider landscape, not a claim that every SEO data service is covered.

Every row above reflects code and test evidence in this repository; no provider is described as live-verified by this document.
