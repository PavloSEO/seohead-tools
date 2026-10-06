- Add explicit disk-backed saved-audit comparison with complete NDJSON results,
  deterministic ordering, source identities, per-file checksums and before/after
  finding conservation. Large audit.v2 comparisons require a new output directory;
  duplicate identities and URL correspondence collisions fail rather than losing rows.
- Keep targeted audit.v2 verification bounded when duplicate rows are present,
  retain validated scan identity in selected documents and refuse conflicting
  identities or invalid after crawls as proof of remediation.
