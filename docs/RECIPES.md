# Agent recipes without provider API keys

These workflows use committed fixtures, user-supplied exports, bounded public-page checks, or a
host-provided browser session. SEOHEAD Tools does not bundle browser credentials or claim an API
connection that has not been configured.

## 1. Audit existing Screaming Frog exports

Use this when the crawl already exists and no new live crawl is needed.

```bash
python -m pip install -e ".[mcp,cluster,reports]"
seohead sf run \
  --exports-dir ./sf-exports \
  --out ./report \
  --tasks
```

Review `report/audit.json` before the rendered report. Preserve `checks_skipped` and failure reasons;
an absent export is not a passing check. The source must be compatible Screaming Frog CSV/XLSX,
not an assumed drop-in export from another crawler.

Deliver:

- `audit.json` as the evidence contract;
- `audit.md` as the review view;
- `tasks.md` as a proposed backlog, not an approved implementation plan.

## 2. Investigate a traffic decline from console exports

First define the property, search type, comparison periods, filters, and ignored output directory.
Use the `analytics-console-review` skill when a user-authorized browser is available, or ask the
user to export aggregate page data manually.

For GSC, export the Pages comparison before requesting query rows for selected losing pages. For
Metrika, use aggregate landing-page metrics and explicit goals; do not request Logs API data or
visitor identifiers.

For each candidate URL, add deterministic page evidence:

```bash
seohead parse --url https://example.com/page
seohead headers-check --url https://example.com/page
seohead robots-check --url https://example.com/robots.txt
```

If Screaming Frog exports are available, run recipe 1 and join by normalized canonical URL. Report:

- observed traffic/search deltas;
- crawl/indexability/internal-link evidence;
- join confidence and unmatched rows;
- candidate explanations and missing checks;
- data limitations.

Do not equate GSC clicks with analytics sessions or describe correlation as cause.

## 3. Run a bounded live audit safely

Use a reserved example or an explicitly authorized public site. Start small:

```bash
seohead site-audit \
  --url https://example.com \
  --limit 25 \
  --report json \
  --out ./site-audit.json
```

This is a sitemap-based evidence pass, not a general-purpose crawl and not an exhaustive execution
of every tool. Keep private-network access, security-path probes, bot DNS verification, sitemap
live rechecks, provider production mode, and file mutation disabled unless the user separately
authorizes the exact side effect.

Record the URL, retrieval time, limit, failed tools, skipped checks, and unresolved uncertainty.
Only increase the limit after reviewing the first output.

## Data handling

- Store exports under an ignored working directory.
- Never commit property names, client URLs, raw analytics exports, access logs, or authentication
  material.
- Delete temporary exports when the review no longer needs them.
- Hash files used as evidence so a later review can identify exactly which export was analyzed.
