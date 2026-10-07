# SEOHEAD QA site

`qa_site.py` is a controlled, standard-library website for one small native
SEOHEAD capture and repeatable offline reanalysis. It contains only synthetic
content and binds only to a loopback address. It is not a crawler, web console,
or a replacement for the SEOHEAD CLI/MCP.

The default `broken` profile intentionally contains concrete faults. `clean`
is the fixed comparison corpus; `fix-delta` currently has the same served
routes as `clean` and exists as an explicit profile name for before/after
receipts. The source has no hard-coded machine path or public origin.

## Start a bounded local fixture

```bash
QA_SITE_DIR=examples/qa-site
RUNTIME_DIR=/absolute/local/demo-runtime
mkdir -p "$RUNTIME_DIR"
python3 "$QA_SITE_DIR/qa_site.py" serve \
  --profile broken --host 127.0.0.1 --port 0 --lifetime-seconds 180 \
  --pid-file "$RUNTIME_DIR/runtime.json" --ready-file "$RUNTIME_DIR/ready.json" \
  > "$RUNTIME_DIR/server.log" 2>&1 &
```

Read `ready.json` for the exact PID and port. The process owns only that
loopback socket. It ends at the chosen lifetime or after `SIGTERM`; do not kill
processes by a broad name. `--host` rejects non-loopback addresses. `--port 0`
avoids collisions. `/optional/slow` has no delay unless `--slow-seconds` is
explicitly set (allowed values are bounded at five seconds); it and the 429
route are optional retry/timeout cases, not defaults for routine captures.

The core deliberately treats private targets as opt-in. For an owned fixture
only, authorize precisely its host in the command environment and use the
existing CLI:

```bash
export SEOHEAD_ALLOW_PRIVATE_HOSTS=crawl.localhost,127.0.0.1
seohead project-new --directory "$RUNTIME_DIR/project-broken" --target "http://crawl.localhost:PORT/"
seohead crawl-site --project "$RUNTIME_DIR/project-broken" --url "http://crawl.localhost:PORT/" --max-urls 40
```

Replace `PORT` with the value from `ready.json`. Keep the generated project,
scan SQLite, command transcript, source manifest, catalogue, settings, and
artifact hashes in a private local demo-data directory. Generated databases and
reports do not belong in this source package or public Git.

## Receipt inputs and offline reuse

Before capture, materialize the exact fixture identity and expected facts:

```bash
python3 examples/qa-site/qa_site.py manifest --profile broken --output "$RUNTIME_DIR/source-manifest.json"
python3 examples/qa-site/qa_site.py catalogue --profile broken --base-url "http://crawl.localhost:PORT" --output "$RUNTIME_DIR/known-ground-truth.json"
shasum -a 256 "$RUNTIME_DIR"/*.json "$RUNTIME_DIR"/*.sqlite > "$RUNTIME_DIR/artifact-sha256.txt"
```

The retained SQLite artifact is the read-only input for `scan-reanalyze`,
reports, exports, and comparison commands. Routine tests inspect fixture routes
and generated manifests only; they never start a crawler. Start a new local
server and make a new capture only after an explicit fixture/profile change.

## Scenario catalogue

The generated catalogue declares paths, exact expected facts, and whether a
scenario is active in the selected profile. The broken profile covers status
404/410/503, a 301→302 chain and a redirect loop, robots plus a sitemap index
with duplicate/noncanonical/missing entries, `noindex`, canonical chain and
hreflang mismatch, duplicate title/meta/H1/body, thin and no-`main` pages,
internal/external/nofollow/anchor links and a sitemap-only orphan, image alt,
resource, and dimensions faults, valid and malformed JSON-LD, cache/privacy
headers, and bounded optional 429/slow responses.

`/broken/js-rendered` has a raw marker, a JavaScript replacement marker and an
intentional console error. Those facts require an already configured existing
JS renderer; a raw capture must record them as unavailable rather than claim
they were checked. The fixture does not claim that every provider-dependent or
browser-only check can be proven offline.
