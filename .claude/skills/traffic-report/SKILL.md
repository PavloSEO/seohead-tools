---
name: traffic-report
description: >-
  Build a client-facing traffic and conversion report (monthly or yearly PDF slides) from
  Yandex Metrika, Yandex Webmaster and Google Search Console without Looker/Data Studio:
  collect → model → render, with search, sections, products, conversions and a buyer
  portrait. Triggers: "monthly report", "annual report", "traffic report", "report instead
  of Data Studio/Looker", "conversion report", "where conversions come from", "buyer
  portrait", "year over year traffic", "PDF report for the client".
---

# Traffic report — from analytics APIs to client slides

A repeatable path for the report a client reads instead of a dashboard: what search
brought, how sections and products performed, how many visits ended in a contact, from
which pages, and who the buyers are. The toolkit supplies every data client; the report
itself is a small per-project script set that lives in the **project's own repository**,
never in this one.

## Trigger
- "Monthly / quarterly / annual report", "report instead of Data Studio / Looker".
- "Where do conversions come from", "which landing pages convert", "buyer portrait".
- A year-over-year or month-over-month comparison of search traffic with charts.

## Anti-trigger
- One quick dashboard-style PDF for one month with standard pages only — run
  `seohead metrika-traffic-pdf` directly; it is the packaged version of the same idea.
- A technical site audit — `control` / `site-report`.
- Raw visitor-level data (ClientIDs, sessions, Logs API) — out of scope by design.

## Layout of a report project (in the client repo, not here)

```
reports/<report-name>/
  config.json      counter id, Webmaster host id, GSC property, periods, lead goals, brand regex, logo path
  collect.py       read-only collection through seohead.data_sources → raw/*.json (cached, --refresh)
  analyze.py       raw/ → model.json: monthly series, period totals, sections, products, leads, portrait
  build.py         model.json → out/*.html → PDF (seohead.reports.chromium_pdf.print_to_pdf)
  README.md        internal method, exclusions, reconciliation — never part of the client PDF
```

`config.json` holds identifiers only. **No tokens, keys or passwords** in any file of the
report project: credentials are read by the toolkit from its own configuration
(`sources-doctor` shows where). Raw exports may contain client data — keep the report in a
private repository or gitignore `raw/`.

## Data sources (all read-only)

| Need | Toolkit call | Notes |
|---|---|---|
| Visits, users, goals, landing/exit pages, demographics | `MetrikaClient().report(params, paginate=True, offset=1)` | `offset` is 1-based; filters are RE2 (`\s` is rejected) |
| Shows/clicks by day, pages in search, SQI | `yandex_webmaster.collect("search_history" / "in_search_history" / "sqi_history" / "summary", host_id=…)` | full history |
| Yandex queries | `yandex_webmaster.collect("search_performance", paginate=True, params={...})` | the API keeps only ~3 months — accumulate monthly |
| Google clicks, impressions, pages, queries | `gsc.search_analytics(site, dimensions=[...], row_limit=25000)` | 16 months; most queries are anonymised |
| Page titles for readable labels | `seohead crawl-site --urls-file … --out-dir … --max-urls-per-second 1` | list mode, polite rate |
| Section of a URL | the site's sitemaps (`sitemap-crawl` or plain fetch) + URL rules | |

## Method

1. **Attribution.** Use the last-significant-source groups (`ym:s:lastSignTrafficSource`,
   `lastSignSearchEngineRoot`, `lastSignSearchPhrase`) — that is what Metrika's interface
   and Looker show. Reconcile one month against the dashboard to the unit before trusting
   the rest; write the reconciliation into README.
2. **Count conversions as visits.** A "lead" is a visit with at least one contact goal:
   `filters="(ym:s:goal==A OR ym:s:goal==B …)"`. One person who copies the e-mail, calls and
   sends a form in one visit is one lead. Show the goal reaches separately as "actions".
3. **Sanity-check every goal before using it.** Look at the landing and exit pages and the
   channel of the visits that reach it. Goals that fire on login/registration, internal
   tools or tests are not leads — exclude them and say so in README.
4. **Goals created mid-period** cannot be compared year over year as a total: compare the
   goals that existed in both periods, or equal quarters.
5. **Bot spikes** (direct visits jumping by an order of magnitude) — exclude them from the
   "all visits" comparison and give the client one plain sentence about it.
6. **Where the action happened.** Metrika reports do not expose the page of a goal reach;
   the exit page (`ym:s:endURL`) of lead visits is the closest estimate.
7. **Products.** If the site is a shop, join landing pages to the catalogue
   (product → manufacturer → category → series) exported read-only from the site, and
   report visits and leads by manufacturer and product type, plus top products by visits
   and by leads.
8. **Portrait.** For lead visits vs all visits: age, gender, device, OS, region, hour,
   weekday, days since first visit, search phrases (share with a model code vs generic vs
   brand). An index (share among leads ÷ share among all visits) shows who converts.

## Slides — client version

- Chapters with divider pages: search → all traffic → sections → products → conversions →
  portrait → summary and next steps. A monthly report adds "work done" (from a short
  markdown file the specialist writes) and "plan / what we need from you".
- **The headline is the conclusion** ("Search brought 2 402 visits — ▲ +33% to August"),
  then two or three big numbers, one or two short bullets, one chart.
- Changes in **percent with ▲/▼** (green good, red bad) — never "×3.9".
- Pages by **title, as clickable links**; rounded cards, light background, a bright
  palette validated for colour-vision deficiency.
- Nothing about API limits, attribution internals or excluded goals in the client PDF —
  that goes to README.
- Render, then look at every page (PDF → PNG contact sheets): no overflow, no clipped
  labels, no empty bottom third.

## Done when
- One month reconciles with the client's dashboard; differences are explained in README.
- Every lead goal was checked by its pages and channels.
- All pages were inspected visually; links in the PDF open the right pages.
- No credential, token or personal data is present in the report project.
