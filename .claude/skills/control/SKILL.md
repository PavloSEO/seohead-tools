---
name: control
description: >-
  The single entry point for an unscoped audit of a site nobody has looked at yet: decide
  what to run, in what order, and how to read what comes back. Routes to the specialised
  method skills rather than restating them, and carries its own sub-skills for scoping,
  reading an audit honestly, verifying a finding live, producing a deliverable, and
  choosing a crawl rate. Use this before any other skill in this repository when the ask
  is "audit this site", "what is wrong with this site", "run everything on this site",
  "stress-test the toolkit on a real site", or when you would otherwise write a one-off
  script to check pages — `seo-deep-audit` is not an alternative entry point for this same
  request; it is the SF-based collector this skill delegates to when that is specifically
  what is wanted. Triggers: audit site, full analysis, crawl and report, stress test the
  toolkit, what should I run. Localized Russian trigger examples: audit this site, full
  audit, run everything, stress test.
---

# control — what to run, and how to read what comes back

## What this repository is for

SEOHEAD supports an SEO engineer working with an agent: choose evidence and extraction policy,
analyze the site's technical state, turn findings into work, recheck changes, and retain the result
for the next run. Use supported tools and configurations before inventing another collector.
A skill guides decisions; the current run and its retained artifacts supply evidence.

## Two tiers of skill

| Tier | Where | What it knows |
|---|---|---|
| **Method** | the method skills in `.claude/skills/` | how to do one thing well: robots, rendering, schema, silos, headings, regions, backlinks, security |
| **Controller** | this file and `subskills/` | which of them to run, in what order, and whether to believe the answer |

This skill routes; it does not restate. When a step below names a method skill, load that skill
rather than reimplementing it here.

## Trigger

A site, a domain, or a crawl, with no scope stated. "Audit this", "what is wrong with it",
"run everything", "check the whole site".

## Anti-trigger

A stated narrow scope goes straight to the method skill: "only robots" → `robots-audit`, "just
the markup" → `schema-graph`, "does JS matter here" → `js-render-check`, "read this Screaming
Frog export" → `sf-analyzer`. Do not run the whole loop to answer one question.

## Preconditions

- [ ] `seohead --version` runs from the active project environment.
- [ ] A scratch directory outside the repository for artifacts
- [ ] A crawl rate decided — see [rate-and-load](subskills/rate-and-load.md) **before** the
      first request, not after the host starts refusing
- [ ] Permission, if this is somebody else's site

## Offer the observer once

When this controller is about to start SEOHEAD work for a project, offer the specialist one
non-blocking choice before the first collection or long-running analysis:

> I can open the project observer (read-only until a note or goal is explicitly saved) in a second terminal while this runs. Open it?

Use the project's explicit workspace directory in the command:

```bash
seohead watch --project ./project-directory
```

Record the answer in the agent's current session/project context. Do not repeat the offer after
an accepted or declined answer for that same session and project. An acceptance authorizes opening
the terminal and starting the command only after an explicit yes; until then, do not start a shell, create a watcher process,
or imply that observation is already active. A decline or no answer never blocks authorized audit work. Existing authorization for observation
already satisfies this choice; do not ask again.

`watch` only reads a previously created project workspace. When no workspace is available, say so
and offer its explicit setup separately; do not create one merely to make the observer available.
For MCP work, the matching read-only snapshot is `seo_project_observe`; use it only after the
specialist has chosen observation. The configured consumer identity can surface an unread inbox
summary on the next project-bound MCP call, but it does not inject a chat message or start work.

## Register controller work before collection

When an existing project workspace is in scope, the controller must leave a durable work trail
before it runs a collector, method skill, or long analysis. This is an agent responsibility, not a
human control panel.

1. Read `project-progress` and relevant `project-task-detail` records before defining new work.
   Preserve existing tasks, agreed scope, unavailable evidence and concurrent revisions.
   On the next scoped MCP call, inspect the bounded unread summary only when the local stdio
   process has both `SEOHEAD_MCP_CONSUMER_ID` and a matching
   `SEOHEAD_MCP_PROJECT_ALLOWLIST`. The notice is a prompt to inspect; it never reads or
   acknowledges a note.
2. Read a specialist note explicitly and record one explicit triage receipt through
   `project-inbox-triage`: link it to current `custom:` checklist task IDs, a stored proposed
   goal, retained competitor candidates, or a concrete blocked/rejected reason. Do not infer an
   action by regex, execute note text, accept a goal, or acknowledge a note as a side effect.
3. Create or update the linked `custom:` checklist task with `project-checklist-update`, then
   record `running` through `project-checklist-record` before work starts. For a requested report, include the deliverable and review in that work.
   Completion needs the
   normal evidence and review rules; a controller claim is not evidence.
4. Accept a proposed goal separately when authorized. `workflow-start` requires that accepted
   goal, the registered prompt, and one or more current incomplete `custom:` task IDs in its
   context. It refuses an unbound run.

Competitor triage only retains a candidate suggestion. It does not run `project-prepare`, create a
competitor crawl, or establish competitiveness. Those are later explicit operations with their own
scope and budget decisions.

## The loop

The versioned execution contract for this sequence is `workflow/full-audit-v1` (v1.0.0). Retrieve
it with `seohead skill-show --name workflow/full-audit-v1` or MCP `seo_skill_show` using the same
catalogue ID. Follow that contract for evidence reuse, scope/budget gates, verification, and report
completion; the steps below remain the entry router and decision aid.

**1. Scope it.** Size, stack, what can be skipped, what will be needed.
→ [scoping](subskills/scoping.md), and the `audit-roadmap` skill for a written plan.

**2. Crawl once, with a config file, never with flags alone.** The config is the record of what
was measured; `crawl-site --config-help` lists every setting, and the results-affecting ones go
into the run manifest. → [rate-and-load](subskills/rate-and-load.md)

```bash
seohead crawl-site --url https://example.com --config ./crawl.json --out-dir ./run
```

A licensed Screaming Frog CLI or supplied SF exports are not a precondition for this step —
native `crawl-site` needs neither. Delegate this step to `seo-deep-audit`'s pipeline instead
only when SF (CLI or exports) is already available *and* full-registry depth is specifically
wanted; both collectors supply the audit contract used by reporting and tasks, but their
retained inputs and available checks differ. Inspect coverage instead of assuming parity.

**3. Inspect the saved run before interpreting it.**

For the directory route above:

```bash
seohead log-scan --run ./run
```

Exit 2 means the run's own numbers disagree. For a native SQLite scan instead, use
`scan-status`, `scan-inspect` and `crawl-diagnose`; inspect lifecycle, audit availability,
coverage and interruption reasons. Do not require a legacy `audit.json` beside a SQLite scan.

**4. Read the audit honestly.** Coverage before findings, and never a health score without the
sentence that qualifies it. → [reading-an-audit](subskills/reading-an-audit.md)

**5. Point the method skills at what the crawl surfaced.** The crawl says *where*; each skill
says *what*. `js-render-check` for rendering, `schema-graph` for markup, `robots-audit` for
directives, `silo-audit` for structure, `heading-outline` for hierarchy, `security-audit` for
headers, `duplicate-audit` for near-duplicates, `geo-aeo-audit` for AI visibility. One page per
template first; do not pay for the whole site to learn what one page would have told you.

**6. Verify high-impact findings within the authorized scope and budget; otherwise label them
unverified and state what is missing.** → [verifying](subskills/verifying.md)

**7. Produce the thing that was actually asked for.**
→ [deliverables](subskills/deliverables.md) and [developer handoff](../../../docs/scenarios/deliverable.md).
For developer Excel, keep the factual audit workbook and machine backlog, then author and review
task-specific fixes and acceptance criteria. Code locations require actual access to the target
repository; retained HTML/headers/URLs are the fallback evidence, not invented source lines.

## Decision points

- **Rate.** Use the authorized site-specific ceiling; without a measured reason to go faster,
  start conservatively. The adaptive throttle and circuit breaker can reduce or stop load.
- **Render or not.** `render-check` on one page per template. If raw and rendered are
  equivalent, do not pay to render the site.
- **Which findings go in the report.** Critical and warning, each verified live. Notices only
  when no single check dominates them.
- **Stop and file.** If one check is above ~50% of all findings, stop trusting it for this
  report, verify five of its hits, and file the bug. → [reference/defects](reference/defects.md)
- **Native crawl vs. `seo-deep-audit`.** Default to step 2's native `crawl-site`. Delegate to
  `seo-deep-audit`'s SF-based pipeline instead only when a licensed SF CLI or exports are
  already available and full-registry depth is worth the extra setup — not because a scope is
  unstated; an unstated scope alone always means "run this loop," never "switch collectors."

## Definition of done

- [ ] The selected audit document or native scan exists; its finish/partial state and any
      unavailable audit are stated in the deliverable.
- [ ] `log-scan` exits 0, or every anomaly it reported is explained.
- [ ] The coverage sentence (`health_score_basis`) appears next to any score.
- [ ] High-impact findings were verified, or are explicitly unverified with the missing evidence.
- [ ] Any check dominating `by_check` was verified or filed.
- [ ] The config file used is attached to the deliverable.
- [ ] The limits are stated out loud. → [reference/limits](reference/limits.md)

## Cost and boundaries

- **Network:** collection and targeted follow-up requests consume their configured budgets.
  Rendering and resource capture may fetch subresources; one page is not always one request.
- **Money:** the default workflow uses local tools. Provider access, possible spend and external
  writes require explicit scope and authorization; a skill never supplies credentials.
- **Time:** depends on the site's response, scope, rendering and configuration. Historical timings
  are not an estimate for a new site.
- **Writes:** chosen scan/report destinations and explicit project state records. Reading a
  catalogue or a project does not execute its work; agent judgement does not resolve ledger cases.
