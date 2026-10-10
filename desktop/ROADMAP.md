# SEOHEAD Desktop — roadmap

Owner decisions (2026-10-07 … 2026-10-09) and the order of work. Russian spec and design docs:
[`docs/spec/`](docs/spec/) (TZ, requirements check, site design reference, prompts) and
[`design/v2/`](design/v2/) (target design, design system, readiness report).

## Decisions

| Topic | Decision |
|---|---|
| UI technology | Python + PyQt5 (Qt Widgets) + QSS only. No web view, Electron or JS framework. HTML in `design/v2` is a specification. |
| Where the code lives | `desktop/` in the seohead-tools monorepo; core stays in `seohead/`. No separate checkouts. |
| Two displays | **With agent** — projects + goals, inbox, methods, agent tasks, MCP. **Simple** — the same projects, scans, compare, re-check and reports, without AI features (no empty agent panels). Switch in the profile menu bottom-left. |
| Crawler mode | Real project-less crawl (Screaming-Frog-like screen), available in both displays; result can be saved as a project. Needs core changes: run journal and per-host rate limit without a project. |
| MCP | Local stdio server; the app may write MCP entries into Claude Code / Claude Desktop / Codex / Cursor configs **only after an explicit permission dialog** that lists files and lines, with backups. One shared state for app and CLI: `seohead mcp status|enable|disable`. |
| Installer | Personal use: build from the repo. macOS `.pkg` puts the app into /Applications and a `seohead` wrapper into `/usr/local/bin` (already on PATH). A plain DMG cannot run install scripts, so the DMG flow offers "Install command line tool" on first run (admin password). Windows: Inno Setup adds the CLI folder to the user PATH. Linux: tarball/AppImage + `~/.local/bin/seohead`. No paid signing/notarisation for now. |
| Terminal | `seohead watch --project` (observer) and `seohead tui` already exist; target: Russian labels, "scan now" block with progress, brand colours, proposed flags `--scan`, `--lang`, `--compact`. |
| App icon | **Approved 2026-10-09**: silver magnifier, textured spider web, black widow (`design/v2/brand/seohead-logo.svg`, square `seohead-logo-square.svg`; small mark `src/seohead_desktop/assets/app/seohead-small.svg` for ≤ 48 px). Used on GitHub avatar, seohead.tech and README. Earlier variants in `design/v2/brand/archive/`. |
| Design | Design phase is sufficient to start implementation; further design only per screen when a Qt implementation reveals a gap. |

## Order of work (vertical slices)

0. Desktop venv + tests from `desktop/` (256 tests); own ruff config for `desktop/`.
1. Split `app.py` into modules (no behaviour change).
2. Tokens v2 + three themes (light, dark, high contrast) + QSS generated from tokens.
3. Shell: 52 px top bar, workspace tabs, navigation 200/64 with profile button, status bar, Settings (11 sections), two displays.
4. RU/EN switching on the fly.
5. Screens in scenario order (see `design/v2` boards), each checked against its board and QA scenarios.
6. Crawler mode (core: project-less journal + rate limit).
7. MCP state + permissioned config writing; `.pkg` installer with CLI on PATH; icon export.
8. Core data gaps: `scan-url-query`, outlinks per URL, HTML body in `scan-url-detail`, paged log, report/findings/methods adapters.
9. Acceptance: end-to-end on `examples/qa-site` (scan 1 → tasks → fixes → scan 2 → compare) and the 1M-URL database read budgets (page ≤ 1 s, 200 rows, no full materialisation).

Effort estimate from the readiness report: 35–55 working days total; minimal showcase (0–3 + macOS `.pkg`) 12–16 days.
