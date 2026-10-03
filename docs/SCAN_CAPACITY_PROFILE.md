# Native scan capacity profile (#815)

This is an opt-in, offline measurement harness for the existing `scan.v1` writer.
It neither raises the crawler's URL ceiling nor asserts that a million-URL crawl
works. Run each stage in a fresh process; the reported peak RSS belongs to that
stage's process. Use synthetic `example.test` URLs only. No socket, DNS, browser,
provider or paid API is used.

## Declared budgets and profiles

Declare the budgets before a run and retain the command, JSON output, stderr,
artifact and source SHA-256 outside this repository. The defaults are 900 seconds
per process, 2048 MiB peak RSS, 8192 MiB database plus WAL/shm, and at least
32768 MiB free disk. A budget failure is `status=blocked`, never a passed result.
The build stage checks those limits every 256 accepted pages. The read, inspect,
snapshot and integrity stages report elapsed time and peak RSS but currently do
not enforce an independent process watchdog; callers must give each a timeout.
`disk_bytes` names the database, WAL and shared-memory files at the end of build;
it is not a system-wide temporary-disk high-water mark.

The fixture controls page count, links per page, retained static HTML bytes and
retained rendered DOM bytes independently. A zero body/DOM size is the sparse
metadata profile. A nonzero DOM size requires static body capture. Generated
HTML contains deterministic unique hex data to avoid deduplication making a
large corpus look small. It is synthetic storage data, not a browser rendering
measurement.
Run the stages **sequentially** on an artifact. Concurrent read, full inspect
and snapshot processes were observed to produce transient `cannot read scan:
interrupted` refusals on a dense 10k file; each stage passed when rerun alone.
This profile does not claim a concurrent-reader capacity.

```bash
SCAN="$OUT/scan.sqlite"
python scripts/profile_scan_capacity.py build --scan "$SCAN" --pages 10000 \
  --links-per-page 3 --body-bytes 4096 --dom-bytes 4096 \
  --max-seconds 900 --max-rss-mib 2048 --max-disk-mib 8192 --min-free-mib 32768
python scripts/profile_scan_capacity.py read --scan "$SCAN" --pages 10000
python scripts/profile_scan_capacity.py inspect --scan "$SCAN" --pages 10000
python scripts/profile_scan_capacity.py snapshot --scan "$SCAN" --pages 10000
python scripts/profile_scan_capacity.py integrity --scan "$SCAN" --pages 10000
```

To measure recovery while filling, run a build with `--interrupt-after N` at a
committed page boundary. Exit 75 intentionally skips Python cleanup, modeling
process loss. Repeat the same build command without that option to reopen the
same artifact, recover any inflight lease and continue. Keep both logs and check
`pages_before`, `pages_after`, `resumed`, `finished` and the independent inspect
result. A retained complete page count with `finished=false` is **blocked**:
storage survived, but finalization did not pass.

## Current admission boundary

The effective crawler configuration refuses `limits.max_urls > 50,000` before
creating a scan. `NativeScan.create` and `NativeScan.inspect` both validate that
configuration. Consequently, 100k and 1M cannot yet pass the real writer's
build/reopen/recovery stages. This harness records their refusal; it does not
monkeypatch the cap, seed raw SQL rows into an operational artifact, or claim a
million-page result from an unrelated SQLite table. The optional
`--experimental-synthetic` flag requests the separately versioned
`storage.capacity_profile=experimental_synthetic` gate proposed in #818. A
build without that implementation still refuses the request. The marker is
recorded in scan configuration/fingerprint, admits only a direct synthetic
NativeScan writer/reader, and never makes public crawl collectors accept more
than 50k. Even when admission passes, this profile must still prove every stage
within its declared budgets before any capacity claim.

Existing `docs/SQLITE_ACCEPTANCE.md` also records a 64 MiB saved-audit limit and
a 50k profile timeout. Capture storage, audit/report generation and actual
network crawling have separate acceptance boundaries. A successful stage here
does not prove the later stages or a million-URL product capacity.

## Exploratory local result, not release acceptance

This working-tree run used source revision
`1a5825b72874e88cf7ef71eb2e8e9616124a5f3e`, profiler SHA-256
`43c43d187206b4eca9beddf1b0f4ae78d9cd9318fe9ecc4d792508eb5a000d5c`,
`source_dirty=true`, macOS arm64, Python 3.14.6, SQLite 3.53.3, 16 GiB physical
RAM and 68.34 GiB free disk before the run. The declared limits were 900 s per
stage, 2048 MiB peak RSS, 8192 MiB database/WAL/shm and a 32768 MiB free-disk
reserve. These values identify one reproducible experiment, not a supported
product size. The retained JSON, stderr, artifacts and budget manifest belong
outside the public repository.

| Fixture and stage | Result | Wall | Peak RSS | Database / snapshot |
|---|---|---:|---:|---:|
| 10k sparse: build | finished, 10,000 pages | 26.569 s | 234.28 MiB | 5,402,624 B |
| 10k sparse: read / inspect / snapshot / integrity | all passed sequentially; integrity and foreign keys `ok` | 0.285 / 0.295 / 0.557 / 0.034 s | 233.28 / 233.48 / 238.38 / 227.98 MiB | snapshot 5,402,624 B |
| 10k dense: build, 3 links/page, 4 KiB static HTML + 4 KiB rendered DOM | finished, 10,000 pages | 425.780 s | 237.94 MiB | 65,740,800 B |
| 10k dense: read / inspect / snapshot / integrity | all passed sequentially; integrity and foreign keys `ok` | 9.158 / 36.929 / 16.583 / 0.439 s | 236.89 / 228.50 / 236.22 / 227.42 MiB | snapshot 65,740,800 B |
| 50k sparse: intentional process loss after 25k | retained 25,000 pages, lifecycle `running` | 262.442 s | 236.47 MiB | 16,674,816 B DB + 4,202,432 B WAL |
| 50k sparse: resume | **blocked** at 900 s, 43,264/50,000 pages; lifecycle still `running` | 900 s ceiling | unreported for blocked process | 24,240,128 B retained DB |
| 50k sparse partial: read / inspect / snapshot / integrity | 43,264 rows readable and structurally valid; snapshot saved; integrity and foreign keys `ok`. Capture is **not finished** | 1.983 / 1.338 / 3.396 / 0.124 s | 236.94 / 234.41 / 247.27 / 228.36 MiB | snapshot 24,240,128 B |
| 50k dense: build, 3 links/page, 4 KiB HTML + 4 KiB DOM | **blocked** at 900 s, 12,032/50,000 pages; lifecycle `running` | 900 s ceiling | unreported for blocked process | 84,025,344 B retained DB |
| 50k dense partial: read / inspect / snapshot / integrity | 12,032 rows readable and structurally valid; snapshot saved; integrity and foreign keys `ok`. Capture is **not finished** | 10.982 / 13.014 / 19.632 / 0.116 s | 236.72 / 233.48 / 247.16 / 228.36 MiB | snapshot 84,025,344 B |
| 100k and 1M admission | **blocked** before artifact creation by the stable 50k crawler-config ceiling | — | — | none |

The 50k process-loss exercise proves that committed rows survived and the writer
continued from 25,000 to 43,264 pages. It does **not** prove completed recovery
or 50k capacity: the declared wall budget stopped the continuation. The 50k
dense run likewise stopped by wall time, rather than an RSS or disk limit. The 10k
dense time includes static-body and rendered-DOM commits; it must not be
compared with the sparse row cost as if link density alone changed. The current
record has no 50k, 100k or 1M completed writer result, no raw/rendered
1M corpus result, and no audit/report capacity result. An adjacent versioned
large-audit companion, if used, has separate disk and snapshot costs not measured
here.
