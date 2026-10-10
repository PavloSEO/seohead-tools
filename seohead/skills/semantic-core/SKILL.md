---
name: semantic-core
description: >
  Build and grow an accumulating semantic core (keyword pool) with `seohead semantics`: one SQLite
  database per project, resumable stages, Yandex Wordstat demand, SERP clustering, exact frequency and
  competitor mining. Use when you need to collect, clean, cluster or extend a keyword core, explain why a
  phrase was dropped, or plan provider spend so nothing is paid for twice.
  Triggers: "semantic core," "keyword pool," "collect keywords from Wordstat," "cluster keywords by SERP,"
  "exact frequency," "add competitor keywords," "how much will the core cost."
---

# Semantic Core

`seohead semantics <stage> --project DIR` runs one stage against a project directory that holds
`project.yaml`, `seeds.txt` and the SQLite database (`sya.db`, named by `db:` in `project.yaml`).
Pointing `--project` at a SEOHEAD project workspace keeps the core in its `semantics/` folder.
MCP: `seo_semantics_status`, `seo_semantics_run` (free stages), `seo_semantics_mine` (public pages),
`seo_semantics_paid` (paid stages, needs `confirm_paid=true`).

The store only accumulates. A phrase is never deleted: a stage changes its status (`new`, `kept`,
`review`, `dropped`, `dead`) and records the reason and the stage, so every decision is reversible
and explainable (`phrases.reason`, `phrases.reason_stage`).

---

## Stages

| Stage | Cost | What it does |
|---|---|---|
| `init` | free | creates `project.yaml`, `seeds.txt`, the database |
| `import` | free | loads a CSV (`norm`/`phrase`/`query` column, optional `base`, `quoted`, `exact`, `impr`, `pos_y`, `pos_g`) |
| `collect` | **paid** (Yandex Wordstat) | expands seeds, re-expands strong phrases up to `max_depth`; `--resume` continues |
| `clean` | free | statuses and routes from the preset, frequency outliers, lemma groups |
| `graph` | free | homonym detector and candidate clusters → `anomalies.md` |
| `sitematch` | free | phrases without a `catalog.txt` term go to review |
| `cluster` | **paid** unless cached (Yandex Search API) | one SERP per phrase, union-find on shared pages; `--method louvain` needs the `semantics` extra |
| `relevance` | free | phrases whose SERP has no `vendors.txt` peer go to review |
| `competitors` | free | competitor domains and title n-grams → `synonyms.txt` |
| `mine` | web, no quota | competitor page headings → `synonyms.txt` |
| `synonyms` | **paid** (Yandex Wordstat) | checks `synonyms.txt` candidates and adds those with demand |
| `exact` | **paid** (Arsenkin limits) | exact `!W` frequency, once per lemma group; `--yes` above `budget_arsenkin_gate` |
| `report` / `excel` / `export` | free | `report.md` + `clusters.csv`, the XLSX workbook, `pool.csv` + `pool.json` |
| `status` | free, read-only | counters, provider units used, the next stage |

---

## Order of Work

```
init → (import) → collect → clean → graph → sitematch → cluster → relevance/competitors → mine
     → synonyms → collect --resume → clean → cluster … → exact (once, at the end) → report/excel
```

1. Write 3–10 seeds and the project filters (`preset`, `intent`, `stop`, `info`, `anchor_words`)
   before the first paid call. Filters decide what is expanded; a loose filter pays for junk.
2. Run every free stage between paid ones and read `anomalies.md` and the review list.
3. Cluster the **whole** core in one run. `--limit` cuts clusters apart and exists for debugging.
4. Close the loop with `competitors` / `mine` → `synonyms` → `collect --resume`, then cluster again.
5. Buy exact frequency last, when the pool has stopped changing.

---

## Spending Less

- **Free before paid.** `clean`, `graph`, `sitematch` and `relevance` shrink the pool before each
  paid stage; every phrase removed before `cluster` or `exact` is a request not bought.
- **Cache is the budget.** Expanded seeds (`expansions`), SERP (`serp`, `serp_fetches`) and
  Arsenkin tasks (`provider_tasks`) are stored. Re-running a stage pays only for new phrases.
- **Never pay twice.** A paid submission is reserved before the call. An interrupted, timed-out or
  ambiguous request is not resent automatically: an Arsenkin result is fetched again by its
  `task_id` for free, an unresolved SERP receipt must be reconciled first.
- **Base demand first, exact once at the end.** Wordstat base frequency is cheap and comes with
  expansion; exact `!W` is bought once per lemma group, and only for kept phrases.
- **Asynchronous SERP only.** The toolkit uses the deferred Yandex Search API; synchronous search
  costs an order of magnitude more per request and must not be used for batches.
- **Reuse other projects' evidence.** Import an existing pool or search-console export with
  `import` instead of collecting it again; observed demand (`impr`, `pos_*`) also protects a phrase
  from frequency cut-offs.
- **Grow `reexpand_floor` with the pool.** Expansion has no natural end; a low floor makes the
  frontier explode on large topics. Raise it so only high-demand phrases are expanded again.
- **Check the bill, not an estimate.** Provider charges land in the shared journal; read them with
  `seohead spend-report` (MCP `seo_spend_report`). `status` shows the project's provider units.

---

## Example

`docs/examples/semantics/seohead-tech/` is a small synthetic project (an SEO-tool topic, no real
search-console data). Copy it, then run the free stages:

```
seohead semantics init --project ./seohead-tech
seohead semantics import --project ./seohead-tech --file ./seohead-tech/phrases.csv
seohead semantics clean --project ./seohead-tech
seohead semantics graph --project ./seohead-tech
seohead semantics sitematch --project ./seohead-tech
seohead semantics report --project ./seohead-tech
```

## Rules

- Paid stages run only after the person agreed to spend; through MCP that is `confirm_paid=true`.
- Back up the database before a paid stage (`sqlite3 sya.db ".backup sya-<date>.db"`).
- Keep client cores, configs and exports in the client's own private folder, never in this repository.
- A phrase with observed impressions or positions is never cut by frequency rules.
