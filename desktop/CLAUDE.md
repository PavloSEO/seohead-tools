# SEOHEAD Desktop preparation

Authorized by Pavel on2026-10-07: isolated PyQt5/Qt Widgets skeleton and reusable QSS/theme based on existing SEOHEAD brand. Native macOS/Linux/Windows target; only macOS is currently available for runtime acceptance. The owner fully cancelled the browser/web console on 2026-10-07. User-facing interfaces are CLI/terminal and native PyQt5 only; local stdio MCP serves AI agents. NoElectron/QtWebEngine UI.

Use Python. Since 2026-10-09 this app lives in the seohead-tools monorepo under desktop/; the core CLI/MCP and crawler/analyzer/task stores stay at the repository root (seohead/); this package is a presentation adapter. Target visual design: desktop/design/v2 (README, DESIGN-SYSTEM.ru.md, READINESS report); the current UI is outdated. There is no demo mode: screens show real core data or an honest empty/partial state (ui/kit StatePanel). No hidden network/crawl/provider calls. Project loading may call the existing CLI read-only from a worker.

Read current Work/CLAUDE.md and source instructions before changes; preserve userfiles. No external deployment/publication or paid calls. Public-ready source/comments are English; Russian UI is intentional localization. Theme tokens have one canonical JSON source and QSS consumes them. Avoid per-row widgets/unbounded data materialization. Record source identities and actual tests; don'tclaim cross-platform or million readiness from a skeleton.

Tests: run the suite with `python scripts/run_tests.py` (offscreen Qt; it leaves through `seohead_desktop.qt.exit_now`, because interpreter finalisation crashes PyQt with SIGSEGV on exit). Create the QApplication only via `seohead_desktop.qt.app()`; end Qt scripts with `qt.exit_now(code)`.
