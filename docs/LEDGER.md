# Remediation ledger (`ledger.v1`)

The remediation ledger is a separate local SQLite artifact for finding cases, their occurrences,
observations, lifecycle decisions and evidence-bound rechecks. It answers "what was found, where,
what was reported fixed, and what was actually verified later?" independently of the project's
audit-task checklist. Source scans remain unchanged.

## Start from the right layer

The current public CLI/MCP exposes `remediation-summary`, `remediation-cases`,
`remediation-transition`, `remediation-recheck`, `remediation-record-verification` and
`remediation-report`. Their exact parameters and network/write effects are in
[TOOL_REFERENCE.md](TOOL_REFERENCE.md). A recheck is an explicit operation: without a supplied after-audit it can fetch the selected
targets; it also writes verification evidence and updates the ledger.

Creating the ledger and ingesting a retained scan currently require the Python core API
`create_ledger` and `ingest_scan` documented below. There is no CLI/MCP bootstrap command to infer
from the read/transition routes. An integrator must create and bind that artifact before using
those routes. The ledger never resolves a case merely because it disappears from a partial scan:
resolution needs exact retained recheck evidence or an eligible later measured observation.

## File identity

| Item | Value |
|---|---|
| Format | `ledger.v1` |
| SQLite signature | `SQLite format 3\000` |
| `application_id` | `1397051212` (`SEOL`; scans use `SEOH`) |
| `user_version` | `4` |

A reader must require all three identifiers. `open_ledger` returns a validated
connection or refuses; it never auto-repairs, never opens a foreign file, and
never silently migrates.

## Project/site binding

`create_ledger(path, project_dir=..., producer_build=...)` creates one new
ledger bound to a validated `seohead.project.v1` project (see
[PROJECTS.md](PROJECTS.md)). The binding records the project's UUID and its
normalized site target/host in the `site` table. A scan binds by exact
`netloc` match against a registered site row — the same site-equality rule the
saved-evidence layer uses — so a scan belonging to another site is refused
atomically before anything is written. A project directory path or a label is
never identity; the project UUID and the normalized netloc are.

## Tables

```text
ledger              singleton header: ledger_uuid, format_version, created_at,
                    writer_version/revision, ledger_revision
site                project_uuid + normalized target/host/netloc + role
source_scan         one row per bound source revision: site_id, scan_uuid,
                    format_version (scan.v1|scan.v2), source_kind, lifecycle,
                    evidence_revision, audit_sha256, canonical_audit_sha256,
                    audit_schema_version,
                    audit_created_at, config_fingerprint, writer + analyzer
                    identity, first-seen container digest, crawl/corpus
                    partial flags, artifact_state, ingested_at
check_def           bounded registry of check identifiers seen
finding             one logical problem at site scope: check + subject
occurrence          one affected case: check + subject + representation +
                    typed discriminator, linked to its finding
affected_url        (finding, url, role) membership rows
finding_observation the run-local audit projection per finding per source
finding_group       group membership per finding per source (history)
observation         append-only sighting per occurrence per source
decision            append-only lifecycle transition history
verification_artifact immutable verification.v1 file identity, selected scope and collection state
verification_result  one exact before/after outcome bound to an occurrence and decision
```

Foreign keys are enforced and checked on every validated open together with a
full schema comparison and `PRAGMA quick_check` / `PRAGMA foreign_key_check`.

## Identity

Two digest keys carry the whole model. Both are SHA-256 over the canonical
JSON serialization `["<kind>","<project_uuid>","<site_host>",...]`:

- `finding_key` (`seohead.ledger-finding.v1`): `project_uuid`, `site_host`,
  `check`, `subject_type`, `subject_value`.
- `occurrence_key` (`seohead.ledger-occurrence.v1`): the finding tuple plus
  `representation` and `discriminator_type`/`discriminator_value`.

`subject_type` is `url` or `scope`. An audit-wide/graph finding uses the
explicit `scope:site` subject — never an empty URL. `subject_value` for a URL
subject is the URL canonicalized with the analyzer's own equality policy
(`seohead.sf.core.normalize.norm_url`: strip, lowercase scheme and host, drop
a trailing `/`; query, fragment and path case are preserved).

`representation` names the corpus lane an observation belongs to:
`static`, `rendered`, `legacy_fragment`, `legacy_unknown`, `unknown`, or
`scope`. An audit issue may declare it explicitly; otherwise it is resolved
from the affected page's recorded representation, then the scan's `pages`
table, then `unknown`.

`discriminator` separates firings of one check on one URL. `primary` is the
finding's own subject. `subject` carries the finding subject so two findings
of the same check landing on the same page stay two cases (for example two
different broken links both reported from `/a`). `locator` carries a digest of
the typed subject and stable link path when one check fires more than once at
the same URL. If repeated locations lack unique stable locators, the finding
stays aggregate-only rather than claiming those occurrences were enumerated.

The key deliberately **excludes**: severity, message, HTTP status, evidence
value or hash, configuration fingerprint, producer build, timestamps, the
run-local `ISSUE-…` ordinal, group membership and the scan UUID. Those
describe an *observation* of the case, never the case itself, so a changed
measurement appends history instead of minting a new identity.

`affected_url` membership is recorded separately from occurrences so finding
count, occurrence count and distinct affected-URL count remain independent.

## Source scan binding

Each ingested source revision is bound by `(scan_uuid, evidence_revision)`
plus its audit SHA-256, canonical legacy-audit SHA-256 when available, audit
schema version, configuration fingerprint and
writer/analyzer identity. Re-ingesting the exact same revision is idempotent:
no new rows, no new observation, no new timestamp, no ledger revision bump.
A new scan UUID or a bumped `evidence_revision` binds a new row and appends
observation history — nothing is overwritten.

If the same claimed `(scan_uuid, evidence_revision)` presents different bytes
— a different audit digest, a different producer or configuration — the
ingest is refused atomically. A pin, checkpoint or copy may legitimately
change container bytes without touching evidence; the first-seen container
digest is recorded as provenance and such a replay must reproduce the stored
content exactly or fail.

`artifact_state` records whether the bound artifact is still present:
`note_source_missing` marks a pruned or moved-away source `missing` with a
reason while keeping every digest and observation resolvable; a successful
re-ingest restores `present`.

## Observations

One `observation` row per `(occurrence, source_scan, issue_ordinal)` with an
append-only `observation_revision` sequence per occurrence. Each row keeps:
the observation's comparability basis (config fingerprint, analyzer
version/revision), its evidence state and reason, the typed evidence
references, a payload digest, the UTC observation time (or `observed_at_state
= 'unknown'`), the ingest timestamp and the run-local audit ordinal as
provenance.

`evidence_state` is `measured`, `imported_projection` (legacy imports), or
`unavailable` — a finding whose underlying capture is missing stays a
recorded finding with its reason; it is never silently treated as clean or
resolved.

`finding_observation.coverage_state` records how much of the audit's
`occurrences_count` the saved document enumerates: `enumerated`, `capped`
(bounded location list), `aggregate_only` (a count or repeated location set
without enough stable event identity), or `unknown`. `enumerated_count` counts
identified occurrence locations, independently of affected-URL membership;
the unenumerated remainder is never synthesized.

## Versioning and migration

`PRAGMA user_version` is the format version. A **write** open is the only
operation allowed to migrate, and it runs inside one transaction: known
versions migrate step by step, validation runs inside the same transaction,
and any failure rolls the file back untouched. A newer or unknown version is
refused on both read and write opens without mutation. A read open never
writes.

`ledger.v0` is the reserved bootstrap stub format an interrupted or
hand-staged creation may leave: the `SEOL` application id, `user_version=0`,
and a `ledger_meta` marker naming the intended ledger UUID, project/site
binding and writer. An explicit write open runs the 0→1 migration, which
materializes the full schema plus the header and primary site row from that
marker. `create_ledger` itself always writes a complete validated
`ledger.v1`. Version 2 adds immutable `verification_artifact` and
`verification_result` bindings; version 3 records whether group membership
was fully retained; version 4 records a canonical audit digest for legacy
audit documents. A write open migrates an older ledger atomically; readers
of an older artifact still refuse rather than changing it. A migrated v2
`scan.v2` source is conservatively marked `unavailable` for group scope,
because v2 did not persist enough information to reconstruct omitted members.
The v3→v4 migration leaves a legacy source's canonical digest empty rather
than reconstructing one without its retained audit; explicitly re-ingesting
that original validated source can fill the digest and restore its canonical
baseline bridge.

`ledger.ledger_revision` counts committed write transactions that changed
ledger content, used for optimistic concurrency. It is
independent of `source_scan.evidence_revision`, which belongs to the source
artifact's evidence.

### Backup and rollback

The artifact is one file: copy it to back up, replace it to roll back.
`create_ledger` stages the file under a temporary name in the same directory,
validates it, fsyncs, and publishes by hard link — a crash never leaves a
half-written ledger at the final path. Migrating and ingesting are
transactional, so a failed write leaves the previous committed state; keep a
backup before a migration if you must be able to return to the exact prior
bytes.

## Lifecycle decisions and remediation coverage

`transition_occurrence` appends an immutable lifecycle decision and uses the
ledger revision as an optimistic-write precondition. Its states are `detected`,
`verified`, `fix_reported`, `recheck_pending`, `resolved`, `persisting`,
`false_positive_reviewed`, `unverifiable` and `regressed`. Invalid transitions
and stale writers are refused. Finding state is a conservative projection of
its occurrence states: a mixed finding never reads as resolved.

Claims and missing data do not resolve a case. A `resolved`, `persisting` or
`regressed` decision requires an exact retained, measured observation after
the baseline; the decision stores that observation id or a typed result from a
saved `verification.v1` artifact. The latter preserves the baseline/selection/
collection hashes, exact before/after scope hashes and the artifact digest. A false-positive review
is retained as its own state and is excluded from the remediation denominator,
never counted as a fix. Targeted recrawl selection and writing a new
verification artifact remain separate workflow work: an omission from a later
partial scan still has no lifecycle effect.

A verification artifact binds its pending case to either the exact stored audit
digest or the stored canonical digest for a legacy audit document, and also to
the original results-affecting crawl-policy fingerprint. A matching scan UUID
is retained as provenance only; it never authorizes a different audit or policy
to resolve an older case.

`remediation_summary` exposes distinct original-case, remediation and recheck
denominators. `represented_resolved_percent` keeps unverifiable represented
cases in its denominator; `represented_rechecked_percent` counts only retained
resolved/persisting/regressed evidence. If group membership is truncated or
unavailable, `scope.state` is `partial` or `unknown` and the ambiguous
full-scope `resolved_percent`/`rechecked_percent` are withheld. It also
partitions the full represented population by retained recheck task id;
unassigned cases remain visible rather than disappearing from a denominator.
`remediation_report`
returns one deterministic, paginated JSON-ready case page with the baseline,
later observations and latest decision while its summary still covers the full
ledger. `write_remediation_report` writes a new, never-overwritten
JSON/Markdown review directory from that page; neither path makes a network
request or mutates the ledger.

## Core API

`seohead.storage.ledger`:

- `create_ledger(path, *, project_dir, producer_build) -> Path` — create one
  new project-bound ledger; never overwrites.
- `open_ledger(path, *, write=False)` — validated read or write connection.
- `ingest_scan(ledger, scan_path) -> dict` — ingest one validated saved audit
  (read-only), streaming `audit.v2` issue rows instead of materializing a
  legacy document; returns binding, `ledger_revision`, per-entity `recorded`
  counts, explicit `group_memberships_state` and `already_recorded`.
- `ledger_summary(ledger) -> dict` — identity, sites and per-table counts
  including `distinct_affected_urls`.
- `read_cases(ledger, *, check=None, url=None, finding_key=None) -> dict` —
  findings with occurrences, membership, group history and ordered
  observations and lifecycle decisions. It returns `total`, `limit`, `offset`
  and the ledger revision; reads paginate by finding (default 100, maximum
  1,000) so an observer can request exact cases without hiding the population.
- `transition_occurrence(ledger, *, occurrence_key, state, actor, reason,
  expected_revision, observation_id=None, decided_at=None) -> dict` — append
  one revision-safe lifecycle decision.
- `record_verification(ledger, verification_path, *, actor, expected_revision,
  occurrence_keys=None, task_id='unassigned') -> dict` — bind a retained
  verification artifact to exact pending cases with typed outcome evidence.
- `remediation_summary(ledger) -> dict` and `remediation_report(ledger, *,
  limit=100, offset=0) -> dict` — explicit full coverage totals and one bounded
  before/after case page without I/O.
- `write_remediation_report(ledger, out_dir, *, limit=100, offset=0) -> dict` — create one immutable
  JSON/Markdown review snapshot from already retained evidence.
- `note_source_missing(ledger, source_scan_id, *, reason)` — mark a bound
  artifact missing without losing its digests.
- `register_site(con, *, project_uuid, target, role)` — register an
  additional site identity on a write connection.
- `canonical_url`, `finding_key`, `occurrence_key` — the canonical
  serialization used for identity, exposed so a recheck reproduces keys.
