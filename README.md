<p align="center"><img src="docs/assets/logo.svg" alt="SEOHEAD logo: a spider web inside a magnifying glass with a black widow spider" width="96" height="96"></p>

# SEOHEAD Tools

**A headless, local-first SEO crawler and audit toolkit for SEO engineers and the AI agents they work with.**

[Website](https://seohead.tech/seotools) · [Documentation](docs/README.md) · [CLI overview](docs/CLI.md) · [Examples](docs/examples/README.md) · [Scope and trade-offs](docs/COMPARISON.md)

[![CI](https://github.com/PavloSEO/seohead-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/PavloSEO/seohead-tools/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-1565C0)
![MCP](https://img.shields.io/badge/MCP-local%20stdio-151A25)
[![MIT License](https://img.shields.io/badge/code-MIT-1565C0)](LICENSE)

SEOHEAD turns a project goal into repeatable technical-SEO work: crawl or import evidence, keep it
in a retained SQLite scan, analyse and re-analyse it offline, compare releases, hand prioritized
tasks to developers, recheck their fixes and build reports. Everything runs on your machine
through one Python core with two equal interfaces: the `seohead` CLI and a local stdio MCP server
for Claude and other agent clients. There is no hosted account and no web dashboard.

<p align="center"><img src="docs/assets/cli-crawl.png" alt="seohead crawl-site crawling a local QA fixture: rate-limited progress lines followed by the audit summary" width="820"></p>

**Contents:** [Install](#install) · [Quick start](#quick-start) · [What it can do](#what-it-can-do) ·
[CLI](#cli-overview) · [MCP for Claude](#mcp-server-for-claude-and-other-agents) ·
[Projects and agents](#projects-and-agent-handoff) · [Desktop app](#desktop-app) ·
[Honest results](#what-is-measured-and-what-is-not) · [Development](#development)

## Install

Python 3.10 or newer. Clone the repository and install it into a virtual environment:

```bash
git clone https://github.com/PavloSEO/seohead-tools.git
cd seohead-tools
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[all]"

# Confirm the installed interface.
seohead --help
```

With [uv](https://docs.astral.sh/uv/), `uv sync --all-extras` creates the same environment from
the committed `uv.lock`, and `uv run seohead --help` runs it. On Windows PowerShell, activate the
virtual environment with `.venv\Scripts\Activate.ps1`.

The repository is named `seohead-tools`; the Python distribution is `seohead-seotools` and the
installed command and import package are `seohead`.

`all` installs every optional Python dependency. Smaller environments can pick extras:

| Extra | Enables |
|---|---|
| `mcp` | The local stdio MCP server |
| `render` | Raw-versus-rendered DOM checks and JavaScript crawling (install a Playwright browser separately) |
| `reports` | XLSX and DOCX output |
| `pdf` | PDF reports (also needs a local Chrome, Edge or Chromium) |
| `cluster` | Keyword clustering |
| `gsc` | The Google Search Console OAuth client |
| `sitemap` | Optional sitemap helpers |
| `tui` | The `watch` observer and the interactive terminal shell |
| `remote` | The optional authenticated remote job API ([REMOTE_API.md](docs/REMOTE_API.md)) |

Provider credentials and browser binaries are separate and never required for the core crawl.
[Setup from zero](docs/SETUP.md) covers versions, environment variables, Docker
([CONTAINERS.md](docs/CONTAINERS.md)) and headless Linux servers ([LINUX_VPS.md](docs/LINUX_VPS.md)).

## Quick start

### 1. Crawl a site, audit it, build a report

```bash
# Crawl locally. The URL cap is an explicit budget, not a claim about site size.
seohead crawl-site \
  --url https://example.com \
  --max-urls 500 \
  --scan-out ./scans/audit.sqlite

# Format the retained audit as a working spreadsheet or a client document.
seohead report-build --audit ./scans/audit.sqlite --format xlsx --out audit.xlsx
seohead report-build --audit ./scans/audit.sqlite --format docx --out audit.docx
```

`crawl-site` is the primary collector: free, local, polite by default (about two requests per
second per host), resumable ([RECOVERY.md](docs/RECOVERY.md)) and configurable through
`--config`/`--set` (see `seohead crawl-site --config-help` and
[HEADLESS_CAPABILITIES.md](docs/HEADLESS_CAPABILITIES.md)). It sends only bounded, read-only
requests to the site you name. `report-build` formats existing evidence as XLSX, DOCX, CSV,
Markdown, JSON or a bounded PDF overview ([PDF_OVERVIEW.md](docs/PDF_OVERVIEW.md)); it never
invents findings.

### 2. Analyse existing Screaming Frog exports

```bash
# Offline: no SF installation, licence or request to the site is needed.
seohead sf run --exports-dir docs/examples/exports --out ./report --tasks
```

You get `audit.json`, `audit.md` and a prioritized `tasks.md`/`tasks.json` backlog with
reproduction steps, DOM positions and fixes. A separately installed, licensed SF CLI can also be
driven directly with `sf run --crawl`. Other crawlers' CSV exports are not drop-in compatible; they
are read through a versioned manifest ([THIRD_PARTY_CRAWL_IMPORT.md](docs/THIRD_PARTY_CRAWL_IMPORT.md)).

<p align="center"><img src="docs/assets/cli-sf-audit.png" alt="seohead sf run on the synthetic export fixture, followed by the first prioritized tasks in tasks.md" width="820"></p>

### 3. Run it as a project with an agent

```bash
# These calls only create or inspect local project state; they do not crawl.
seohead project new --directory ./shop --target https://example.com/
seohead project checklist-init --directory ./shop
seohead project progress --directory ./shop
```

Register the MCP server (below), then ask the agent for the outcome you need, for example:

> Audit this site for the agreed scope. Give the developers an Excel workbook of tasks, the
> underlying exports and evidence links, proposed fixes, and acceptance/recheck criteria.

The agent starts from the [control](.claude/skills/control/SKILL.md) skill, records scope and
crawl policy in the project, reuses retained evidence and follows the
[developer handoff](docs/scenarios/deliverable.md) scenario. See
[Projects and agent handoff](#projects-and-agent-handoff).

## What it can do

Each area links to the page that documents its method, inputs and limits. The
[capability map](docs/README.md#capability-map) answers the same question by engineering task.

| Area | What you get | Read |
|---|---|---|
| **Native crawling** | HTTP and optional JavaScript crawling, sitemap/list/URL-file modes, scope and template rules, request/time budgets, robots policy, resume, crawl diagnosis | [USAGE](docs/USAGE.md), [RECOVERY](docs/RECOVERY.md), [URL lists](docs/URL_LIST_SCANS.md), [scale profile](docs/SCAN_CAPACITY_PROFILE.md) |
| **Extraction** | Content selectors, declarative extraction rules over retained bodies, saved-scan source search (for example, which pages carry a GTM marker) | [HEADLESS_CAPABILITIES](docs/HEADLESS_CAPABILITIES.md), [saved evidence](docs/scenarios/saved-evidence.md) |
| **Audit checks** | A generated check registry shared by native crawls and SF exports: status codes, redirects, indexability, canonicals, metadata, headings, hreflang, structured data, images, links, sitemaps, rendering and more | [CHECKS](docs/CHECKS.md), [scenarios](docs/scenarios/README.md), [SF coverage](docs/COVERAGE_SF_ISSUES.md) |
| **Focused URL checks** | `parse`, `robots-check`, `headers-check`, `redirects-check`, `links-check`, `hreflang-check`, `schema-check`/`schema-build`, `render-check`, `soft404-check`, `mirror-check`, `social-meta-check` | [CLI: page checks](docs/CLI.md#page-and-url-checks) |
| **JavaScript and navigation** | Raw versus rendered DOM, rendered routes, observed navigation, lab timings | [browser navigation](docs/BROWSER_NAVIGATION.md), [rendering scenario](docs/scenarios/rendering.md) |
| **Saved scans** | Retained SQLite evidence with provenance: inspect, export, snapshot, pin, prune, requeue, body diff, offline reanalysis | [STORAGE](docs/STORAGE.md), [SQLite acceptance](docs/SQLITE_ACCEPTANCE.md) |
| **Content and semantics** | Near-duplicates, boilerplate, semantic inputs and similarity, meta-description drafts, CTA/form inventory, keyword clustering | [content scenario](docs/scenarios/content.md), [marketing inventory](docs/scenarios/marketing-inventory.md) |
| **AI search (GEO/AEO)** | AI-crawler access in robots.txt, `/llms.txt` scoring, content citability | [AI visibility](docs/scenarios/ai-visibility.md) |
| **Infrastructure and security** | Domain/DNS/hosting profile, CDN and cache behaviour, tech stack, security headers, CT-log subdomains, Wayback history, access-log analysis, regional structure, known-donor backlinks | [infrastructure scenario](docs/scenarios/infrastructure.md), [skills](docs/SKILLS.md#recon-and-technical-hygiene) |
| **Compare and verify** | Before/after crawl diffs, declared URL migrations, segment diffs, bounded fix verification and a remediation ledger with recheck evidence | [COMPARE](docs/COMPARE.md), [LEDGER](docs/LEDGER.md), [comparison scenario](docs/scenarios/comparison.md) |
| **Reports and BI** | XLSX/DOCX/CSV/Markdown/JSON/PDF reports, prioritized task backlog, saved finding views, multi-site facts tables, typed BI packages with Sheets/BigQuery plans | [BI](docs/BI.md), [report fixtures](docs/examples/reports/README.md), [deliverable scenario](docs/scenarios/deliverable.md) |
| **Projects and agents** | Project workspace, checklist coverage, priorities, crawl policy, observer, inbox, checkpointed workflow runs, one-shot monitoring | [PROJECTS](docs/PROJECTS.md), [WORKFLOWS](docs/WORKFLOWS.md), [TERMINAL](docs/TERMINAL.md) |
| **Search and analytics providers** | Search Console, GA4, Yandex Metrika and Webmaster, CrUX, IndexNow, Wordstat/Arsenkin, DataForSEO, Topvisor, Miratext; local history, joins and spend journal | [PROVIDERS](docs/PROVIDERS.md), [provider workflow](docs/scenarios/provider-evidence.md), [GOTCHAS](docs/GOTCHAS.md) |
| **Method skills** | Packaged playbooks and end-to-end scenarios an agent can load (`skill-show`, `scenario-show`) | [SKILLS](docs/SKILLS.md), [scenarios](docs/scenarios/README.md) |
| **Operations** | Optional authenticated remote job API, durable jobs, Docker image, VPS install | [REMOTE_API](docs/REMOTE_API.md), [REMOTE_JOBS](docs/REMOTE_JOBS.md), [CONTAINERS](docs/CONTAINERS.md), [LINUX_VPS](docs/LINUX_VPS.md) |

## CLI overview

Every capability is a shared handler with a CLI command and an MCP tool of the same name
(`crawl-site` ↔ `seo_crawl_site`). Use `seohead <command> --help` for syntax.

| Job | Commands | Full list |
|---|---|---|
| Collect a site | `crawl-site`, `crawl-diagnose`, `crawl-import`, `crawl-enrich`, `sitemap-crawl`, `site-audit`, `inspect-url` | [Collect](docs/CLI.md#collect-a-site) |
| Work with saved scans | `scan list`, `scan inspect`, `scan url-detail`, `scan content-search`, `scan extract`, `scan export`, `scan reanalyze` | [Saved scans](docs/CLI.md#saved-scans-offline) |
| Check pages and infrastructure | `parse`, `robots-check`, `headers-check`, `schema-check`, `render-check`, `domain-profile`, `security-check`, `log-analyze` | [Pages](docs/CLI.md#page-and-url-checks), [Infra](docs/CLI.md#infrastructure-security-and-recon) |
| Content, AI search, assets | `duplicate-check`, `boilerplate-report`, `citability-check`, `llms-txt-check`, `images-optimize`, `redirects-generate` | [Content](docs/CLI.md#content-semantics-and-assets), [GEO](docs/CLI.md#ai-search-readiness-geoaeo) |
| Compare and remediate | `compare-crawls`, `segment-diff`, `verify-fixes`, `remediation-*` | [Compare](docs/CLI.md#compare-verify-and-remediate) |
| Report | `report-build`, `sf tasks`, `findings-view`, `facts-export`, `bi-*` | [Reports](docs/CLI.md#reports-views-and-bi) |
| Projects and agents | `project-*`, `workflow-*`, `audit-workflow`, `monitor-*`, `watch` | [Projects](docs/CLI.md#projects-and-agent-handoff), [Monitoring](docs/CLI.md#monitoring) |
| Providers | `provider-*`, `sources-*`, `gsc-*`, `metrika-*`, keyword and SERP tools, `spend-report` | [Providers](docs/CLI.md#search-analytics-and-demand-providers) |
| Discover | `tool-catalog`, `skill-list`, `skill-show`, `scenario-show` | [Catalogue](docs/CLI.md#catalogue-and-diagnostics) |
| Screaming Frog | `sf run`, `sf tasks`, `sf doctor`, `sf save-config` | [Entry points](docs/CLI.md#entry-points-that-are-not-single-tools) |

<p align="center"><img src="docs/assets/cli-help.png" alt="seohead --help listing commands and the grouped scan, project, skill, scenario, sf, mcp, tui and watch entry points" width="720"></p>

[TOOLS.md](docs/TOOLS.md) explains network use and side effects by layer; the generated
[tool reference](docs/TOOL_REFERENCE.md) is authoritative for arguments, defaults, idempotency
and provider spend; [INPUTS.md](docs/INPUTS.md) lists what each command accepts.

## MCP server for Claude and other agents

**Claude Code** — register the installed CLI (use the absolute path to your virtual environment):

```bash
claude mcp add seohead -- /absolute/path/to/seohead-tools/.venv/bin/seohead mcp
```

Inside this repository the committed [`.mcp.json`](.mcp.json) already registers `seohead` for
Claude Code when the virtual environment is active.

**Claude Desktop and other stdio clients** — add the server to the client's MCP configuration
(`claude_desktop_config.json` for Claude Desktop):

```json
{
  "mcpServers": {
    "seohead": {
      "command": "/absolute/path/to/seohead-tools/.venv/bin/seohead",
      "args": ["mcp", "--profile", "full"]
    }
  }
}
```

`--profile full` exposes every tool; `audit`, `infra`, `quick-check` and `router` expose smaller
schema sets for focused sessions. Progress notifications are sent only when the client supplies
a progress token. See [MCP profiles and progress](docs/MCP_PROFILES.md).

An agent discovers routes with `seo_tool_catalog`, loads a method with `seo_skill_show` and an
end-to-end chain with `seo_scenario_show`. A catalogue entry describes a capability; it does not
execute it. The CLI shows the same catalogue:

```bash
seohead tool-catalog --query redirect --limit 5
```

<p align="center"><img src="docs/assets/cli-tool-catalog.png" alt="seohead tool-catalog returning tools with their network, writes, idempotent and paid annotations" width="820"></p>

For example, these `tools/call` parameters run an offline duplicate check on a retained scan:

```json
{
  "name": "seo_duplicate_check",
  "arguments": {"scan": "./scans/audit.sqlite"}
}
```

## Projects and agent handoff

A project directory keeps the site, agreed scope, crawl policy, checklist coverage, retained scans,
saved finding views and run history, so a new agent continues from the saved project instead of
chat history:

1. **Define why:** record the site, the agreed URL/template population and priorities.
2. **Choose what and how:** save crawl scope, rendering, extraction and resource settings.
3. **Collect once, analyse again:** reuse retained evidence; partial, skipped and unavailable
   work is recorded explicitly.
4. **Compare and act:** review changes, create developer tasks, track repairs in the
   remediation ledger.
5. **Report and improve:** choose report views and formats, then refine the next run.

<p align="center"><img src="docs/assets/cli-project-progress.png" alt="seohead project progress showing checklist counts, task states and the next actions for a local project" width="720"></p>

`seohead watch --project DIRECTORY` opens an optional terminal observer beside the chat
([TERMINAL.md](docs/TERMINAL.md)). The inbox stores the specialist's notes and proposed goals;
workflow runs checkpoint each registered step with hashed evidence so another agent can resume
exactly where the first stopped ([WORKFLOWS.md](docs/WORKFLOWS.md)). Task completion and site
health stay separate: a prepared project or a completed task is not proof that a site error was
fixed. Details: [Projects](docs/PROJECTS.md), [project-control scenario](docs/scenarios/project-control.md),
[remediation ledger](docs/LEDGER.md).

## Desktop app

SEOHEAD Desktop (PyQt5) is a native companion application over the same local core: it opens an
existing project, browses retained scans and URL evidence, starts explicitly confirmed native
crawls and shows their progress through the local MCP connection. It lives in `desktop/` (coming
via a separate pull request); this README will link its documentation once it lands. The CLI and
MCP server remain the reference interfaces.

## What is measured and what is not

Every audit separates four outcomes:

- **Finding:** the available evidence supports a specific problem.
- **Ran without findings:** the check was evaluated and found nothing.
- **Skipped:** required evidence was unavailable or incomplete; the report names the check and reason.
- **Failed or unavailable tool:** the boundary failure is recorded instead of being treated as a pass.

Partial crawls withhold conclusions that need complete evidence, such as link-graph claims, and
health scores are withheld or marked not comparable when coverage is low. Native crawls and
Screaming Frog are different collectors and may discover different URL populations; compare them
only with compatible scope, configuration and provenance.

Known limits:

- Native crawl and browser results are lab evidence, not field Core Web Vitals. `crux-report`
  exists but is credential-gated, and its live access is not claimed as verified
  ([SETUP.md](docs/SETUP.md)).
- `site-audit` is a bounded sitemap-based pass, not a link-graph crawl and not a run of every tool.
- There is no web-scale backlink index (`backlinks-check` verifies a list of donor pages you
  supply), no hosted multi-user dashboard and no general content strategy.
- Full feature and performance parity with commercial crawlers, including on very large or
  JavaScript-heavy sites, is a development direction, not a verified release claim
  ([COMPARISON.md](docs/COMPARISON.md), [MILLION_CRAWL_ACCEPTANCE.md](docs/MILLION_CRAWL_ACCEPTANCE.md)).

Safety boundaries: network tools block private targets unless explicitly allowed; file mutation,
service-path probes, bot DNS verification, provider production mode and paid calls require
explicit inputs. DataForSEO defaults to sandbox, and paid calls are journalled for `spend-report`.
Image optimization writes to a separate directory unless in-place mode is requested, which keeps
backups. Secrets and client crawl data never belong in this repository or a client report.
See [the audit guideline](docs/GUIDELINE.md) and [GOTCHAS.md](docs/GOTCHAS.md).

## Development

```bash
python -m pip install -e ".[dev,mcp,cluster,reports]"
ruff check .
ruff format --check .
pytest -q
seohead sf run --exports-dir docs/examples/exports --out /tmp/seohead-report --tasks
python -m build
```

Public commands, counts and generated references are checked in CI: a command shown in the docs
must still run, and generated pages must match the registries. Keep public prose in English, use
synthetic examples and reserved domains only, and add a focused offline test whenever behaviour
changes. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md),
[architecture](docs/ARCHITECTURE.md) and [testing](docs/TESTING.md).

## Licence and provenance

The Python implementation and documentation are released under the [MIT License](LICENSE). The
bundled Schema.org vocabulary keeps its original CC BY-SA terms. See
[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [PROVENANCE.md](docs/legal/PROVENANCE.md),
[TRADEMARKS.md](docs/legal/TRADEMARKS.md) and [CITATION.cff](CITATION.cff).
