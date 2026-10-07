# SEOHEAD Tools

**Configurable SEO engineering for specialists working with AI agents and agent systems.**

[Website](https://seohead.tech/seotools) · [Documentation](docs/README.md) · [Examples](examples/README.md) · [Scope and trade-offs](docs/COMPARISON.md)

[![CI](https://github.com/PavloSEO/seohead-tools/actions/workflows/ci.yml/badge.svg)](https://github.com/PavloSEO/seohead-tools/actions/workflows/ci.yml)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-1565C0)
![MCP](https://img.shields.io/badge/MCP-local%20stdio-151A25)
[![MIT License](https://img.shields.io/badge/code-MIT-1565C0)](LICENSE)

SEOHEAD helps an SEO engineer turn a project goal into repeatable analysis and actionable work:
choose what to collect and extract, retain the evidence, investigate technical health, compare
changes, prepare developer tasks, recheck fixes, and build reports. Refine the configuration and
priorities for the next run; a new agent can continue from the saved project instead of chat history.

Its native Python crawler is a first-class collector. Supplied Screaming Frog exports, licensed
live SF, focused checks and explicit provider operations supply other evidence. The CLI and local
stdio MCP share the same core. An [optional authenticated remote API](docs/REMOTE_API.md) supports
operator-configured job workers; installation starts no listener. There is no hosted account or web dashboard.

## What you can build

- **Configurable collection and extraction:** native HTTP/JavaScript crawling, sitemap/list inputs,
  URL and template scope, content selectors, declarative extraction, request budgets and recovery.
- **Technical analysis:** indexing, metadata, links, redirects, structured data, hreflang,
  duplicates, infrastructure, logs and marketing-element inventories, with unavailable evidence named.
- **Evidence reuse:** retained SQLite scans, offline reanalysis, before/after and declared URL-migration
  comparisons, plus explicit joins to supplied or provider data.
- **Engineering work:** scoped project tasks and dependencies, agent handoff, finding history,
  remediation decisions and evidence-bound rechecks. Task completion and site health stay separate.
- **Reporting:** audit spreadsheets/documents, a configurable prioritized backlog, filtered finding
  views, CSV/JSON exports and BI packages. Provider credentials and external destinations are explicit.

The [capability map](docs/README.md#capability-map) connects each area to its method, CLI/MCP route,
inputs and limits. Use the [comparison guide](docs/COMPARISON.md) to evaluate a workflow against
another tool and the [generated reference](docs/TOOL_REFERENCE.md) for exact defaults and effects.

## A useful first request

> Audit this site for the agreed scope. Give the developers an Excel workbook of tasks, the
> underlying exports and evidence links, proposed fixes, and acceptance/recheck criteria.

The agent starts with [control](.claude/skills/control/SKILL.md), selects collection and extraction
policy, reuses the saved audit, and follows the [developer handoff](docs/scenarios/deliverable.md).
`report-build` produces the factual audit workbook; `sf tasks` produces the prioritized
`tasks.json`/`tasks.md` from a native or SF audit. The agent combines these into the requested
engineering handoff and reviews task-specific criteria. A formatter alone does not produce a
reviewed specification or identify a website's source-code locations. Code references require
access to that code; otherwise the handoff points to retained URL, HTML, header and link evidence.

## The repeatable project loop

1. **Define why:** record the site, agreed URL/template population, custom work and priorities.
2. **Choose what and how:** save crawl scope, rendering, extraction and resource settings.
3. **Collect once, analyze again:** reuse retained evidence where it answers the question;
   record partial, skipped and unavailable work explicitly.
4. **Compare and act:** review changes, create developer work, and keep repair verification
   in the remediation ledger.
5. **Report and improve:** select report views/formats, retain evidence and review decisions,
   then refine the next configuration or hand it to another agent.

```bash
# These calls only create/inspect local project state; they do not crawl.
seohead project new --directory ./shop --target https://example.com/
seohead project checklist-init --directory ./shop
seohead project progress --directory ./shop
```

The [project-control scenario](docs/scenarios/project-control.md) shows configuration and handoff.
[Projects](docs/PROJECTS.md) explains tasks, coverage, inbox and workflow state;
[the remediation ledger](docs/LEDGER.md) separately tracks finding cases and recheck evidence.
A prepared project or a completed task is not proof that the site's errors were fixed.

## Start with the task

| If you have… | Run | You get |
|---|---|---|
| A site with no crawl | `seohead crawl-site --url https://example.com` | A bounded native scan under `./scans/` with retained crawl evidence and audit output |
| Existing Screaming Frog exports | `seohead sf run --exports-dir ./exports --out ./report --tasks` | Offline analysis of supplied CSV/XLSX files; no SF installation, licence or target request required |
| A licensed local Screaming Frog installation | `seohead sf run --crawl https://example.com --out ./report --tasks` | A local SF crawl followed by the same audit artifacts |
| A bounded current-state evidence pass | `seohead site-audit --url https://example.com --limit 25` | One `seohead.site-audit/1` document from selected sitemap URLs and site-level checks |
| Two compatible audit documents | `seohead compare-crawls --before before.json --after after.json` | Findings that entered, changed, or disappeared between runs; an optional declared `url-correspondence.v1` map adds a saved release-review facts artifact without title/content inference |
| An agent client | `seohead mcp` | The local stdio MCP server, with the same public behavior as the CLI |

`crawl-site` is SEOHEAD's primary collector and is free to run locally; no Screaming Frog licence or paid crawl API is required. It still sends bounded, read-only requests to the approved site. Native crawling and Screaming Frog use different collectors and may discover different URL populations. Treat their outputs as distinct evidence and compare them only with compatible scope, configuration and provenance. Existing SF CSV/XLSX exports are analyzed offline and do not require an SF installation or a new crawl. Live SF mode requires a separately installed, active licence.

The product direction is full headless crawling and analysis, including JavaScript-heavy sites. Current capabilities and limits are documented below; complete feature and performance parity with other crawlers is a development target, not a verified release claim. Crawl budgets control resource use and record incomplete coverage; they do not define SEOHEAD as a small-site-only collector.

## What makes an audit honest

Every audit distinguishes four outcomes:

- **Finding:** the available evidence supports a specific problem.
- **Ran without findings:** the check was evaluated and found nothing to report.
- **Skipped:** required evidence was unavailable or incomplete; the report names the check and reason.
- **Failed or unavailable tool:** the result records the boundary failure instead of treating it as a pass.

Partial crawls withhold conclusions that need complete evidence, such as link-graph claims. Health scores are withheld when coverage is too low, and scores based on incomplete coverage are marked as not comparable with full coverage. See [the audit guideline](docs/GUIDELINE.md), [check catalogue](docs/CHECKS.md), and [coverage map](docs/COVERAGE_SF_ISSUES.md).

## Quick start

```bash
git clone https://github.com/PavloSEO/seohead-tools.git
cd seohead-tools
python -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[all]"

# Confirm the installed interface.
seohead --help

# Run the committed synthetic Screaming Frog export fixture.
seohead sf run --exports-dir examples/exports --out ./report --tasks
```

The repository is named `seohead-tools`; the Python distribution remains
`seohead-seotools` and the installed command/import package remains `seohead` for compatibility.

On Windows PowerShell, activate the environment with `.venv\Scripts\Activate.ps1`.

The `all` extra installs every optional Python dependency. Credentials for external providers and a Playwright browser binary are separate. Install only what a workflow needs when a smaller environment is preferable:

- `mcp` for the local stdio server;
- `render` for raw-versus-rendered DOM checks (install a Playwright browser separately);
- `cluster` for keyword clustering;
- `reports` for DOCX/XLSX output;
- `gsc` for the Google Search Console OAuth client;
- `sitemap` for optional sitemap helpers.

## From evidence to a deliverable

```bash
# Crawl a site locally. The URL cap is an explicit input, not a claim about site size.
seohead crawl-site \
  --url https://example.com \
  --max-urls 500 \
  --scan-out ./scans/audit.sqlite

# Render the resulting audit as a client document or working spreadsheet.
seohead report-build --audit ./scans/audit.sqlite --format docx --out audit.docx
seohead report-build --audit ./scans/audit.sqlite --format xlsx --out audit.xlsx

# Run focused evidence checks when a full crawl is not the question.
seohead parse --url https://example.com
seohead headers-check --url https://example.com
seohead schema-check --url https://example.com
seohead domain-profile --domain example.com

# Optimize images into a separate directory; sources stay untouched.
seohead images-optimize \
  --files ./images \
  --output-dir ./optimized \
  --format webp \
  --quality 82
```

`report-build` formats evidence already collected as XLSX, DOCX, CSV, Markdown, JSON, or a bilingual offline PDF. It does not run new checks or invent findings. PDF output needs the optional `seohead-seotools[pdf]` extra and a local Chrome, Edge, or Chromium executable. [Report fixtures and the field contract](examples/reports/README.md) show the resulting artifacts.

For a retained native scan, `scan reanalyze` creates a new derived SQLite artifact without a network request. [Storage documentation](docs/STORAGE.md) describes retention, provenance, and the limits of offline reanalysis.

## Find a focused workflow

Start from the [capability map](docs/README.md#capability-map), [scenarios](docs/scenarios/README.md)
or [skill map](docs/SKILLS.md). Narrow requests such as robots, schema, rendering or saved-scan
comparison go directly to their method; a full audit uses the controller.

Search a saved native scan without another crawl. For example, find which retained HEADs
contain a GTM marker; choose `--mode not_contains` to list measured absences instead:

```bash
mkdir -p ./reports
seohead scan-content-search --scan ./scans/audit.sqlite --query GTM- --scope head_markup --out-dir ./reports/gtm-check
seohead scan-content-search-page --package ./reports/gtm-check --offset 0 --limit 100
```

Coverage accompanies the result. A missing or unretained document is `unknown`, and a stored tag
is not proof of execution. Rendered-body search needs captured rendered evidence. To collect only
explicit sitemap members, use `crawl-site --sitemap https://example.com/sitemap.xml --sitemap-only`;
this mode does not follow page links or redirect targets outside that list.

Use `seohead <command> --help` for calling syntax and `seohead crawl-site --config-help` for the
configurable collection/extraction surface. [Input contracts](docs/INPUTS.md) distinguish URLs,
retained scans, inline data, provider queries and the separate operational stores.

## Local MCP server

Register the installed CLI as a stdio server in a compatible client:

```json
{
  "mcpServers": {
    "seohead": {
      "command": "/absolute/path/to/.venv/bin/seohead",
      "args": ["mcp"]
    }
  }
}
```

For example, after an MCP client connects, these `tools/call` parameters perform the same offline duplicate check on a retained scan:

```json
{
  "name": "seo_duplicate_check",
  "arguments": {"scan": "./scans/audit.sqlite"}
}
```

Use `seo_tool_catalog` to discover a route, `seo_skill_show` for its playbook, and
`seo_scenario_show` for an end-to-end example. File-producing tools return paths for the next step.
A catalogue result describes a capability; it neither executes it nor enables a tool removed by
the selected MCP profile.

The CLI and MCP server share handlers and registration checks. The generated [tool reference](docs/TOOL_REFERENCE.md) is the authoritative list of available commands, arguments, side effects, network use, idempotency, and provider spend. [Scenarios](docs/scenarios/README.md) connect a specialist goal to an ordered tool chain and a usable artifact. For an agent beginning an unscoped audit, start with [the control workflow](.claude/skills/control/SKILL.md).

Start with `seohead mcp --profile full` for the complete local surface. The
`audit`, `infra`, `quick-check`, and `router` profiles remove unrelated schemas
at startup. MCP progress notifications are sent only when the caller provides a
standard progress token; elapsed updates label the total as unknown and do not
claim completion. See [MCP profiles and progress](docs/MCP_PROFILES.md).

## External sources and safety boundaries

Provider integrations are optional and explicit. Yandex Cloud, Arsenkin, Yandex Metrika, DataForSEO, Search Console, and IndexNow each have their own credentials, constraints, and possible cost. DataForSEO defaults to sandbox; paid provider calls are journalled so spend can be reviewed with `spend-report`.

Network tools block private targets by default. File changes, service-path probes, bot DNS verification, provider production mode, and paid calls require explicit inputs. Image optimization writes to a separate output directory unless in-place mutation is explicitly requested; in-place mode creates backups. Secrets and client crawl data do not belong in this repository or a client report.

Native crawl and browser results do not provide field Core Web Vitals. A separate `crux-report` entry point exists, but it is credential-gated and its live access is not claimed as verified; see the [source setup notes](docs/SETUP.md). The toolkit does not provide a web-scale backlink index, a hosted multi-user dashboard, or a general-purpose content strategy. [Comparison notes](docs/COMPARISON.md) describe these boundaries before results are used in a client deliverable.

## Development

```bash
python -m pip install -e ".[dev,mcp,cluster,reports]"
ruff check .
ruff format --check .
pytest -q
seohead sf run --exports-dir examples/exports --out /tmp/seohead-report --tasks
python -m build
```

Public commands and generated references are checked in CI. Keep public prose in English, use synthetic examples only, and add a focused offline test whenever behavior changes. See [CONTRIBUTING.md](CONTRIBUTING.md), [SECURITY.md](SECURITY.md), and [architecture](docs/ARCHITECTURE.md).

## Licence and provenance

The Python implementation and documentation are released under the [MIT License](LICENSE). The bundled Schema.org vocabulary retains its original CC BY-SA terms. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md), [PROVENANCE.md](PROVENANCE.md), [TRADEMARKS.md](TRADEMARKS.md), and [CITATION.cff](CITATION.cff) for the relevant notices and policies.
