# SEOHEAD Desktop — preparation skeleton

Native PyQt5/Qt Widgets shell, version0.1.0. This is D0 preparation, not the full desktop product.

## Run

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -e ".[build]"
.venv/bin/seohead-desktop
```

On Windows use `.venv\Scripts\python.exe` and `.venv\Scripts\seohead-desktop.exe`. Runtime on Windows/Linux is not yet verified. `requirements-macos-arm64.lock` records the measured Mac development environment; it is not a universal OS lockfile.

## Available now

- Canonical brand tokens in `theme/tokens.json`, applied through `theme/theme.qss`.
- Packaged local Roboto and Material Symbols with upstream licensing/source manifest.
- Model-backed demo URL table, filtering/sorting, selection detail, tabs and collapsible split panels.
- Read-only existing-core project-open seam on a worker. Configure `--core-cli <seohead executable>`; demo rows are removed when a real project is loaded. Real URL queries and full project projections remain unconnected.
- Preview-only scan setup, with no dispatch/network/provider calls.
- Native Qt PNG capture and SVG export: `--capture evidence/shell.png --export-svg design/qt-skeleton.svg --no-settings`.

## Figma input

`design/source/Untitled.fig` is preserved verbatim. ZIP/fig-kiwi inspection found a326-byte message with Document/Page1/InternalOnlyCanvas labels and a black400pxthumbnail. It was not fully decoded or edited in a Figma client. `design/qt-skeleton.svg` is an SVG exported from the actual Qt widget rendering, suitable for importing into Figma; it is not a newly created native.fig document.

## Checks and packaging

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/python -m PyInstaller --noconfirm --onedir --windowed --name "SEOHEAD Desktop" --osx-bundle-identifier tech.seohead.desktop --collect-data seohead_desktop scripts/entrypoint.py
```

Mac arm64 native Cocoa launch, AX search/inspector, four Qt smoke tests and real CLI project metadata reading passed. Evidence is in `evidence/preparation-acceptance.json`. No live site was scanned. Five visible rows are labeled synthetic demo data. No million-row/large-crawl readiness or Windows/Linux acceptance is claimed.

Next work: replace demo provider with bounded core URL query/detail and project/task/inbox adapters; finish job controls, durable notes and platform packaging/latency/memory gates. Avoid new crawler/analyzer/task stores. Use the supplied text TZ.

PyQt5 uses GPLv3/commercial distribution terms; this private preparation does not establish a distribution license decision. Google font/icon assets retain their original notices. No paid purchase or external deployment was performed.
