# SEOHEAD Desktop

Native PyQt5/Qt Widgets desktop adapter for the local SEOHEAD core. It is not a
second crawler, task store, or HTTP service: collection, retained SQLite
evidence, project state, reports and policy validation stay in the co-shipped
core.

The current development baseline is compatible with SEOHEAD core
`ceb2eab716c0077355d96b3a99f92ea1eb0bc0b6`. An external development core must
be a clean checkout; a frozen bundle verifies its manifest, executable digest
and producer commit before it supplies `--producer-build`.

## Run from source

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[build]"
.venv/bin/seohead-desktop --core-cli /absolute/path/to/seohead
```

The application starts one local stdio MCP connection to the supplied core.
It does not open an HTTP listener. A project is opened explicitly from disk.

## Functional scope

User-facing interfaces are the CLI/terminal and this native PyQt5 application.
Local stdio MCP serves AI agents. The browser/web console was cancelled by the
owner on 2026-10-07 and is removed from the roadmap.

| Area | Current behaviour |
| --- | --- |
| Project observer | Retained progress, task page/detail, scan history, activity and durable inbox load through one persistent local MCP session. Reads are bounded and generation-scoped; each operation keeps one in-flight request and one latest pending intent. |
| URL evidence | A retained scan supplies paged URL rows, a redacted URL detail, scan status and bounded inlinks. Selecting another project, scan or URL clears previous evidence before the next result arrives. Observer refreshes preserve the selected retained scan. |
| Native scan plan | The user explicitly confirms a project-bound native crawl. The dialog shows URL, HTTP-request and time budgets plus raw/JS mode. Advanced settings use the core descriptor and existing typed JSON CLI input, including scope patterns, content selectors and request delay. Opening or refreshing a project never starts a crawl. |
| Local run control | Desktop owns only QProcesses it started. It runs up to three native children concurrently, queues later requests and records separate local output per run. Stop targets the selected owned run. Closing cancels queued work and waits for owned children to save checkpoints; work started outside Desktop is left alone. |
| Progress | `project-observe` is polled at 0.5 seconds while the selected project has active children or awaits a final core status. A child exit alone cannot claim completeness. A missing terminal record is explicitly unavailable after 10 seconds; project UUID plus run UUID is the identity, including after PID exit. |
| Resume | Resume is offered only after core reports a retained scan with `source.lifecycle: interrupted`; Desktop keeps project binding and stored scope/configuration, and supplies a fresh observer UUID for each resumed attempt. |
| Notes | A note or proposed goal is stored only after an explicit click and revision-aware core confirmation. It never launches a scan or injects chat text. |

Desktop preserves the configured minimum request delay; its editor admits
a delay of at least 0.5 seconds. Core enforces the shared per-origin gate. The core remains the authority for final
admission, stored settings, scope, authentication and evidence completeness.

## Verification

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
SEOHEAD_DESKTOP_CORE_CLI=/absolute/path/to/seohead \
  QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

The real-core gate creates only owned `crawl.localhost` loopback projects. It
verifies three concurrent bounded captures in one MainWindow/project over MCP,
selecting two retained artifacts, stopping/resuming with a fresh run UUID, typed
Advanced settings in actual scan evidence, and closing with two active children
plus queued work while an external crawl remains alive. No public site, provider, cloud account or paid API is used.

## Packaging and platforms

`scripts/build_macos.sh` builds an application plus an exact core payload and
writes a provenance manifest. See [docs/packaging.md](docs/packaging.md) for
the required build arguments and clean-machine smoke check.

macOS is the only runtime currently exercised. Windows and Linux compatibility,
large-crawl/million-row readiness and final product design are not claimed.

## Design input

`design/source/Untitled.fig` is preserved as the original input. The Qt
component catalogue is in `design/tab-contracts.md`; it provides native bounded
tables and panels without a web view or Electron runtime.
