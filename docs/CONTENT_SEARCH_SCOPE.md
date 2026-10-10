# Content search: progress, cancel and partial results — scope

Status: scoping note for the Desktop core-gaps epic (#921, item D). It records the requirement
and the boundary. It is not a design and adds no code. Related: `scan-content-search` in
[CLI.md](CLI.md) and [TOOL_REFERENCE.md](TOOL_REFERENCE.md).

## Current state

- `seohead/storage/content_search.py:search_scan` reads one finished retained scan offline. It
  never fetches a URL and treats a missing or corrupt body as unknown, not as absence.
- `seohead/mcp/handlers.py:scan_content_search` writes a complete local NDJSON package with a
  fixed-width offset index. It takes an optional `progress` callback that receives a count.
- `scan_content_search_page` reads at most 100 derived records per call.
- A partial source publishes an honest partial package. It never publishes an all-clear.
- There is no cancel. The call runs to the end of the scan or raises.

## What Desktop needs

1. Progress that a UI can show while a long search runs.
2. A cancel that stops the search between records and leaves no half-published package.
3. Partial results that can be read before the search finishes.

## Boundary

These three items are not part of the epic #921 code. They need a separate design decision
first, and the content-search surface is not touched by the other #921 children.

Open questions for the maintainer:

- Is a dedicated search index needed, or is a streaming scan over retained bodies enough?
- Should cancel be a flag in a package-state file, the same cooperative pattern as run stop?
- Should a partial package be readable while the package is still being staged?

Until these are answered, the only supported behavior is the existing one: a finished search
package, optional progress count, no cancel.
