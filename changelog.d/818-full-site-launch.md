- Native SQLite site scans accept `max_urls=0` to collect until the queue is exhausted,
  and `max_depth=-1` to disable the depth limit. Time/HTTP budgets remain separately
  configurable, storage guards and interruption/resume remain active, and legacy
  materialized routes refuse an unlimited URL population.
- Sitemap-only scans also preserve the disabled URL limit instead of refusing
  their first declared member when the configured limit is zero.
- Explicit finite request rates above the default 2 requests/s now reach the
  shared project-origin pacing lane as well as the per-scan limiter. Parallel
  scans still reserve one common host schedule instead of multiplying its rate.
