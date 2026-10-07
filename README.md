# SEOHEAD Desktop

Native PyQt5/Qt Widgets desktop adapter for the local SEOHEAD core. It is not a
second crawler, task store, or HTTP service: collection, retained SQLite
evidence, project state, reports and policy validation stay in the co-shipped
core.

The current development baseline is compatible with SEOHEAD core
`3c60d0c051e282603188cef0f70341d1a5d8ad72`. An external development core must
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

| Area | Current behaviour |
| --- | --- |
| Project observer | Retained progress, task page/detail, scan history, activity and durable inbox load through one persistent local MCP session. Reads are bounded and generation-scoped. |
| URL evidence | A retained scan supplies paged URL rows, a redacted URL detail, scan status and bounded inlinks. Selecting another project or scan clears previous evidence before the next result arrives. |
| Native scan plan | The user explicitly confirms a project-bound native crawl. The dialog shows URL, HTTP-request and time budgets plus raw/JS mode. Opening or refreshing a project never starts a crawl. |
| Local run control | Desktop owns only QProcesses it started. It supports up to three queued native runs and records separate local output per run. Stop targets the selected owned run; other runs and work started outside Desktop are left alone. |
| Progress | While an owned run is active, `project-observe` is polled at 0.5 seconds. Core run identity is keyed by project UUID plus run ID; PID state is diagnostic only. |
| Resume | Resume is offered only after core reports a retained scan with `source.lifecycle: interrupted`; Desktop keeps project binding and never changes stored scope/configuration. |
| Notes | A note or proposed goal is stored only after an explicit click and revision-aware core confirmation. It never launches a scan or injects chat text. |

Each native project run is rate-limited to 0.5 URL/s from Desktop while core
enforces the shared per-origin gate. The core remains the authority for final
admission, stored settings, scope, authentication and evidence completeness.

## Verification

```bash
QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
SEOHEAD_DESKTOP_CORE_CLI=/absolute/path/to/seohead \
  QT_QPA_PLATFORM=offscreen .venv/bin/python -m unittest discover -s tests -v
```

The real-core gate creates only owned `crawl.localhost` loopback projects. It
verifies three concurrent bounded captures, selecting/stopping one owned run,
and resuming its retained interrupted artifact while the other captures remain
independent. No public site, provider, cloud account or paid API is used.

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
