---
name: site-report
description: >-
  Build XLSX, DOCX, CSV or Markdown reports from saved audit evidence, or run an explicitly
  requested bounded sitemap/list-based site-audit. Use for report formatting, not as the entry
  point for an unscoped full audit or link crawl; those route through control. Report formatters
  arrange measured facts and do not produce a reviewed developer specification.
---

# Site Report — From Domain to File

The consolidation is implemented in code: one call assembles the evidence document,
and a second call turns it into a file.

## Trigger
- A saved audit needs a human-readable file, or an explicitly bounded sitemap/list pass is wanted.
- "Excel report," "Word report," "export to CSV," "client report," "build a
  report," "xlsx," "docx," "site audit," "SEO report."
- A sitemap-driven audit is wanted without running a full Screaming Frog crawl.

## Anti-trigger
- A full site crawl is needed (arbitrary depth, all internal links, not just the
  sitemap), or the full-audit scope is unstated — use `control`, which selects the native
  collector by default and an SF path only when that input is requested/available.
- The deliverable is a Screaming-Frog-derived issue backlog or a topical/silo
  verdict — those are `sf-tasks` and `silo-audit`, both built on an SF crawl, not
  on this skill's site-level + per-page checks. Native audit evidence also feeds `sf-tasks`.
- Only a `robots.txt` directive review is wanted — use `robots-audit`; this skill
  treats `robots.txt` only as the place it reads the sitemap URL from, it does not
  analyze the directives themselves.
- A delta against a previous audit is wanted ("what changed since last month") —
  use `compare-crawls` for supported compatible audits; the report formatter itself produces no diff.

## Preconditions
- [ ] The domain resolves and either serves a discoverable sitemap via
  `robots.txt`, or a custom `--urls` list is supplied instead.
- [ ] `--limit`/`--concurrency` have been considered for the site's size — the
  default 25-page limit under-samples a large site, and raising it multiplies
  request volume by N×3 (see the Flags table below).
- [ ] The output format(s) actually needed are decided up front (xlsx/docx/csv/md)
  — the JSON evidence document is the single source of truth, so extra formats
  only cost one more `report-build` call each, not a re-audit.

## Workflow

**Saved evidence first:** when a native/SF audit or supported scan already answers the scope,
call `report-build` directly. Do not launch `site-audit` simply to obtain another output format.
For a developer task workbook, use `sf-tasks` and the reviewed engineering handoff; this formatter
exports findings and does not invent implementation or acceptance criteria.


**Everything at once — audit and Excel in a single command:**
```bash
seohead site-audit --url https://example.com --limit 50 --report xlsx --out audit.xlsx
```

**Two steps when multiple reports are needed:**
```bash
seohead site-audit --url https://example.com --limit 100 > audit.json
seohead report-build --audit audit.json --format docx --out client.docx
seohead report-build --audit audit.json --format csv  --out tasks.csv
```

**A custom page list instead of the sitemap** (for example, landing pages from the
semantic keyword set):
```bash
seohead site-audit --url https://example.com --urls "https://example.com/a,https://example.com/b"
```

## What Runs

| Level | Tools | Number of Runs |
|---|---|---|
| Site | domain, CDN and cache, stack, security, robots, AI crawlers, llms.txt, regions, rendering, sitemap | once |
| Page | `parse`, `schema-check`, `social-meta-check` | for every URL |

The URL list comes from the sitemap, whose address is read **from robots.txt**. If
there is no sitemap, at least the home page is analyzed, and this is reflected in
`pages_checked`.

## Flags That Actually Change the Result

| Flag | When It Is Needed |
|---|---|
| `--limit N` | 25 pages by default. Increase it deliberately on a large site: this generates N×3 requests |
| `--concurrency N` | 5 by default, with a ceiling of 10. The audit must not resemble a load test |
| `--render` | a script renders the city switcher (requires Playwright) |
| `--skip` | avoid unnecessary checks: `--skip render_check,regions_check` |

## How to Read the Result

**Start with `summary.tools_failed`.** This is the most important field: it lists
the checks that did not run successfully. Their silence does **not** mean "no issues
found." All four formats print this block separately, and the report should be read
starting with it.

**Then read `findings` by level.** `critical` means the saved evidence matches a
high-severity rule (for example, an empty page, an off-site canonical, or noindex).
It does not measure current ranking loss. `warning` means the rule identifies a
problem likely to interfere with crawling or indexing. `notice` is an observation.

**The level is assigned by rules, not measured.** The rule table is
`SEVERITY_RULES` in `seohead/audit/site.py`; order matters (the first match wins).
The document states this explicitly in `summary.severity_note`. If the client
disagrees with a priority, the rule can be shown and discussed.

**Deliver the recorded observation, not an internal label.** Human formats name
the issue in plain language and show its saved URL/status/location as the
reproduction. They do not use a check identifier or the name of the collector as
evidence. If an older audit lacks those primitive facts, say that reproduction is
unavailable from the saved audit; do not add a live request or invent one.

## Which Format Is for Whom

| Format | Audience | Why |
|---|---|---|
| `xlsx` | you and the developers | filters, sorting, a live Excel chart, and findings distributed one per row |
| `docx` | the client | text with headings: an executive summary (counts, unavailable checks) first, then evidence — never a generated conclusion |
| `csv` | findings, for the tracker to map | three files (findings, `*.pages.csv` and `*.scope.csv`), `;` and BOM — otherwise Excel displays garbled characters; one row per finding, not a grouped task |
| `md` | Git and correspondence | readable anywhere |

## Boundaries
- **This collection mode is bounded.** `site-audit` uses the sitemap or a supplied list;
  native `crawl-site` is the first-class link collector. See `control` for collection selection.
- **The report does not calculate anything.** The generators only arrange JSON into
  worksheets and paragraphs. If a number is absent from the document, it will also
  be absent from the report.
- **Comparison is separate.** Use `compare-crawls` only for its supported audit inputs with
  compatible scope and provenance; save each original document.

## Decision points
- **`pages_checked` is lower than expected.** Check whether that is because no
  sitemap was discoverable (a real site problem worth flagging) versus because a
  deliberate, shorter `--urls` list was supplied (expected) — the field alone does
  not distinguish the two causes.
- **Raising `--limit` on a large site.** Since it multiplies request volume by
  N×3, decide based on the site's actual size and how representative a smaller
  sample already is, not by defaulting to the maximum out of caution.
- **`summary.tools_failed` is non-empty.** Never report "no issues found" for a
  check that is listed there — state explicitly which checks did not run and that
  their absence from `findings` is not a clean bill of health.
- **Client disputes a severity level.** Do not re-judge the finding ad hoc — show
  the actual matching rule from `SEVERITY_RULES` (`summary.severity_note` names
  it) and discuss whether the rule itself should change, rather than overriding
  one instance of it.

## Definition of done
- [ ] `audit.json` (or the single JSON evidence document) was produced, and
  `pages_checked` matches either the sitemap count or the explicit `--urls` count.
- [ ] `summary.tools_failed` was read and reported to the user even when empty.
- [ ] Every requested output format was generated with `report-build` from that
  same JSON — no re-audit for a second format.
- [ ] Findings delivered to the user are grouped by critical/warning/notice per
  `SEVERITY_RULES`, not by ad hoc judgment.
- [ ] Each finding has its saved reproduction, or explicitly says which evidence
  is absent; failed and partial-run warnings remain before the executive summary.

## Cost
`seohead site-audit` runs the site-level checks (domain, CDN/cache, stack,
security, robots, AI crawlers, llms.txt, regions, rendering, sitemap — roughly
ten checks) once, plus three page-level checks (`parse`, `schema-check`,
`social-meta-check`) per URL up to `--limit` (25 by default, so ~75+ requests at
the default). Raising `--limit` scales page-level cost linearly (N×3 requests);
`--concurrency` (default 5, cap 10) controls how aggressive that is. `report-build`
itself makes no network requests — it only formats the already-produced JSON, so
generating additional output formats is free of extra site load. No paid API is
touched by either command.

## Templates
[`examples/reports/`](../../../examples/reports/README.md) contains `minimal.json`,
`full.json`, and a field-by-field explanation of the contract. It also shows what
to populate when the document is assembled by custom code rather than by the
audit.

## Related Skills
`control` (collection choice) · `sf-analyzer` (selected SF input) · `sf-tasks`
(backlog from a crawl audit) · `regional-audit` · `js-render-check` · `seo-recon`.
