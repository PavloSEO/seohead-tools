# GA4 visited-page reporting slice

Issue #782 selects `ga4/page_views`: content-use evidence at **date x hostName x
pagePathPlusQueryString** grain, with the integer `screenPageViews` metric. This is a
visited-page count including repeated views. It is not a landing-page session metric,
a Search Console click, a unique-user count, or evidence that a technical change caused traffic.
The existing `landing_pages` operation and source archive retain their separate scopes.

## Collection and saved evidence

Use the existing `provider-collect` / `seo_provider_collect` interface with an explicit
property, ISO date range, site origin and private artifact directory:

```bash
seohead provider-collect --provider ga4 --operation page_views \
  --input '{"request":{"property_id":"123456789","start_date":"2026-10-01","end_date":"2026-10-03","site_origin":"https://example.test","max_rows":10000}}' \
  --artifact-dir ./provider-evidence
```

This explicit command collects from the configured property. Reading this guide or
normalizing an existing artifact does not collect anything. Credentials come from the existing
GA4 access-token helper or the existing Google service-account helper using `analytics.readonly`.
The account needs property read access. No new API provider, billing setup, account or credential
file is created. Live calls consume the existing property's API quota and are not part of the
synthetic acceptance run.

The request reuses `properties.runReport`, offset/limit pagination and deterministic dimension
ordering. A run has at most 100,000 returned rows, 32 request attempts including retries,
16 MiB per response and 32 MiB total response bytes. HTTP 429/5xx retries are bounded to three
attempts per page. Permission, exhausted quota and collection failure remain distinct failure
kinds; failed collection is not an empty measured report. Raw provider rows remain in the
restricted local artifact rather than the public tool response.

Each page retains response timezone, quota snapshot, sampling metadata, thresholding and data-loss
flags. Timezone drift across pages is refused. A missing timezone stays unknown. Quota snapshots
are observations and are never added together. Absent URL rows are not measured zero; a returned
zero count stays zero.

A URL is bound only when its reported hostname matches the explicitly supplied site origin and
its path is a valid local absolute path. A foreign hostname, `(not set)` or incompatible path
stays unkeyable. The original hostname/path/date dimensions and URL-binding reason stay in the
saved evidence. The collector never guesses another host's scheme or starts a crawl.

## Offline join and report

The private `provider-*.json` artifact is understood by existing evidence normalization.
Replace the illustrative `provider-example.json` filename below with the path returned
by collection; the example does not create a new provider artifact:

```bash
seohead evidence-normalize --file ./provider-evidence/provider-example.json --out-dir ./normalized
seohead evidence-join --scan ./native.sqlite --evidence ./provider-evidence/provider-example.json --out-dir ./joined
```

Pass the generated join artifact to `bi-export --scan ./native.sqlite --provider-join <join-artifact>
--out-dir ./bi`. The typed `metrics` dataset retains date/hostname/path dimensions and
`screenPageViews` separately; it cannot silently become the sessions axis of a traffic quadrant.
Matched, crawl-only, external-only and unkeyable rows retain their provenance and coverage.
Use the existing local BI filters/reports for inspection; Google Sheets or BigQuery delivery is
an independently confirmed destination action, never implied by this slice.

## Primary API contract

- [GA4 API dimensions and metrics](https://developers.google.com/analytics/devguides/reporting/data/v1/api-schema)
  defines `date`, `hostName`, `pagePathPlusQueryString` and `screenPageViews`.
- [properties.runReport](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/properties/runReport)
  defines pagination, row counts and `returnPropertyQuota`.
- [ResponseMetaData](https://developers.google.com/analytics/devguides/reporting/data/v1/rest/v1beta/ResponseMetaData)
  defines timezone, sampling, thresholding and data-loss evidence.

Offline fixtures cover exact row grain, pagination, bounded retry, permission/quota denial,
empty data, explicit zero, partial data, timezone drift, foreign host and retained scan-to-BI
conservation. Actual account access and live provider behavior are not claimed by those fixtures.
