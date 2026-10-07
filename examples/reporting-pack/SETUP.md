# Looker Studio template build and verification guide

This guide turns the committed synthetic fixture into the original five-page Looker
Studio template required by the reporting-pack contract. Report creation is a manual
Looker Studio step: Google provides no report-creation API, and this repository ships
no Apps Script or Looker API dependency. Everything here is point-and-click work by a
person signed in to the Google account that will own the template.

Estimated effort: about 30 minutes. No script is required or permitted to perform these
steps on your behalf.

## Inputs

| Artifact | Path |
|---|---|
| Synthetic worksheets (one CSV per BI dataset) | `examples/reporting-pack/worksheets/*.csv` |
| Package manifest (row counts, schema, run id) | `examples/reporting-pack/worksheets/manifest.json` |
| Page/field contract | `examples/reporting-pack/looker-studio-blueprint.json` |
| Field guide | `examples/reporting-pack/FIELD_GUIDE.md` |
| Reserved data-source aliases | `examples/reporting-pack/linking-api-source-aliases.json` |
| Layout review | `examples/reporting-pack/layout-preview.svg` |

The fixture is regenerated deterministically by
`python scripts/generate_reporting_pack_worksheets.py`; `--check` fails if the committed
worksheets no longer match the toolkit's BI projection. All data is synthetic
(`example.com`, `external.example.com`); no customer URL or credential appears.

## Step 1 — Create the synthetic spreadsheet

1. In Google Sheets create a spreadsheet named
   `SEOHEAD Tools - Synthetic reporting pack v1`.
2. For each dataset, use the CSV path declared in `manifest.json` under
   `datasets.<name>.partitions`. This fixture has one partition per dataset,
   named `<dataset>-0001.csv`. File → Import → Upload → select the CSV →
   Import location "Insert new sheet(s)" → separator "Detect automatically". Rename each
   resulting tab to the dataset name, without the partition suffix: `coverage`, `pages`,
   `findings`, `link_occurrences`, `cohorts`, `metrics`. For a larger package, import every
   declared partition in order into that dataset's worksheet, keeping its header once.
3. Verify each worksheet's data row count against `manifest.json`
   (`datasets.<name>.row_count`). `link_occurrences` legitimately contains only a header
   row: the synthetic audit input declares this dataset `unavailable`, which is part of
   the state demonstration.
4. Do not edit cells. Formula-leading values already carry an apostrophe safety prefix
   (`formula_safe_cells_prefixed` in the manifest); removing it would re-enable CSV
   formula injection for anyone who re-exports the sheet.
5. Keep the spreadsheet restricted, or set "anyone with the link can view" only if the
   owner wants a publicly copyable template — synthetic data makes link-viewer safe, but
   the sharing choice is the owner's.

## Step 2 — Create the report and six data sources

1. Looker Studio → Create → Report.
2. Add a data source → Google Sheets → select the spreadsheet and the `coverage`
   worksheet → keep "Use first row as headers" → connect. Set its display name to
   `SEOHEAD Synthetic — coverage`.
3. Repeat once per worksheet: `pages`, `findings`, `link_occurrences`, `cohorts`,
   `metrics`, with the display names below.
4. In the report editor open **Resource → Manage added data sources**. Edit the
   **Alias** column for each source to the exact value below. Display names and aliases
   are separate properties; renaming a source does not set its Linking API alias.

   | Worksheet | Display name | Linking API alias |
   |---|---|---|
   | `coverage` | `SEOHEAD Synthetic — coverage` | `coverage_sheet` |
   | `pages` | `SEOHEAD Synthetic — pages` | `pages_sheet` |
   | `findings` | `SEOHEAD Synthetic — findings` | `findings_sheet` |
   | `link_occurrences` | `SEOHEAD Synthetic — link occurrences` | `links_sheet` |
   | `cohorts` | `SEOHEAD Synthetic — cohorts` | `cohorts_sheet` |
   | `metrics` | `SEOHEAD Synthetic — metrics` | `metrics_sheet` |

   Confirm each alias appears exactly once and points to its declared worksheet.
   These values are the contract in `linking-api-source-aliases.json`; see Google's
   [data-source alias instructions](https://developers.google.com/looker-studio/integrate/linking-api#data_source_alias).
5. Do not let Looker Studio "fix" field types that change semantics: every `*_state` and
   `*_reason` column stays Text; `*_value`, `*_rows`, `*_count`, `crawl_depth`,
   `word_count`, `inlinks`, `unique_inlinks`, `numerator`, `denominator`, `value_number`,
   `search_value`, `sessions_value` are Number; `period_start`/`period_end` are Date.
   Blank numeric cells are missing values, not zero — never type-override them into a
   default.

## Step 3 — Build the five pages

Keep a text box in the top-left of every page stating the page's source dataset(s), the
selected `run_id` or provider period, and the coverage state it is responsible for
disclosing. Match `layout-preview.svg` for arrangement, using original wording.

### Page 1 — Run scope and coverage (`coverage`)

- Report-level control: drop-down on `run_id` (applies to every page's charts).
- Drop-down on `state` for completeness disclosure.
- Table "Dataset coverage": dimensions `dataset`, `population`, `state`, `reason`;
  metrics `source_rows`, `exported_rows`, `unavailable_rows` (all SUM).
- Bar chart "Rows by state": dimension `state`, metric SUM(`exported_rows`).
- Text: the run's `finished_at` from `manifest.json` (`run.finished_at`) and the note
  "BI export time is not source collection time; provider periods appear on page 5."

### Page 2 — Technical URL status and directives (`pages`, `cohorts`)

- Bar chart: dimension `status_code`, metric Record Count.
- Table: `url`, `status_code`, `status_code_state`, `indexability`,
  `indexability_status`, `indexability_reason`.
- Table filtered `cohort_id = 'observed_status'`: `url`, `value_label`, `membership`,
  `state`.
- Table filtered `cohort_id = 'captured_indexability'`: `url`, `value_label`, `state`,
  `reason`.

### Page 3 — Depth and observed inlinks (`pages`, `cohorts`, `link_occurrences`)

- Bar chart "URLs by retained crawl depth": dimension `crawl_depth`, metric Record Count.
- Table: `url`, `crawl_depth`, `inlinks`, `unique_inlinks`, `unique_inlinks_state`,
  `unique_inlinks_reason`.
- Table filtered `cohort_id = 'observed_unique_inlink_share'`: `url`,
  `observed_inlink_share_label` (calculated field below), `extraction_coverage_state`,
  `state`, `reason`. Inlink shares are shown only as numerator/denominator plus the
  extraction coverage state — never as a bare percentage.
- State card for `link_occurrences`: a coverage table filtered `dataset =
  'link_occurrences'` showing `state` and `reason`. The synthetic pack shows
  `unavailable` because the audit input retains no complete edge inventory; the page must
  show that label rather than an empty chart that reads as "zero links".

### Page 4 — Content and metadata screening (`pages`, `findings`)

- Table: `url`, `title`, `meta_description`, `h1`, `word_count`, `title_state`,
  `meta_description_state`, `h1_state`.
- Bar chart: dimension `severity`, metric Record Count (findings).
- Table: `check_id`, `severity`, `url`, `message`, `occurrences_count`,
  `occurrences_count_state`.

### Page 5 — Search Console and Analytics evidence (`metrics`, `cohorts`)

- Table "Provider metric observations": `provider`, `metric_name`, `url_resolved`,
  `value_number`, `value_state`, `value_reason`, `is_measured_zero` (calculated field),
  `population_state`, `collection_state`, `period_start`, `period_end`, `timezone`.
- Date-range control bound to `period_start` on the `metrics` data source only. It is a
  provider-period control; it does not describe the crawl's `finished_at`.
- Table "Search vs sessions cohort": filter `cohort_id =
  'search_visibility_vs_sessions'`; dimensions `url`, `value_label`, `membership`,
  `state`, `reason`; metrics `search_value`, `sessions_value` with their `*_state`
  columns kept visible.
- Scatter chart for `membership = 'member'` rows: `search_value` on X, `sessions_value`
  on Y. Search clicks/impressions and GA4 sessions remain separate axes — never blend or
  sum them.
- Text: "GSC clicks/impressions are search observations; GA4 sessions are analytics
  observations. Counts are not rates. The connector refresh time shown by Looker Studio
  is not the provider collection time; the declared period and timezone above are the
  source-of-truth window."

## Step 4 — Calculated fields

Add exactly the two blueprint fields, verbatim from `looker-studio-blueprint.json`:

- On the `metrics` source: `is_measured_zero` =
  `CASE WHEN value_state = 'measured' AND value_number = 0 THEN 1 ELSE 0 END`
- On the `cohorts` source: `observed_inlink_share_label` =
  `CONCAT(CAST(numerator AS TEXT), '/', CAST(denominator AS TEXT))` — display it only
  for `cohort_id = 'observed_unique_inlink_share'` rows where `state = 'available'`,
  alongside `extraction_coverage_state`.

## Step 5 — Share with copy permission and record the template link

1. Share the report: "Anyone with the link — Viewer". In the share dialog's advanced
   settings, leave "download, print and copy" allowed for viewers — that is what makes
   the template copyable.
2. The template link is the report URL
   `https://lookerstudio.google.com/reporting/<report-id>`. Generate its configured
   Linking API URL with `seohead.reports.looker_link.build_looker_copy_link`, using
   the confirmed original report ID and all six aliases from step 2 mapped to their
   actual synthetic spreadsheet/worksheet IDs. The local builder validates the mapping;
   only the fresh-copy check below can verify that the native report resolves it.
3. Record the report ID, the spreadsheet ID and the per-worksheet IDs in the local
   destination notes only — never commit them to the repository.

## Step 6 — Fresh-copy verification (required before calling the template done)

In a different account or an incognito session where the original is only *viewable*:

1. Give the copying account access to the synthetic spreadsheet, then open the **generated
   Linking API URL** from step 5. Confirm that all six sources resolve to the intended
   spreadsheet/worksheet IDs without manual reconnection. Use "Edit and share" to save
   the configured copy. A manual copy of the original template does not verify the
   Linking API mapping; if the generated link fails, correct the original aliases or local
   mapping and repeat this check from a fresh session.
2. Confirm all five pages render, the report-level `run_id` control and the page-5 date
   control work, and no chart shows a broken-data-source badge.
3. Confirm the states contract on the copy: `link_occurrences` shows `unavailable`, the
   measured-zero GSC clicks row for `https://example.com/page-a` shows `0` with
   `value_state = measured` (not blank), the suppressed `page-b` impressions cell is
   blank with `value_state = unavailable` — it must not plot as zero — and quadrant rows
   for URLs without a complete pair show `incomplete`, not zero.
4. Record the template link, report ID and this checklist's outcome in the issue, including
   that the generated Linking API URL resolved all six sources without manual repair. Until
   then the deliverable state stays `template_state: missing` even though every local
   artifact below is complete.

## Data freshness contract

The Google Sheets connector refresh (Looker Studio's cache) is independent of when
SEOHEAD collected the crawl or when a provider collected its metrics. Pages must always
surface `run.finished_at` for the crawl and `period_start`/`period_end`/`timezone` for
provider observations; "last refreshed" in Looker Studio says nothing about either.
