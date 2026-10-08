# Design v2 — SEOHEAD Desktop

The target look and behaviour of the desktop app. **These HTML files are specifications, not
the product**: the app is built only with Python, PyQt5 widgets, layouts and QSS. Each board
shows what a Qt screen must look like and how it must react to clicks.

Live, clickable version: the Design canvas (owner's link). Source files here are the same boards.

| File | Purpose |
|---|---|
| `canvas/*.dc.html` | 38 boards (incl. simple display, `seohead watch`, brand/icon): start, projects, inbox, reports to executors, new scan (site / sitemap / URL list / Screaming Frog), scans & live observer, log, URL inspector (A: details below, B: details right), issues, HTML search, compare before/after, methods, compact 800×800, ⌘K, new tab, tab settings, crawler mode, installer & first run, CLI, themes & language, loading states, colours & 17 statuses, component library, icon library, notifications, menus, all 20 modals, settings, motion, do/don't guide |
| `canvas/TopBar.dc.html`, `SideNav.dc.html` | Shared shell components used by the screens |
| `canvas/assets/app.css`, `ext.css` | Every colour, size and state used by the boards — the reference for `theme/tokens.json` + QSS |
| `canvas/canvas.json` | Board layout of the canvas |
| `DESIGN-SYSTEM.ru.md` | Design system (Russian): tokens, components → QSS properties, screens → core data, gaps |
| `READINESS-2026-10-09.ru.md` | How ready the current code is for v2, area by area, with file:line references and order of work |
| `brand/icon-*.svg` | App icon variants: spider + search (A recommended) |
| `qa/clickthrough.py`, `qa/scenarios.py` | Playwright checks: every board renders without errors, no dead links, 28 behaviour scenarios pass |

## Porting rules (canvas → Qt)

1. Colours/sizes come from `theme/tokens.json` only; QSS is generated from it; no hex in Python.
2. Variants via dynamic properties (`role=primary`, `badge=warn`, `status=stale`) and
   `unpolish/polish` on the changed widget — never re-set the app stylesheet.
3. Tables are `QAbstractTableModel` + delegates (badges are painted, not widgets per cell).
4. Icons: Material Symbols Outlined as local SVG with licence notice; custom icons on the same 24 px grid.
5. Motion ≤ 120 ms via `QPropertyAnimation`; reduce-motion → 0 ms; no animation without new data.
6. Every board element maps to a real core command or is shown as "not measured" with a reason.

Run the QA scripts: `DC_RUNTIME=/path/to/dc-runtime.js python desktop/design/v2/qa/scenarios.py`
(the canvas runtime is not committed; it belongs to the canvas type).
