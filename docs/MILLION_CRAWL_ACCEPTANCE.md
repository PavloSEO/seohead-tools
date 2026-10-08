# Native one-million-URL acceptance

Native SQLite site scans admit an explicit `limits.max_urls` from 1 to 1,000,000.
CLI, local MCP, native capture, the optional remote submission schema and its
worker validate the same ceiling. Values above it fail before collection.
`limits.max_requests` is independent and may be set up to 2,000,000: one million
pages needs additional attempts for robots, sitemap shards, redirects and retries.
The default remains 200 URLs, 20,000 requests and at most two requests per second.
Remote project defaults remain 10,000 URLs and 20,000 requests; the operator must
explicitly authorize a higher project budget. Render, elapsed-time and disk limits
still apply independently. No provider or paid call is enabled by higher admission.
Remote duration can be explicitly authorized up to 14 days (1,209,600 seconds),
with the one-hour default preserved. The operator's project duration and per-origin
request limits must also admit the planned workload; the submission cannot raise them.

This configuration contract does **not** establish completed runtime capacity.
Issue #818 remains open until the source-bound stages below pass. Historical
direct-storage and partial 50k receipts retain their original, narrower scope.

## Route and representation bounds

- Native site/sitemap capture and audit.v2 stream through SQLite. Every page,
  response, document, body, link, form and sitemap member must be conserved.
  The consumers gate runs SQLite integrity/foreign-key checks and reads back a
  public snapshot with exact native and audit collection counts.
- Materialized legacy list and eager spider APIs retain an explicit 50,000-URL
  safety ceiling. Their audit bridge refuses above 10,000 pages, 20,000 forms or
  1,500,000 links and retains collection evidence. They do not inherit native
  capacity results. The complete streamed one-million-list route is a separate
  acceptance gap, not a successful part of this gate.
- Tasks expose bounded URL examples with exact omitted counts. Their check and
  occurrence totals must still equal the complete source audit.
- CSV scan export and CSV reports contain every requested row. XLSX scan export
  partitions sheets at Excel's 1,048,575 data-row limit; the gate reads every row
  back and checks all partition ranges. Selected XLSX fields do not claim to carry
  the complete group-member or nested evidence inventory.
- Ordinary audit XLSX/PDF and nested group-member representation retain their
  own format bounds and acceptance in #759/#816. Oversized cells must fail,
  never truncate. A bounded PDF overview requires `pdf_policy=overview-v1` and
  complete machine-readable companions.
- The compatible `experimental_synthetic` storage marker remains refused by
  live collectors. Old saved configuration fingerprints remain readable.

## Run the owned fixtures

Use a clean, frozen checkout and an existing environment with the reports extra.
Set `OUT` to a new directory outside the checkout. Run one heavy writer at a time.
The script records the source commit and hashes of the modules actually imported.
Each requested stage runs in a fresh subprocess, with its stdout/stderr, watchdog
receipt, operating-system peak RSS and retained artifacts. Temporary report and
SQLite spools stay under the measured stage directory.

```bash
python scripts/accept_million_crawl.py --out "$OUT/native" \
  --stages 50000,100000,1000000 --shard-size 50000 --interrupt-after 25000 \
  --links-per-page 3 --forms-per-page 1 --h1-families 1 --body-profile catalogue-v1 \
  --body-padding-bytes 2048 --comparison-compression gzip \
  --max-seconds 1800 --max-rss-mib 4096 --max-disk-mib 32768 --min-free-mib 12288
```

This command uses `httpx.MockTransport` at a reserved `.test` origin. It exercises
the actual dispatch, robots, sitemap parsing, page parsing, capture, storage and
audit code; it does not populate databases with synthetic SQL rows. Its synthetic
clock configuration does not modify normal crawler defaults. The representative
HTML includes distinct titles/descriptions/canonicals, headings, links, forms,
catalogue tables and varied content; deterministic defects produce real findings.
The default single H1 family puts every page into one duplicate group, so the
one-million stage also exercises a full one-million-member group. A 101-family
variant remains available for comparison with preserved historical fixtures.

The watchdog stops a worker on wall-time, RSS, disk, free-space or unavailable
telemetry. It first requests interruption, then terminates a worker that does not
stop; every artifact is preserved. A blocked or failed stage is not acceptance.
RSS telemetry covers the stage worker process; this raw-HTML fixture starts no
browser tree. Windows watchdog code is present but its runtime capacity is not
established by macOS receipts.

Guarded HTTP is a separate, explicit small run. It binds only `127.0.0.1`, first
proves rejection without a private-host allowance, then permits that exact host
and verifies the default two-request-per-second pacing. The owned server stops
when the run finishes; no public or customer host is contacted.

```bash
python scripts/accept_million_crawl.py --out "$OUT/loopback" --loopback-only
```

If a prior producer passed but consumers failed, retry only the consumers. This
validates the retained synthetic source without recollection; the selected recheck
captures exactly one fresh synthetic page and tests both stale and fresh evidence.

```bash
python scripts/accept_million_crawl.py --input-scan "$SAVED_SCAN" \
  --out "$OUT/consumer-retry" --comparison-compression gzip
```

Keep the original producer revision, all failed receipts and pre/post source
hashes. A later consumer retry has its own revision and cannot relabel the original
run as successful. `--skip-consumers` proves only capture/audit, never the full
workflow. Real-site crawls, paid calls and deployment are outside this gate.
