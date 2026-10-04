# Synthetic Looker Studio reporting pack

This is an original field-and-layout blueprint for the external Looker Studio consumer of a
`seohead.bi-manifest.v1` package. It contains no customer URL, credential, Google resource or
claim that a Looker report exists. `looker-studio-blueprint.json` is machine-reviewable: it lists
every page, dataset, field, calculated field and filter rule that a native report must implement.
`FIELD_GUIDE.md` records the published grain, keys and report-facing fields for each worksheet.
`layout-preview.svg` is the original visual review of the five blueprint pages; it is not a
substitute for the native template copy check.

## Local review

Create a synthetic BI package from a bundled audit:

```bash
seohead bi-export --audit examples/audit.json --out-dir ./synthetic-bi
```

Inspect `manifest.json` first. It is the schema authority: each worksheet consumes the identically
named dataset (`pages`, `findings`, `metrics`, `link_occurrences`, `cohorts`, `coverage`) and must
keep the declared grain and primary key. CSV formula-leading values have already been neutralized;
never remove the leading apostrophe during import.

## Native Google / Looker completion procedure

This procedure is deliberately manual until a user authorizes a concrete Google target and write.
It is the final external step for issue #836, not an implementation substitute.

1. Create a new Google Sheet in the approved account and import one full CSV partition sequence
   per dataset into a worksheet with the same name. Do not collapse link occurrences or replace
   empty state/reason columns with zeroes.
2. Preflight each worksheet against the package manifest's partition row counts and checksums. If
   Sheets cannot hold every row, retain the local package and stop instead of partially replacing
   the destination.
3. In Looker Studio, create a report from the six worksheets and implement the five pages in the
   blueprint verbatim. Add the global run control and source-specific date controls.
4. Create a fresh copy as a second account/session, reconnect its synthetic Sheets sources, and
   verify every page/control. Record the report URL and this copy check only after that external
   verification succeeds.

Looker connector freshness is not source collection freshness. Display the package run's
`finished_at` and each provider metric's period/timezone/collection state beside visualisations.
