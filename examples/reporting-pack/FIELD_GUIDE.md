# Reporting-pack field and grain guide

The BI `manifest.json` is authoritative for the complete typed field list and nullable state.
This guide fixes the fields consumed by the synthetic Looker blueprint.

| Dataset | Grain and key | Required report fields |
|---|---|---|
| `coverage` | one evidence population; `run_id,dataset,evidence_source_id,population` | `state`, `reason`, `source_rows`, `exported_rows`, `unavailable_rows` |
| `pages` | one retained URL observation; `run_id,url_observation_id` | `status_code`, `indexability`, `crawl_depth`, `unique_inlinks`, metadata fields and their state/reason pairs |
| `findings` | one retained finding/check; `run_id,finding_id` | `check_id`, `severity`, `url`, `message`, `occurrences_count` and their state/reason pairs |
| `link_occurrences` | one retained source-to-target occurrence; `run_id,link_occurrence_id` | source/destination URL, anchor, rel, placement and captured-context states |
| `metrics` | one provider metric at its supplied row/dimension/period grain; `run_id,provider_source_id,metric_observation_id` | provider, metric, numeric value, state/reason, period, timezone, collection and population states |
| `cohorts` | one URL/definition observation; `run_id,cohort_observation_id` | definition, membership, state/reason, inlink numerator/denominator/coverage and separate search/session axes |

Use `state` and `reason` as dimensions/tooltip fields, never a calculated-zero fallback. The
blueprint's only calculated fields are `is_measured_zero` and `observed_inlink_share_label`; both
are defined in `looker-studio-blueprint.json`. A report filter may restrict `run_id`, but it must
not mix runs. A metric date control applies only to that provider's `period_start`, `period_end`
and `timezone`; it is not a crawl-date control.
