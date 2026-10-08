# Retained explicit URL-list scans

Use an explicit list to inspect exactly the supplied URLs, including multiple
hosts. List mode preserves trimmed input strings and first-occurrence order;
Blank entries and overlong URLs retain their existing exclusion policy; input
coverage counts accepted URLs, duplicates, blank entries and length exclusions.
It does not normalize distinct query strings or add hyperlinks, sitemap members,
redirect destinations or canonical targets as page rows.

```sh
seohead crawl-site --urls-file input.txt --scan-out list.seohead --max-urls 1000000 --set limits.max_requests=2000000
seohead scan-inspect --scan list.seohead --limit 20
seohead crawl-site --resume list.seohead
```

Native list capture uses the same SQLite frontier, page writer, typed response
capture, audit.v2 representation and offline readers as site scans. It delegates
HTTP, parsing and optional redirect/canonical diagnostics to `collect_urls`.
Input batches, one active page, and bounded response captures remain in Python;
the full input, page population and per-origin robots cache live on disk.

`--scan-out` supports static list capture with the shared native URL budget.
An explicitly larger legacy `--out-dir` list uses `.list.seohead` in that
directory as its durable spool. Its JSONL includes the complete retained page
population, while the directory `audit.json` bridge keeps its existing page
materialization boundary and reports `audit_available=false` above it. Earlier
audit/task files are retained under `.stale-*` names when that bridge is unavailable.
The eager Python API and small legacy directory route retain their separate
50,000-page safety bound. Admission is not a million-page performance result.

Native list capture requires cache off. It refuses JavaScript rendering and
resource fetching rather than silently ignoring those requested operations.
List mode retains no link-edge/form observations, so graph/form checks remain
explicitly unavailable during initial analysis and later reanalysis. Retained
HTML can still be reparsed offline without making the input into a site crawl.

Before collection, input identity is frozen while its URLs are inserted in
bounded SQLite batches. A changed supplied list cannot resume that capture.
Once preparation completes, `--resume` uses the stored input, settings and
producing revision; it does not need the original exchange file. Interrupted
input preparation itself is not resumable: the incomplete artifact is retained,
and a new output is required. This is separate from collection resume.

All page, robots and optional destination requests share the request budget.
Completed destination hops remain per-row evidence, even when the next hop
cannot be requested. Resume retains spent requests, known elapsed time and
committed page order. A finite elapsed budget left active by an abruptly killed
worker remains unavailable across repeated resume attempts. It is never granted
a fresh allowance. JSONL resume atomically restores the complete saved prefix
before appending new pages.

TXT and CSV inputs are iterators; URL matching does not allocate a second list
for each cell. Physical text lines above 8,388,608 characters are refused by
name. XML releases completed elements, and XLSX keeps the existing read-only
workbook reader. Input-format parity and tiny collection/resume fixtures are
covered separately from RSS and density measurements. Large TXT/CSV, complex
XLSX/XML records, dense bodies and a million-page collector still require their
own source-bound resource acceptance; this document does not claim those runs
have passed.
