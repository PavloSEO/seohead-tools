# Explicit PDF overview for retained audit.v2 inputs

`build_report(..., fmt="pdf", pdf_policy="overview-v1")` enables a bounded PDF
reading view with complete machine companions. The policy applies only to streamed
`audit.v2` inputs. It is never selected automatically: without it, a streamed PDF
request returns a named refusal. Existing materialized-audit PDF behavior is unchanged.
The policy is invalid with any other format or a materialized input.

This is **not an exhaustive PDF listing or a representative sample**. Its first page
states the full source count, displayed count, omitted-from-PDF count and display
limits for findings and pages. Source totals, severity charts, run status, coverage
states and reasons retain their source meaning. Every saved check state/reason is
projected; nested summary metadata and prerequisite descriptors are referenced in
the complete JSON instead of repeated in large PDF table cells.

## Display and resource policy

- Findings and pages each use the ordered source prefix: at most 32 records and
  2 MiB of compact UTF-8 source JSON per collection. Stop before the first record
  exceeding the byte budget; do not skip it and select a later record.
- The projection records the zero-based first ordinal and exclusive end ordinal,
  exact source/displayed/omitted counts, displayed source bytes and both limits.
- Complete coverage and essential display metadata are limited to 4 MiB. Nested
  summary populations, such as a per-depth histogram, are first replaced in the
  display model with references to complete JSON; their size does not become a
  lower crawl limit. Oversized essential metadata produces a named failure, not
  a shortened coverage claim.
- The PDF retains the existing 200-page and 25-MiB validation limits and the local
  Chromium timeout. Large individual records can still exceed renderer limits;
  failure does not publish a successful package or change the retained source.
- Complete companion output has a 64-GiB package budget and preserves a 1-GiB free
  disk reserve, checked during streaming. Memory use depends on metadata, bounded
  display records and one source row, not the number of findings/pages exported.
- Project/saved-view combinations unsupported by the streamed report route remain
  explicit failures; the overview policy does not silently ignore those selectors.

## Output contract

For `out/report.pdf`, the complete result contains:

| Output | Meaning |
|---|---|
| `report.pdf` | Bounded overview; the first page links to every companion |
| `report.files/audit.json` | Complete audit document, including all indexed collections and nested group/member evidence |
| `report.files/findings.csv` | All active findings in the established human CSV contract |
| `report.files/findings.pages.csv` | All source pages in the established CSV contract |
| `report.files/findings.scope.csv` | Run caveats, coverage, exclusions and all suppressed-finding scope rows |
| `report.files/manifest.json` | Versioned provenance, counts, projection, hashes and resource limits |

CSV remains a display projection with the existing formula-injection protection;
`audit.json` preserves the complete original machine records. The manifest names the
source audit.v2 binding/digest and every indexed collection count, then records each
output's SHA-256 and bytes plus CSV row counts. The result also returns the manifest
hash, all output paths, full source `findings`/`pages` counts and the `projection`.
PDF artifact links are relative, portable links to the companion directory, not
links to temporary rendering files or local absolute source paths.

## Publication and failures

The destination PDF and companion directory must both be new. Existing files,
directories and symlinks are refused without modification. All rendering, validation,
complete exports, row-count checks, hashing and manifest creation happen in a private
staging directory. The complete companion directory is renamed first; the PDF is
published last as the completion marker. Runtime publication errors roll back the
new outputs. No successful result is returned for partial companion exports.

A process crash between those two filesystem operations may leave complete companions
without the final PDF completion marker. This is not a successful PDF package and
must not be reported as one; the original retained scan remains unchanged. The code
does not overwrite such a directory on retry. POSIX directory fsync is used; Windows
retains its documented filesystem durability boundary.

The policy makes the PDF consumer usable without materializing a complete large audit.
It does not itself prove one-million-URL producer or end-to-end capacity: those require
the separate staged native-pipeline acceptance gate.

## CLI and local MCP

```bash
seohead report-build --audit scan.sqlite --format pdf --pdf-policy overview-v1 --out report.pdf
```

The matching local tool is `seo_report_build` with `fmt="pdf"` and
`pdf_policy="overview-v1"`. Both call the same report builder. Omitting the policy
never silently selects a bounded PDF view.
