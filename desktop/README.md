# SEOHEAD Desktop

Native desktop app for the SEOHEAD toolkit, written in **Python + PyQt5 (Qt Widgets) + QSS**.
No web view, no Electron, no JavaScript framework: every screen is a Qt widget, styled by one
QSS theme generated from design tokens. The app is a *presentation adapter* — crawling,
analysis, projects and tasks live in the core (`../seohead`) and are reached through the
same CLI/MCP contracts the agents use.

> **Status (2026-10-09): redesign in progress.** The code here is the first functional
> skeleton (project console, URL workbench, scan runner, MCP gateway). Its visual layer is
> being replaced by **design v2** in [`design/v2/`](design/v2/). Treat the current UI as
> outdated; treat `design/v2` as the target.

## What lives where

| Path | What it is |
|---|---|
| `src/seohead_desktop/app.py` | Main window: top bars, navigation, pages, wiring of signals (≈3.3k lines; to be split into modules — see design/v2 readiness report) |
| `src/seohead_desktop/ui/` | Reusable widgets: `components.py` (buttons, badges, panels), `workspace_tabs.py` (workspace tabs), `tabcatalogue.py` (SF-style tab/column contracts), `work_monitor.py` (scan observer), `crawl_configuration_dialog.py` (advanced scan settings), `content_search_panel.py`, `comparison_summary.py`, `help_guide.py`, `icons.py` (Material Symbols registry), `gallery.py` (component gallery for screenshots) |
| `src/seohead_desktop/theme/` | `tokens.json` (single source of colours, sizes, density, motion) → `theme.qss` |
| `src/seohead_desktop/models.py` | Qt table models (no widget per cell) |
| `src/seohead_desktop/mcp_gateway.py` | Talks to the core over local stdio MCP; allow-list of tools the app may call |
| `src/seohead_desktop/scan_manager.py`, `scan_runner.py` | Starts/observes scans owned by the app (QProcess), queue of up to 3 |
| `src/seohead_desktop/crawl_configuration.py` | Turns the core's `crawl-describe-settings` into validated controls |
| `src/seohead_desktop/comparison.py`, `content_search.py` | Compare two scans; search inside saved HTML |
| `src/seohead_desktop/local_control.py`, `control_cli.py` | Opt-in local control channel so an agent can drive the window (`seohead-desktop-agent`) |
| `src/seohead_desktop/bundle.py` | Locates the bundled core inside a frozen app |
| `tests/` | Offscreen Qt tests (`QT_QPA_PLATFORM=offscreen`) |
| `examples/qa-site/` | Local loopback SEO test site with profiles `broken`, `clean`, `fix-delta`, `tracking` — the only site real scans are tested on |
| `packaging/`, `scripts/build_*.sh/.ps1`, `SEOHEAD Desktop.spec` | PyInstaller bundle that ships app + compatible core + `seohead` CLI together |
| `docs/` | User guide, workspace/tab contracts, local control, packaging, source install |
| `ROADMAP.md` | **Owner decisions and order of work — start here** |
| `docs/spec/` | Russian TZ, requirements check, site design reference, prompts/hand-offs |
| `design/site-reference/` | Brand reference extracted from seohead.tech (CSS tokens, Badge/Chip) |
| `design/v2/` | **Target design**: interactive mock-ups (35 boards), design system, readiness report, QA scripts |
| `design/` (rest) | Earlier design notes and the first Qt skeleton |

## Build and run from source (monorepo)

```bash
# from the repository root
python3.12 -m venv .venv-desktop
.venv-desktop/bin/pip install -e ".[all]" -e desktop
.venv-desktop/bin/seohead-desktop --project /path/to/existing/project
```

Tests (run from `desktop/`, the suite imports its helpers as `tests.*`):
`cd desktop && QT_QPA_PLATFORM=offscreen ../.venv-desktop/bin/python -m unittest discover -s tests` — 256 tests, 13 skipped.
Bundle (macOS): `desktop/scripts/build_macos.sh` → `desktop/dist/SEOHEAD Desktop.app` (ignored by git).

`desktop/` is excluded from the core's ruff/pytest/packaging; it has its own toolchain. Lint: `cd desktop && ../.venv-desktop/bin/ruff check .` (config in `desktop/pyproject.toml`).
`packaging/source-install.json` still pins a core commit from the time this was a separate
repository — inside the monorepo the core is `..`; updating that flow is part of the redesign.

## Design v2 in one paragraph

Two modes — **Projects** (work with an AI agent: goals, tasks, inbox, scans, compare
"before/after", reports to developers/SEO/content) and **Crawler** (Screaming-Frog-like, no
project, enter URL → Start). One 52 px top bar, workspace tabs, 200/64 px navigation with a
profile/settings button bottom-left, honest data states ("not measured" ≠ "0"), 17 statuses,
3 themes (light, dark, high contrast), RU/EN, local MCP server switch shared with the CLI
(`seohead mcp status`), installer that puts the app and the `seohead` CLI on the machine
together. Start with [`design/v2/README.md`](design/v2/README.md).

## Licence

GPL-3.0-or-later (required by PyQt5) — see `LICENSE` here. The core toolkit at the repository
root is MIT.
