# Synthetic Looker Studio reporting pack

This is an original field-and-layout blueprint for the external Looker Studio consumer of a
`seohead.bi-manifest.v1` package. It contains no customer URL, credential, Google resource or
claim that a Looker report exists. `looker-studio-blueprint.json` is machine-reviewable: it lists
every page, dataset, field, calculated field and filter rule that a native report must implement.
`FIELD_GUIDE.md` records the published grain, keys and report-facing fields for each worksheet.
`layout-preview.svg` is the original visual review of the five blueprint pages; it is not a
substitute for the native template copy check.

## Template state and local copy-link preparation

**Template state: missing.** This repository has no published Looker Studio report ID and no
copyable native report. The local builder refuses a blank/default report route and requires an
operator-supplied original report ID, one explicit source alias for every BI dataset, and either
a Sheets worksheet mapping or a BigQuery table mapping. It does not contact Google, create a
report, alter a data source, or prove the supplied ID is available to the eventual viewer.

```python
from seohead.reports.looker_link import build_looker_copy_link, load_blueprint

blueprint = load_blueprint("examples/reporting-pack/looker-studio-blueprint.json")
copy = build_looker_copy_link(
    blueprint=blueprint,
    original_report_id="<real original five-page report ID>",
    original_report_confirmed=True,  # confirmed by an operator; not checked over the network
    report_name="<new report name>",
    data_sources=[
        {
            "alias": "pages_sheet",
            "dataset": "pages",
            "kind": "sheets",
            "data_source_name": "Pages worksheet",
            "spreadsheet_id": "<approved spreadsheet ID>",
            "worksheet_id": 123,
            "worksheet_title": "pages",
        },
        # Map findings, metrics, link_occurrences, cohorts and coverage exactly once too.
    ],
)
```

The returned URL follows Google's [Linking API](https://developers.google.com/looker-studio/integrate/linking-api):
`c.reportId` is required here, aliases name sources from the original report, Sheets uses a
spreadsheet and worksheet ID, and BigQuery uses a project/dataset/table mapping. `refreshFields`
is pinned false because this pack only accepts its published BI schema; a changed source schema
must be reviewed before a new native template is made. `original_report_confirmed=True` is a
required operator attestation, while the builder still labels the result unverified locally.

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

Before treating the returned URL as a usable copy link, verify the original report ID is a real,
viewable report and complete this exact import checklist:

1. **Run scope and coverage** — `coverage`: `dataset`, `state`, `source_rows`, `exported_rows`,
   `unavailable_rows`, `reason`.
2. **Technical URL status and directives** — `pages` and `cohorts`: `url`, `status_code`,
   `indexability`, `value_label`, `state`, `reason`.
3. **Depth and observed inlinks** — `pages`, `cohorts`, `link_occurrences`: `crawl_depth`,
   `unique_inlinks`, `numerator`, `denominator`, `extraction_coverage_state`.
4. **Content and metadata screening** — `pages` and `findings`: `url`, `title`,
   `meta_description`, `h1`, `word_count`, `check_id`, `severity`.
5. **Optional Search Console and Analytics evidence** — `metrics` and `cohorts`:
   `metric_name`, `value_number`, `period_start`, `period_end`, `timezone`, `search_value`,
   `sessions_value`, `value_label`, `state`.

Then recreate the two calculated fields and all filter rules verbatim from
`looker-studio-blueprint.json`, confirm the global `run_id` control on every dataset, and perform
the fresh-account copy check described above. A missing or inaccessible original report remains
`template_state: missing`; it is not replaced with Google's default report.

Looker connector freshness is not source collection freshness. Display the package run's
`finished_at` and each provider metric's period/timezone/collection state beside visualisations.
