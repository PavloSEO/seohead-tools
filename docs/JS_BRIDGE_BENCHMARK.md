# Owned HTML and JavaScript bridge benchmark

The versioned profile in `examples/js-bridge-benchmark.v1.json` freezes the
synthetic acceptance corpus and budgets before measurement. It is an opt-in
local benchmark, not a claim about arbitrary large JavaScript sites.

The primary matrix has 72 cases: HTML and JavaScript, 32/80/160 pages, 8/64 link
occurrences per page, and three application-cold/warmed-worker pairs. Every
rendered page has 128 actual forms and approximately 16 KiB of serialized DOM.
The largest JavaScript case therefore crosses the former 20,000-form bridge
boundary with 20,480 forms. The raw JavaScript shell has no forms or anchors;
Chromium must execute its inline script to create them. Distinct fragments
preserve 64 link occurrences even in the 32-page corpus. Forms are never submitted.

`Origin` serves only owned loopback URLs. The ordinary shared `crawl-site`
handler performs collection, browser rendering, retention and the native audit.
Hooks observe phase boundaries and commits; they do not replace browser results.
The harness verifies ordered page/link/form evidence, selected DOM hashes,
retained counts, offline reanalysis equality, source immutability and bounded
query/export consumers. Browser engine provenance remains in each retained DOM.

One loopback port is selected and frozen for all pairs so repeated seeds keep
the same URL identity. Each worker must successfully bind that owned port before
collection; a port collision blocks the run. Each cold/warm pair has a fresh Python worker. The cold case is unprimed; the
warm case immediately repeats the same fixture in that worker with a new output
scan and fresh browser contexts. Native capture requires HTTP cache off, and
persistent browser profiles are unavailable. Thus these labels describe worker
and allocator warming; they do not claim warm browser caches or a flushed OS cache.

The largest dense JavaScript pair also runs recovery. A controlled
`KeyboardInterrupt` occurs after the 80th committed rendered document. Context
cleanup and the renderer's `finally` block persist elapsed time. Public resume
continues the same scan with the remaining finite 600-second internal budget.
The external watchdog accumulates both render intervals. The final evidence must
match the uninterrupted reference, with no repeated or missing rendered pages.
No budget is reset or increased to make recovery pass.

## Running the frozen profile

First coordinate a serial slot and choose the exact clean source revision. Do
not run alongside another capacity writer. Use an already installed browser;
`SEOHEAD_CHROME` may explicitly select its local executable. The harness never
installs software or changes platform trust.

```sh
python scripts/benchmark_js_bridge.py plan --out "$OUT/plan.json"
python scripts/benchmark_js_bridge.py smoke --out "$OUT/smoke" --source-revision "$REV" --execute
python scripts/benchmark_js_bridge.py run --out "$OUT/matrix" --source-revision "$REV" --execute
python scripts/benchmark_js_bridge.py page-cap --out "$OUT/page-cap" --source-revision "$REV" --execute
```

Every execution requires a new output directory and an exact clean revision.
The separate page-cap axis admits 10,001 raw pages but actually renders only 16;
its remaining 9,985 pages stay explicitly static. It does not prove 10,001 fully
rendered pages. The two-page smoke validates the harness and recovery mechanism,
not the benchmark's resource or scale acceptance.

## Measurements and failure rules

The supervisor records numeric process-tree snapshots from `ps` every 100 ms,
including the owned Python worker, Playwright driver and Chromium descendants.
A sampled aggregate peak is not the instantaneous maximum. Python high-water
RSS is reported separately. Phase events supply wall-time and self plus waited
child CPU deltas; sampled child CPU is a lower bound while a child is alive.

Frozen limits include 600 seconds for rendering, 1 GiB Python RSS, 2 GiB sampled
process-tree RSS, 8 GiB retained files per case, 16 GiB total retained benchmark
output and a 32 GiB free-disk reserve. The complete phase, CPU and growth limits
are in the profile. Small-to-large phase RSS growth is checked at fixed mode,
density, repetition and temperature; missing measurements cannot pass.

Budget overruns, missing dependencies, HTTP errors, missing forms, incomplete
DOMs and failed resume equality are blocked results. Artifacts and failure logs
remain available. Do not tune thresholds after observing a result. The result
must name its source, runtime, profile hash, fixture dimensions and limitations.
The broad #743 completion decision additionally needs its versioned capability
matrix and the other independent collection/recovery/consumer gates.
