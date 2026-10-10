- Checklist `record` now accepts a succeeded automatic check whose saved scan is a
  streamed audit.v2 scan; previously it failed with `'NoneType' object is not
  subscriptable`, because the audit lives in the companion rather than the scan's
  legacy table. The accepted record shapes are documented in
  `seohead project checklist-record --help`, the MCP `seo_project_checklist_record`
  schema, and `docs/INPUTS.md`.
