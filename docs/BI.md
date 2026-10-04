# Offline BI evidence packages

`bi-export` projects saved evidence into typed pages, findings, metric observations,
link occurrences, URL cohort observations and coverage CSV partitions, plus a versioned manifest. It performs
no crawl, provider request or external publication. Supply exactly one `scan` or
`audit`, an explicit new output directory and optional saved provider join artifacts.

```bash
seohead bi-export --audit examples/audit.json --out-dir ./bi-package
```

Partitions declare row counts, schema/grain/keys, byte counts and SHA-256 hashes.
Null/unknown/unavailable states and reasons stay explicit; measured numeric zero
stays zero. Metric observations preserve their original provider/dimension/period
grain. Unmatched and unkeyable populations remain identifiable. This projection
does not add metric values or infer causal effects. CSV formula-like cells receive
an explicit safety prefix recorded in the manifest.

## Cohorts

`cohorts` is a URL-level, inspectable derived dataset. Each row retains its source
URL, normalized key, definition version, membership state, evidence state and reason.
It has no site-wide or causal interpretation. The initial definitions are observed
HTTP-status group, captured indexability, crawl-relative depth (deep is retained depth
at least 3), and observed unique-inlink share. An inlink share names its numerator,
denominator and link-extraction state. It is populated only when extraction is complete;
partial or unavailable link evidence stays unclassified.

Pass an explicit Search Console axis to add the traffic-versus-search quadrants:

```bash
seohead bi-export --audit examples/audit.json --provider-join ./gsc.json \
  --provider-join ./ga4.json --search-metric clicks --out-dir ./bi-package
```

The `search_visibility_vs_sessions` cohort requires exactly one dimensionless,
matched, measured Search Console `clicks` or `impressions` observation and exactly
one GA4 `sessions` observation for the same URL, inclusive period and timezone.
Both sources must declare complete, unsampled, unthresholded and untruncated
collection. It never sums source rows, treats an absent row as zero, or combines
clicks with sessions. Missing, suppressed, partial, duplicate-window or incompatible
evidence is an explicit unclassified/incomplete row. The exported source-observation
references retain the two labelled axes, dates and timezone.

## Reporting consumers

The local package is the source of truth. A future Google Sheets transport must preflight
the manifest's complete partition counts and target capacity before any replace/append;
it must retain the package if a spreadsheet cannot accept all rows. Looker Studio's
connector refresh time is separate from the `finished_at` and provider collection time
recorded in the manifest. BigQuery and external Google writes are outside this command.

Output is staged and published only after bounds and row conservation pass; existing
output is refused. Scan access is read-only. A missing saved audit makes findings
unavailable, rather than a clean zero-findings audit. Valid audit.v2 companions are
read with an explicit 64 MiB compatibility bound so their findings cannot silently
vanish behind the inline slot. The scan input remains limited to 120 MiB and 50,000
pages; providers and total output have separate hard budgets. This implementation
materializes bounded pages/findings/provider observations; it does not demonstrate
streaming million-page BI, Google Sheets publication or a copyable Looker Studio template.

## Publication cohorts

`publication-cohorts` consumes `seohead.publication-cohort-input.v1` and
saved `seohead.normalized-evidence.v1` rows. It writes local inventory,
multi-author, provider-observation, publication-month and optional age-window
datasets with a manifest. Publication, modification and first-observed dates
retain separate raw value, parsed value, state and source; none substitutes for
another. GSC and analytics remain separate labelled sources with their period,
timezone, attribution and coverage. Missing or suppressed evidence is not zero,
and URL-key collisions remain unjoined. Publication timing is descriptive, not
evidence that publication caused traffic.

## Branded and non-branded GSC progress

`gsc-progress` consumes `seohead.gsc-progress-input.v1` with explicit exact or
token aliases, comparable windows and saved normalized GSC query rows. Unicode
NFKC/case-folding is applied before matching; zero, one and multiple rule
matches remain non-branded, branded and ambiguous states, while a missing query
is unknown. Optional operator overrides remain recorded in contributions.
Weighted CTR uses compatible measured clicks and impressions, never row CTR
averages; zero-baseline changes are `new_from_zero`, not infinite. Scope is
kept separate, and average-position bands are not rank or page-placement
claims. Neither command collects GSC or makes a causal SEO diagnosis.
