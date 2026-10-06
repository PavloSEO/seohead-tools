# Project control: configure, analyze, review and hand off

Use this when an SEO engineer and an agent need repeatable work: define the question, configure
collection and extraction, reuse evidence, prioritize findings, deliver work and retain the next step.
The project records that process; its creation or preparation alone does not complete the audit.

## 1. Read the available playbooks

```bash
seohead skill-list
seohead scenario-show --name provider-evidence
```

Use an unambiguous full identifier when a short name is ambiguous. These calls
only retrieve packaged guidance; they do not run a crawl.

## 2. Create and prepare the workspace

Create a local workspace with the `project-start` command, supplying its
directory and a public target URL. The preparation path initializes the local checklist, applies the default
bounded crawl policy, and writes an initial plan. A failure or partial crawl
remains recorded as such in `preparation.json` and project status.

Supply candidate competitors only when their source is known. They are local
candidate workspaces, not evidence that they rank or compete. Pass them as the
structured `competitors` input to a deliberate `project-prepare` operation on
the created workspace.

## 3. Review policy before widening scope

`project-policy` previews a data-only policy. Applying a changed policy requires
the current `expected_revision`; a crawl above the project thresholds requires
the explicit `approve_large_crawl` input. Record a checklist result only from
supplied evidence, with its own expected revision.

## 4. Configure the measurement, then reuse it

`project-policy` governs the preparation budget. A crawler JSON passed through `crawl-site --config`
controls the actual measurement; `crawl-site --config-help` describes every current key and default.
These are distinct from the analyzer/task pipeline in the repository's `config.example.json`.

For example, save this as `crawl.json` after checking the target's markup and agreed scope:

```json
{
  "limits": {"max_urls": 25, "max_requests": 75, "max_crawl_seconds": 60},
  "speed": {"min_delay_seconds": 1, "concurrency": 1, "adaptive": true},
  "scope": {"exclude_patterns": ["/cart(?:/|$)"]},
  "evidence": {
    "content_area": {"root_selector": "main", "exclude_selectors": [".related"]},
    "extraction_rules": [{
      "id": "primary-heading", "kind": "text", "selector": "main h1",
      "operator": "exists", "value": "", "max_matches": 1
    }]
  }
}
```

The content selectors change the main-content signature, not the URL frontier. The extraction rule
records what the selected representation contains; it is not a custom Python check. Inspect one
representative template's retained output before relying on a site-wide extraction. A missing or
wrong selector must not be described as evidence that the page has no content.

```bash
seohead crawl-site --url https://example.com --config ./crawl.json --scan-out native.sqlite
seohead crawl-diagnose --scan native.sqlite
seohead report-build --audit native.sqlite --format xlsx --out audit.xlsx
```

For a project-bound run, supply `--project` with that workspace. Save the returned artifact identity
and available audit/coverage in the corresponding task record. Reanalysis and `scan-extract` can use
retained evidence offline when their input contracts allow it; they cannot recover bodies that were
never retained. Configuration changes affecting collection require an explicitly scoped new capture.

## 5. Decide, deliver and continue

Use [the developer handoff](deliverable.md) to produce the requested Excel, task backlog and evidence
exports. Save review decisions in the project; keep finding repairs and rechecks in the separate
[remediation ledger](../LEDGER.md). A new agent reads `project-progress`, task detail, workflow status
and artifact references before defining more work. Changed scope, definitions or evidence may make
old work stale; do not relabel it complete.

Refinement is explicit: an engineer/agent may adjust supported collection/extraction settings,
`tasks_pipeline` grouping/priorities, project work priorities, or a saved report finding view. Retain
the old inputs and explain the new question. No observer, skill lookup or checklist read rewrites
configs or runs the next iteration automatically.

## Acceptance

- `project-status` names the retained scan or names the crawl as not run/partial.
- Every competitor has an operator source and stays a candidate until separately measured.
- The policy revision, preparation state, and checklist coverage can be reviewed from the local workspace.

## Covers

Project control coordinates recorded checklist and preparation evidence; it does
not add a separate technical finding to the SF issue catalogue.

## What it cannot answer

It cannot establish competitor performance, provider access, a complete site
audit, or client acceptance without the separately recorded evidence.
