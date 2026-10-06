# SEOHEAD Tools documentation

SEO engineering with AI agents: configurable evidence collection, repeatable technical analysis,
developer handoff and verification. Start with the question; follow the links for exact contracts.

## Choose a reading path

- **SEO specialist working with an agent:** [project loop](scenarios/project-control.md) →
  [full audit](scenarios/full-audit.md) → [developer Excel and evidence handoff](scenarios/deliverable.md).
- **Developer or integrator:** [setup](SETUP.md) → [CLI/MCP conventions](USAGE.md) →
  [input contracts](INPUTS.md) → [exact tool schemas](TOOL_REFERENCE.md) → [architecture](ARCHITECTURE.md).
- **Agent taking over:** `skill-list` → `skill-show` with `workflow/control` for an unscoped audit;
  read [project state](PROJECTS.md), the current scope and evidence before starting work.
  [MCP profiles](MCP_PROFILES.md) determine which schemas are actually callable.

## Capability map

The CLI names below have matching `seo_` MCP names with hyphens changed to underscores, except
for the separately named `sf_*` tools. Exact arguments and side effects live in
[TOOL_REFERENCE.md](TOOL_REFERENCE.md); these rows are a navigation map, not another registry.

| Engineering question | Start with | CLI / MCP route | Evidence and boundary |
|---|---|---|---|
| What should be measured, in what order? | [control skill](../.claude/skills/control/SKILL.md), [project-control scenario](scenarios/project-control.md), [Projects](PROJECTS.md) | `project-*`, `workflow-*` | Agreed tasks/population, policy revisions, retained evidence and review gates; a plan executes nothing |
| What should the crawler fetch and extract? | [configuration inventory](HEADLESS_CAPABILITIES.md), [usage](USAGE.md), [recovery](RECOVERY.md) | `crawl-site`, `crawl-describe-settings`, `crawl-diagnose` | Scope, budgets, selectors and declarative rules; settings are not proof of coverage or scale |
| What can existing SF or third-party exports prove? | [sf-analyzer skill](../.claude/skills/sf-analyzer/SKILL.md), [import contract](THIRD_PARTY_CRAWL_IMPORT.md) | `sf run` / `sf_audit_run`, `crawl-import` | SF files work offline; other exports need their declared manifest; live SF needs its own licence |
| Which technical problems are supported by evidence? | [check registry](CHECKS.md), [technical scenarios](scenarios/README.md), [skill map](SKILLS.md) | `parse`, `robots-check`, `schema-check`, `hreflang-check`, `links-check`, `redirects-check` | URL, header, graph and markup observations; a missing input leaves a named gap |
| What changes after JavaScript or navigation? | [js-render-check skill](../.claude/skills/js-render-check/SKILL.md), [browser navigation](BROWSER_NAVIGATION.md) | `render-check`, `scan-navigation`, `scan-rendered-routes` | Saved representations and browser lab evidence; optional browser dependency, no field-CWV claim |
| What do content, forms and templates contain? | [content scenario](scenarios/content.md), [marketing inventory](scenarios/marketing-inventory.md), [saved evidence](scenarios/saved-evidence.md) | `scan-extract`, `duplicate-check`, `boilerplate-report`, `marketing-inventory`, `facts-export` | Retained bodies, occurrences and source locators; missing bodies are not empty content |
| How did a release change the site? | [comparison scenario](scenarios/comparison.md), [comparison contract](COMPARE.md) | `compare-crawls`, `segment-diff`, `scan-body-diff` | Compatible scope/configuration and explicit URL correspondence; a missing page is not automatically fixed |
| Which findings were repaired and rechecked? | [remediation ledger](LEDGER.md) | `remediation-*`, `verify-fixes` | Separate finding/occurrence history, exact recheck evidence and denominators; ledger bootstrap currently uses the Python core API |
| What work can the next agent continue? | [Projects](PROJECTS.md), [terminal observer](TERMINAL.md) | `project-progress`, `project-task-detail`, `project-inbox-*`, `workflow-status` | Task, note, accepted-goal and execution states are separate; reads do not accept or run suggestions |
| What does infrastructure or an access log show? | [recon/security skills](SKILLS.md#recon-and-technical-hygiene), [infrastructure scenario](scenarios/infrastructure.md) | `domain-profile`, `cdn-check`, `security-check`, `log-analyze` | Explicit live requests or supplied logs; service-path probes and bot DNS checks are opt-in |
| How do demand, SERPs and analytics join the audit? | [provider workflow](scenarios/provider-evidence.md), [provider matrix](PROVIDERS.md), [analytics-console skill](../.claude/skills/analytics-console-review/SKILL.md) | `provider-*`, `evidence-join`, `gsc-*`, `topvisor-read`, keyword/SERP tools | Auth, quota, privacy, grain and freshness remain source-specific; a registered connector is not live account access |
| What can be delivered to developers or reporting systems? | [developer handoff](scenarios/deliverable.md), [BI contract](BI.md), [report fixtures](../examples/reports/README.md) | `sf tasks` / `sf_audit_tasks`, `report-build`, `bi-*`, `publication-cohorts`, `gsc-progress` | Audit rows, task backlog and reviewed engineering criteria are distinct; a local BI package is not a verified copyable Looker report |
| How can repeat runs be operated? | [monitor collection](PROJECTS.md#explicit-monitor-collection), [remote API](REMOTE_API.md), [durable jobs](REMOTE_JOBS.md) | `monitor-*`; authenticated adapter/job interfaces | One-shot monitor claims and explicit workers; scheduling metadata alone starts no daemon |

For completeness, use [TOOLS.md](TOOLS.md) for all registered commands,
[INPUTS.md](INPUTS.md) for all inputs, and the [generated setting/workflow inventory](HEADLESS_CAPABILITIES.json)
for source/test references. [COMPARISON.md](COMPARISON.md) explains how to compare supported workflows.

## Where to start

| You are… | Read |
|---|---|
| Setting the toolkit up from zero | [SETUP.md](SETUP.md) — versions, deps, first run |
| Installing on a headless Linux VPS over SSH | [LINUX_VPS.md](LINUX_VPS.md) — pinned install, browser dependencies, upgrades, rollback, and measured CI smoke |
| Looking for a copy-paste command | [USAGE.md](USAGE.md) — runnable examples |
| Checking Docker or remote-service boundaries | [CONTAINERS.md](CONTAINERS.md) — current CLI image and explicit operator-owned service boundary |
| Verifying the remote-service operator profile | [DISPOSABLE_SERVICE_PROFILE.md](DISPOSABLE_SERVICE_PROFILE.md) — owned loopback TLS proxy, artifact recovery, and rollback proof |
| Checking which source inputs a command accepts | [INPUTS.md](INPUTS.md) — generated command-input catalogue |
| Managing or inspecting saved scans | [STORAGE.md](STORAGE.md) — SQLite import, provenance, retained bodies, snapshots, and reviewed retention |
| Importing a third-party crawl export | [THIRD_PARTY_CRAWL_IMPORT.md](THIRD_PARTY_CRAWL_IMPORT.md) — versioned CSV manifest, field coverage, and limits |
| Tracking findings and their history across scans | [LEDGER.md](LEDGER.md) — the `ledger.v1` remediation ledger: identity, observations, coverage, migration |
| Operating a SQLite scan baseline or reviewing capacity evidence | [SQLITE_ACCEPTANCE.md](SQLITE_ACCEPTANCE.md) — capture-to-prune workflow, evidence limits, and the measured release-profile record with its two named limits |
| New to the toolkit | [GUIDELINE.md](GUIDELINE.md) — what it is, the first run, reading an audit honestly, the usual mistakes |
| A native crawl stopped early | [RECOVERY.md](RECOVERY.md) — the two checkpoints, `--resume`, resume vs. intentional fresh start |
| Wondering what this can do end to end | [scenarios/](scenarios/README.md) — workflows, outputs, costs and limits |
| Looking for a tool | [TOOLS.md](TOOLS.md) — inventory, network use, side effects and limits |
| Looking for a tool's exact arguments, types, defaults, or cost | [TOOL_REFERENCE.md](TOOL_REFERENCE.md) — generated from the MCP tool definitions |
| Checking which provider backs a workflow, and what it costs | [PROVIDERS.md](PROVIDERS.md) — generated capability and workflow matrix |
| Looking for a check the SF audit runs | [CHECKS.md](CHECKS.md) — current check registry, generated from source |
| Wondering how this compares to a licensed crawler | [COVERAGE_SF_ISSUES.md](COVERAGE_SF_ISSUES.md) — published coverage claims and status |
| Looking for a method, not a command | [SKILLS.md](SKILLS.md) — workflow and method-skill map |
| Looking for a no-key workflow | [RECIPES.md](RECIPES.md) — exports, traffic decline, bounded live audit |
| About to change code | [ARCHITECTURE.md](ARCHITECTURE.md) — layers and invariants |
| Naming a new module or test file | [NAMING.md](NAMING.md) — what a name must say, and what is deliberately left alone |
| Running or writing tests | [TESTING.md](TESTING.md) — how to run, what they cover |
| Trying to avoid known traps | [GOTCHAS.md](GOTCHAS.md) — money, quotas, footguns |
| Arguing with a past decision | [DECISIONS.md](DECISIONS.md) — why it was done that way |
| Checking what our guidance was aligned against | [GOOGLE_GUIDANCE_REVIEW.md](GOOGLE_GUIDANCE_REVIEW.md) — the 175 Google Search Central guides read on 2026-09-09, their labels, and the three repairs |
| Understanding the product and its role beside Screaming Frog | [COMPARISON.md](COMPARISON.md) — canonical positioning, workflow, and boundaries |
| Reviewing the software for security, legal, or procurement | [SOFTWARE_REVIEW.md](SOFTWARE_REVIEW.md) — generated evidence pack, outbound endpoints, data flow, storage |
| Integrating a guided scan adapter | [GUIDED_SCAN_ADAPTER.md](GUIDED_SCAN_ADAPTER.md) — the generic versioned conversation contract, states, transitions, and invariants |

## What lives here

### Current

- **[TOOLS.md](TOOLS.md)** — what every tool does, which of them touch the
  network, which have side effects, where the boundaries are. Grouped by layer:
  recon, live tools, bounded site audit, own-crawl, external data sources, SF crawl audit.
- **[TOOL_REFERENCE.md](TOOL_REFERENCE.md)** — every tool's arguments (name,
  type, default), its cost (network/writes/idempotent/spend), and its own
  docstring's behavior and failure-mode notes. Generated from the MCP tool
  definitions in `seohead/servers/mcp_server.py` and `sf_mcp.py`
  (`scripts/generate_tool_reference.py`); `tests/test_docs_drift.py` fails the
  build if it drifts.
- **[INPUTS.md](INPUTS.md)** — every command's consumed source inputs, including
  retained scan artifacts, audit documents, inline corpora, provider queries,
  and the distinct operational stores. Generated from
  `seohead/input_contracts.py` (`scripts/generate_input_reference.py`).
- **[CHECKS.md](CHECKS.md)** — the checks the SF crawl audit runs: what each fires
  on, what evidence it needs, and the fix that ships with the finding. Generated
  from `seohead/sf/core/registry.py` (`scripts/generate_checks_reference.py`);
  `tests/test_docs_drift.py` fails the build if it drifts from the registry.
- **[PROVIDERS.md](PROVIDERS.md)** — which external providers are declared, which
  specialist workflows they actually back, what each needs (auth, quota, privacy),
  what is explicitly unsupported, and the phased first release. Generated from
  `seohead/data_sources/providers.py` and the workflow catalogue in
  `seohead/provider_matrix.py` (`scripts/generate_provider_matrix.py`);
  `tests/test_docs_drift.py` fails the build if it drifts.
- **[SETUP.md](SETUP.md)** — install from scratch: Python version, dependency
  groups, venv, optional system tools (SF CLI, `whois`), environment variable
  names (names only, never values), first run checks.
- **[USAGE.md](USAGE.md)** — the CLI/MCP/Docker calling conventions with
  copy-paste commands.
- **[ARCHITECTURE.md](ARCHITECTURE.md)** — the package layout, the main
  invariant ("the core does not know who called it"), the data flow diagram,
  the four registration points of a new tool, test requirements.
- **[NAMING.md](NAMING.md)** — what a module or test file name must say, when a
  basename may legitimately repeat across packages, and what naming decisions
  are deliberately left open.
- **[TESTING.md](TESTING.md)** — how to run the suite, what the tests
  cover, what they deliberately do not, and which missing tests to write first.
- **[GOTCHAS.md](GOTCHAS.md)** — operational traps captured by tests and code
  contracts: API money, quotas, stdin quirks, and explicit mutation flags.
- **[DECISIONS.md](DECISIONS.md)** — decisions with their price: why no GUI,
  why `load` instead of `networkidle`, why the metrics are called `metrics_lab`,
  why the technology fingerprint database is not shipped.
- **[COMPARISON.md](COMPARISON.md)** — how to compare workflow requirements and where verified gaps remain. Collection, interpretation,
  operations and scale are assessed separately; a configuration option is not acceptance evidence.
- **[COVERAGE_GAPS.md](COVERAGE_GAPS.md)** — the map of what the audit still
  lacks, with implemented items marked as done.
- **[CHECKLIST_AUDIT.md](CHECKLIST_AUDIT.md)** — the audit registry checked
  category by category against an external technical-SEO
  checklist, with each claim marked verified or unverified and evidence
  quoted from the registry.
- **[SKILLS.md](SKILLS.md)** — technical workflow skills: when to apply each,
  which tools it drives, which tools deliberately have no skill.
- **[RECIPES.md](RECIPES.md)** — three agent workflows that use existing exports, bounded
  public evidence, or a user-authorized browser without pretending that provider credentials exist.
- **[RECOVERY.md](RECOVERY.md)** — resuming a native `crawl-site` run that stopped early: the
  `crawl_state.json` checkpoint and its identical-invocation requirement, `--resume` for a
  SQLite scan, and how to tell a successful resume from an intentional fresh start.
- **[SOFTWARE_REVIEW.md](SOFTWARE_REVIEW.md)** — the evidence pack an internal
  security/legal/procurement review asks for: generated dependency/license
  inventory, release checksums and provenance, the outbound-endpoint and
  data-flow map, storage and telemetry facts, and how to verify it all.

### Repository contracts

- [AGENTS.md](../AGENTS.md) defines invariants and editing rules for coding agents.
- [PROVENANCE.md](../PROVENANCE.md) defines the clean public-history boundary.
- [THIRD_PARTY_NOTICES.md](../THIRD_PARTY_NOTICES.md) records bundled data and
  interoperability references.
- [CODE_OF_CONDUCT.md](../CODE_OF_CONDUCT.md) defines participation standards.
- [CITATION.cff](../CITATION.cff) provides versioned citation metadata.

## Documentation must not lie silently

Nobody recounts the numbers in prose by hand, so `tests/test_docs_drift.py`
recounts them. It fails when:

- a README, skill, or doc states a wrong number of tools, skills, or
  audit checks;
- a skill references a `seohead` command that does not exist;
- a README table row names a non-existent command or a wrong MCP tool name;
- a skill's frontmatter name does not match its folder or has no `description`;
- `docs/TOOLS.md` no longer names every registered CLI command, or its severity
  breakdown disagrees with the check registry;
- `docs/CHECKS.md` disagrees with what `scripts/generate_checks_reference.py`
  would produce from the registry right now, or is missing a check id.
- `docs/TOOL_REFERENCE.md` disagrees with what `scripts/generate_tool_reference.py`
  would produce from the MCP tool definitions right now, or is missing a tool.
- `docs/PROVIDERS.md` disagrees with what `scripts/generate_provider_matrix.py`
  would produce from the provider registry and workflow catalogue right now.
- a command shown in a fenced code block no longer executes against its supported fixture
  or parses against the current CLI (`tests/test_docs_commands_execute.py`). Browser, licensed,
  provider and interactive routes need their separate runtime acceptance; parser success is not it.

The contract test derives counts and command names directly from registries, so public prose
cannot silently drift away from the interfaces users actually receive.
