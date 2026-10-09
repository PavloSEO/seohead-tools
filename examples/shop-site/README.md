# Shop test site (local loopback bench)

`shop_site.py` serves a deterministic furniture shop («Мебельный магазин», Russian content)
over loopback in three versions. Each version is a later state of the same site, so three
native crawls of v1 → v2 → v3 form a project history where every planted defect can be
traced: introduced, fixed, regressed, or removed together with its page.

All content is synthetic (invented products and texts). The server binds loopback only,
makes no outbound requests, and loads no analytics: tag-manager ids are passive JSON literals
used as Search-in-HTML markers.

| Version | Crawled URLs (`urls_crawled`) | Products | Blog posts | Active planted defects |
|---|---|---|---|---|
| v1 | 1 248 | 1 110 | 36 | 44 |
| v2 | 1 314 | 1 149 | 60 | 26 |
| v3 | 1 330 | 1 163 | 70 | 8 |

The counts are what the native crawler records with the configuration below (HTML pages,
redirects and errors; images are not fetched as URLs; robots-blocked URLs are excluded).
`python shop_site.py urls --version v1` prints the expected frontier with a `page`,
`resource` or `robots-blocked` tag per URL.

## What evolves

- **v1** — baseline with 44 planted defects across statuses, redirects, canonicals,
  indexability, titles/meta/headings, content, links, hreflang, images, structured data,
  speed and robots/sitemaps. Blog posts are thin (~150 words).
- **v2** — about 55 % of v1 defects fixed; new «Дизайн интерьера» blog section (24 posts) and
  45 new products; six discontinued products removed (three answer 404 and are still linked
  from an old post and listed in the sitemap, three 301 to their category); new problems:
  over-long descriptions in the new section, two thin drafts, a lorem-ipsum product.
  Blog word count grows to ~420, posts link products.
- **v3** — almost everything fixed; 301s added for the 404 products (one through a temporary
  `/tovar/` hop → redirect chain); regressions: `noindex` back on `/garantiya/`, broken
  JSON-LD on `kuhnya-001`, chair products canonicalised to the category, slow blog index.
  Blog posts grow to ~650 words and link related posts.

The full list with ids, expected check ids, URLs and per-version state lives in
`DEFECTS` inside `shop_site.py`; `ground_truth.json` is generated from it:

- `versions.<v>.urls` — every expected URL with its status and active defect ids;
- `versions.<v>.expected_urls_crawled` — the crawl size above;
- `defects.<id>.states` — `introduced` / `persists` / `fixed` / `regressed` / `absent` per version.

Regenerate after any change: `python shop_site.py ground-truth`.
Showcase URLs per app screen: [COVERAGE.ru.md](COVERAGE.ru.md).

## Serve

```bash
python examples/shop-site/shop_site.py serve --version v1 --port 18431 \
  --ready-file /tmp/shop-ready.json --lifetime 1800
```

Prerequisite — map the canonical host to loopback once:

```bash
echo '127.0.0.1 shop.example.test' | sudo tee -a /etc/hosts
```

Canonicals, hreflang and sitemaps use the absolute origin `http://shop.example.test:18431`
(`--base` overrides it), so keep one fixed port across versions: the three scans then share one
site identity. `--port 0` picks a free port for ad-hoc use. SIGTERM stops the server;
`--lifetime` is a safety net. Without a hosts entry use the fallback
`--base http://shop.localhost:18431`: `*.localhost` resolves to 127.0.0.1 natively; replace the
host in the commands below accordingly.

## Reproduce the three-scan project

```bash
export SEOHEAD_ALLOW_PRIVATE_HOSTS=shop.example.test   # scoped private-host opt-in
P=/path/to/shop-project
seohead project-new --directory $P/project --target http://shop.example.test:18431/ --label "Мебельный магазин"
cat > $P/crawl-config.json <<'EOF'
{"limits": {"max_depth": -1, "max_urls": 5000, "max_query_variants_per_path": 20},
 "speed": {"min_delay_seconds": 0.02, "concurrency": 4},
 "resources": {"fetch": true},
 "sitemaps": {"auto_discover": true}}
EOF
for v in v1 v2 v3; do
  python examples/shop-site/shop_site.py serve --version $v --port 18431 --ready-file $P/ready.json --lifetime 1800 &
  SRV=$!; while [ ! -f $P/ready.json ]; do sleep 0.1; done
  seohead crawl-site --project $P/project --config $P/crawl-config.json \
    --scan-out $P/project/scans/scan-$v.sqlite --approve-large-crawl -q > $P/crawl-$v.json
  kill $SRV; wait $SRV; rm $P/ready.json
done
seohead compare-crawls --before $P/project/scans/scan-v1.sqlite --after $P/project/scans/scan-v2.sqlite --out-dir $P/compare-1-2
seohead compare-crawls --before $P/project/scans/scan-v2.sqlite --after $P/project/scans/scan-v3.sqlite --out-dir $P/compare-2-3
for v in v1 v2 v3; do seohead sf tasks --json $P/project/scans/scan-$v.sqlite --out $P/tasks-$v -q; done
seohead scan-content-search --scan $P/project/scans/scan-v1.sqlite --query GTM-K7OLD12 --scope raw_html --out-dir $P/search-v1
```

The defaults (`limits.max_depth` 5, `max_query_variants_per_path` 5, `speed.min_delay_seconds`
0.5) are polite settings for real sites; on loopback they would only truncate pagination and
slow the run. The remediation ledger (fix reported → recheck → resolved) currently needs the
Python API `seohead.storage.ledger.create_ledger` / `ingest_scan` before the
`remediation-*` CLI commands can be used.

## Tests

```bash
python -m unittest discover -s examples/shop-site -p 'test_*.py'
```

Checks determinism, that `ground_truth.json` is current, growth between versions, per-URL
defect consistency and the lifecycle (fixes and regressions present). No network.
