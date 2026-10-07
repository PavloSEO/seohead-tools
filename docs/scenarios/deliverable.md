# Scenario 56 — From audit to deliverable: the last mile

## The question

> Give the developers an Excel workbook of all tasks, the supporting exports and evidence links,
> proposed implementation changes, and acceptance/recheck criteria.

This is the step most tooling skips, and it is the one that decides whether any of the analysis
turns into work.

## Covers

Nothing in the published catalogue. This is an operating scenario: what to do with
findings once you have them.

## The chain

**1. Select the existing audit and its scope.**

Use the native/SF audit or supported retained scan that answers the agreed question. If collection
is still needed, route through `control` and authorize its scope/configuration first. This is the
explicit directory-route example; a native SQLite scan can instead feed `report-build` and
`sf tasks` directly:

```bash
seohead crawl-site --url https://example.com --out-dir ./run
```

**2. Verify it before it leaves the building.**

```bash
seohead log-scan --run ./run
```

Exit 2 means the run's own numbers disagree with each other. Nothing that fails this should
reach a client — every defect this toolkit has had on live sites reached a report first.

**3. Build the document the reader actually wants.**

```bash
seohead report-build --audit ./run/audit.json --format docx --out ./audit.docx
seohead report-build --audit ./run/audit.json --format xlsx --out ./audit.xlsx
seohead report-build --audit ./run/audit.json --format csv --out ./audit.csv
```

Three audiences, one source document:

| Format | For |
|---|---|
| `docx` | the client — an executive summary (counts, unavailable checks) followed by evidence, not a generated conclusion or recommendation |
| `xlsx` | the factual audit workbook — findings, pages and evidence/scope sheets |
| `csv` | separate finding, scope-evidence, and page tables — a flat export, not a grouped backlog |
| `md` | the repository, or another agent |

`report-build` only renders an audit document (`findings`+`pages`, or the SF `issues`+`pages`
schema); it does not group, prioritize, or import anywhere by itself.

**4. Build the prioritized backlog from the same audit.**

```bash
seohead sf tasks --json ./run/audit.json --out ./report --config config.json
```

Native and SF audits both work here; a native audit does not need an SF licence.
`tasks.json`/`tasks.md` are grouped work items with their own schema. `report-build` rejects `tasks.json` (`audit document schema not recognized`):
the two are separate contracts, and CSV rows are findings, not tasks.

When the specialist has mapped URLs to a shared render template or component, `sf tasks` can use
`"group_by": "check_assignment"` in `tasks_pipeline`. This is an offline data declaration, not
a detector or code-execution language:

```json
{
  "tasks_pipeline": {
    "group_by": "check_assignment",
    "max_urls_per_task": 25,
    "assignments": [{
      "name": "Article template",
      "kind": "template",
      "urls": ["https://example.com/articles/one", "https://example.com/articles/two"],
      "rationale": "Operator mapped the shared article render path.",
      "state": "declared"
    }]
  }
}
```

The resulting `H1_MISSING + Article template` item retains its occurrence count, unique-page
count, capped representative URLs, saved evidence references and a retrieval note back to the
source audit. It is a **declared** common-cause candidate, not proof that one edit fixes every
page. Use `state: "confirmed"` only with `confirmed_by` and a recorded `confirmation`; otherwise
verify one representative and then every affected URL. With no operator mapping, existing
validated crawl segment rules form **candidate** groups. Unmatched URLs remain an explicit
**unassigned** group; partial crawl and suppression/coverage statements remain unchanged.

**5. Author and review the developer task workbook.** This is the agent's engineering step,
using an available spreadsheet tool or the installed report library. `report-build` does not accept
`tasks.json` and does not generate acceptance criteria. Preserve its factual audit workbook and
write a separate task workbook from the machine backlog, with these columns:

| Column | Basis |
|---|---|
| Task ID, finding/check identity, severity, affected counts | Copy from the saved backlog/audit; keep finding, occurrence and unique-page counts separate |
| Work priority, grouping and dependencies | Saved task configuration or explicit analyst decision with rationale; do not disguise priority as severity |
| Reproduction and evidence link | Retained affected URL/status/HTML/header/link locator and source artifact; explicitly unavailable when absent |
| Proposed implementation and source reference | Analyst-authored fix proposal; link file/line only after inspecting the actual target repository |
| Acceptance and recheck | Analyst-reviewed expected result, affected population, method/command, necessary input and retained output |
| Review/access state | Who reviewed the proposal, unresolved ambiguity, missing evidence or code access; no invented signoff |

For example, a missing-title task can require a non-empty agreed title on every URL in its affected
population and a compatible recheck showing `TITLE_MISSING` no longer firing for that population.
That proves the named repair only; it does not prove ranking gains or resolve other cases.
A broken-link task must recheck the recorded source/destination/representation, not just fetch a
replacement URL once. Use the [remediation contract](../LEDGER.md) when recording case resolution.

The agent may refine `tasks_pipeline` grouping/priority maps, recorded project priorities or report
views to fit the recipient. State those choices and keep the original audit. If spreadsheet authoring
is unavailable, the developer workbook is still missing; the audit workbook is not an equivalent
reviewed implementation specification.

**6. Hand over complete, usable sources.** A capped task URL list is a sample: include the source
audit and a retrievable full affected-population export, including excluded/unavailable counts.
Keep hyperlinks relative to the delivered bundle where possible and open the workbook to verify
rows, filters, wrapping, links and all requested columns. Reconcile task counts with `tasks.json`
and leave criteria marked for review until reviewed. Do not overwrite source evidence.

When an output includes transformed assets, attach the assets and their measured differences too;
see [the images scenario](images.md).

## What comes out

```
audit.docx        the narrative, for a person who will not open JSON — summary and evidence, no conclusion
audit.xlsx        the working file, filterable by severity and section
audit.csv         findings — needs recipient-side field mapping to become tracker rows
audit.pages.csv   page facts
audit.scope.csv   crawl validity, scope, unavailable/disabled checks, and finding-exclusion rules and reasons
tasks.json/.md    grouped prioritized backlog from the same audit
developer-tasks.xlsx  agent-authored work specification with reviewed criteria, linked to tasks.json
run/audit.json    the machine-readable original, which the formats above cannot contradict
```

Measured fields derive from the retained audit. Proposed changes and acceptance criteria remain
labelled engineering judgments and carry their review state. The handoff includes configuration,
source identities, population/coverage limits and a specific next action for unfinished work.

## What it costs

Formatting existing evidence is local. New collection or live rechecks have their separately
authorized request budgets. `docx` and `xlsx` need the optional report extras installed; authoring
and verifying the engineering workbook also requires the agent's spreadsheet capability.

## What it cannot answer

- **What to do first.** Severity is not priority: a critical finding on a page nobody visits
  ranks below a warning on the money page. That ordering needs traffic data and a person.
- **How long anything will take.** Effort estimates in the backlog are shapes, not commitments.
- **Whether the client will act.** The measurable part ends at the handover.
