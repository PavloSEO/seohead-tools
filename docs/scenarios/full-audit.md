# Full audit: from authorized scope to a verified report

## The question

“Audit this site for the agreed scope. Give the developers an Excel of tasks, all needed
exports and evidence links, proposed changes, and acceptance/recheck criteria.”

## The chain

Retrieve the versioned workflow and its default router. These catalogue calls only return guidance;
they do not crawl the target.

```bash
seohead skill-show --name workflow/full-audit-v1
seohead skill-show --name workflow/control
seohead scenario-show --name full-audit
```

Confirm the target, read-only authorization, URL/template scope, and finite crawl budget. Inspect
available exports and retained scans before collecting. For the general case, run one native crawl
and reuse its audit and page evidence:

Before the first collection or long-running analysis, offer the specialist the optional second-screen
observer once for the current session and project. Start it only after an explicit yes; an accepted
or declined answer is not asked again for the same project in that session. The observer reads local project state and never starts a scan; saving a note or proposed goal
is an explicit local write:

```bash
seohead watch --project ./project-directory
```

It requires an existing explicit project workspace. Do not create a project, launch a shell, or
start a watcher merely because the offer was shown. MCP callers use `seo_project_observe` for the
same read-only snapshot after the choice; configured unread notes arrive only on a later
project-bound tool call and never enter the agent chat automatically. Already granted observation
authorization is enough; a declined or unanswered optional offer does not block authorized work.

```bash
seohead crawl-site --url https://example.com --config ./crawl.json --out-dir ./run
seohead log-scan --run ./run
seohead report-build --audit ./run/audit.json --format md --out ./report.md
```

The `crawl-site` command is the native collector. If the user supplied Screaming Frog exports and
requested that input path, analyze them instead:

```bash
seohead sf run --exports-dir ./exports --out ./run --tasks
seohead log-scan --run ./run
seohead report-build --audit ./run/audit.json --format md --out ./sf-report.md
```

Use `workflow/seo-deep-audit` only when the licensed live-SF or supplied-export path is the selected
collector. Load specialist methods for applicable questions and point each one at the same saved
evidence. Verify critical findings before delivery or label them unverified. Report the audit's
actual coverage and every skipped, failed, or unavailable measurement.

For the developer request, continue with [the engineering handoff](deliverable.md), using
`sf tasks` on the saved native/SF audit and `report-build` for its factual workbook. The agent
needs a spreadsheet-authoring capability to make the final task workbook and must review its
proposed fixes and acceptance criteria. Source-code references require access to the target code;
otherwise retain direct evidence links and name the access gap.

`project-prepare` can initialize a bounded local workspace, but it does not run the full method set
or establish full-audit completion. Keep its preparation status separate from the audit report.

## What comes out

The selected collection path produces an `audit.json`; `report-build` writes the requested report,
and SF mode can also produce `tasks.json`/`tasks.md`. The report states scope, evidence source, run
completion, check/URL coverage, verified findings, skipped work, and limits. If a required part of
the agreed scope is unavailable, call the result partial and name what is needed to finish it.

## What it costs

The catalogue lookups are local and read-only. A native or licensed SF crawl makes requests to the
approved target within its configured budget. Reading supplied exports is offline. Specialist
checks may make additional requests; do not repeat the site crawl for each one, and do not use paid
providers without explicit authorization.

## What it cannot answer

This workflow does not prove that every possible registry check ran or infer clean results from
missing evidence. [Project coverage](../PROJECTS.md#checklist-coverage) uses explicit agreed tasks
and URL populations, applicability reviews and stale-evidence rules; its named completion axes
are not site-health or remediation percentages. The workflow does not automatically publish fixes,
measure analytics properties without access, or turn a prepared project into a completed audit.

## Covers

This is an orchestration workflow. It reuses existing collectors and methods and adds no technical
check or finding category of its own.
