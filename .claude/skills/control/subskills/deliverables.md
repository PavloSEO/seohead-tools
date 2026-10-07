# Deliverables: what separates an analysis from a result

A finding is not a deliverable. "Your images are heavy" is an opinion; an archive of re-encoded
files with the saving measured per file is a job that is already done.

## The chain that shows it

```bash
seohead images-download --urls "<comma list>" --output-dir ./original
seohead images-optimize --files ./original --output-dir ./optimized \
        --max-width 1920 --quality 82 --format keep
tar -czf images-optimized.tgz ./optimized
```

Real numbers from one run: 10 files, 7.92 MB → 2.58 MB, −67%. The archive is the deliverable.
It does not fix a server with no compression configured — but it proves the server has none,
with the bytes to show for it.

`docs/scenarios/` holds 60 workflows in this shape, each with its commands, its output, its cost
and its limits.

## Choosing the format

| Format | Audience | Command |
|---|---|---|
| `docx` | the client — prose, severity, meaning | `report-build --format docx` |
| `xlsx` | audit findings/evidence for the SEO engineer | `report-build --format xlsx` |
| `csv` | findings, pages and scope tables; not grouped tasks | `report-build --format csv` |
| `md` | a repository, or another agent | `report-build --format md` |
| `audit.json` | the machine-readable original | written by the crawl |

These outputs derive from one saved audit. Keep its identity in the handoff; analyst-authored
recommendations must remain distinguishable from the measured facts.

For developer Excel, generate the audit workbook and `sf tasks` backlog separately, then use
the agent's spreadsheet authoring capability to turn that backlog into the requested engineering
workbook. Include proposed changes, reproducible evidence, full affected-population references
and reviewed acceptance/recheck criteria. See [the complete handoff](../../../../docs/scenarios/deliverable.md).
Only cite source-code paths/lines when the target repository is actually available.

## What belongs in every deliverable

- The config file the crawl used. Without it the numbers cannot be reproduced or compared.
- The coverage sentence next to any score.
- What was skipped, and why.
- The limits, stated rather than implied. → [reference/limits](../reference/limits.md)

## Priorities and the next iteration

Severity describes a technical finding. Work priority uses the agreed project goals and explicit
evidence: retain the rationale, use the supported task pipeline/project priority policy, and
label default effort estimates as suggestions. Missing business data is not permission to invent it.
After review, refine the crawl/extraction configuration or report view for the next run, while
preserving the original evidence and its scope.
