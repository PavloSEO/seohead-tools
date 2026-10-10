# Semantic core

`seohead semantics` grows an accumulating semantic core (a keyword pool) for one project. Each
project is a directory with `project.yaml`, `seeds.txt` and its own SQLite database. Passing a
SEOHEAD project workspace (a directory with `project.json`) keeps the core under its
`semantics/` folder; `db:` in `project.yaml` names the database file the project uses.

```bash
seohead semantics init --project ./my-core
seohead semantics status --project ./my-core
```

The packaged `semantic-core` skill (`seohead skill-show --name general/semantic-core`) is the
method: stage order, which stages are paid, and how to avoid paying twice.

## Stages

| Stage | Reads | Writes | Money |
|---|---|---|---|
| `init` | — | `project.yaml`, `seeds.txt`, database | free |
| `import --file CSV` | a CSV with a `norm`, `phrase` or `query` column; optional `base`, `quoted`, `exact`, `impr`, `pos_y`, `pos_g` | phrases | free |
| `collect [--resume]` | `seeds.txt`, strong phrases | phrases, expansions, provenance edges | Yandex Wordstat |
| `clean` | new phrases | status, route, lemma group | free |
| `graph` | the pool | `anomalies.md`, homonym phrases to review | free |
| `sitematch` | `catalog.txt` | off-catalog phrases to review | free |
| `cluster [--method serp\|louvain]` | kept phrases | SERP cache and receipts, clusters | Yandex Search API for uncached phrases |
| `relevance` | `vendors.txt`, cached SERP | phrases without a peer to review | free |
| `competitors` | cached SERP | `competitors.md`, `synonyms.txt` | free |
| `mine` | cached SERP, public competitor pages | `competitor_mining.md`, `synonyms.txt` | no provider quota |
| `synonyms` | `synonyms.txt` | checked phrases | Yandex Wordstat |
| `exact [--yes]` | kept phrases | `!W` and phrase frequency, provider task receipts | Arsenkin limits |
| `report`, `excel`, `export` | the store | `report.md`, `clusters.csv`, `<project>_semantic_core.xlsx`, `pool.csv`, `pool.json` | free |

## Accounting and recovery

Provider clients record every charge in the shared spend journal (`seohead spend-report`); a
semantic stage tags its entries with `semantics_project` and `semantics_stage`. The project
database keeps provider units per stage (`runs`) and durable receipts: Wordstat attempts, SERP
fetches and Arsenkin tasks are reserved before the paid call, so an interrupted or ambiguous
request is never resubmitted automatically, and a paid Arsenkin result is fetched again by its
`task_id`.

## Filters

`preset` in `project.yaml` selects the topic filters (`generic` or the example `seo` preset);
`intent`, `stop` and `info` regular expressions override it for the project. A phrase with
observed impressions or positions is never cut by frequency rules.

## Example

`docs/examples/semantics/seohead-tech/` is a synthetic project for an SEO-tool topic: seeds, a
catalog and a few dozen phrases with invented base frequencies. It runs every free stage offline
and is exercised by `tests/test_semantics_cli.py`.
