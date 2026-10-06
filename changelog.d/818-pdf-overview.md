- Add explicit `pdf_policy="overview-v1"` for retained audit.v2 PDF requests.
  The first page distinguishes full source, displayed and omitted counts; deterministic
  display prefixes retain full coverage states/reasons and link to complete streaming
  audit JSON and findings/pages/scope CSV companions. A hash/count manifest records
  provenance and resource limits. Existing destinations are refused, exports are staged,
  and the PDF completion marker is published only after all mandatory companions validate.
  Default PDF behavior is unchanged; the overview is never silently selected or described
  as an exhaustive PDF. See `docs/PDF_OVERVIEW.md` for exact bounds and crash semantics.
