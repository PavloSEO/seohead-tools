# Native workspace presentation

The desktop workspace uses the existing SEOHEAD palette, bundled Roboto and Material Symbols. The single token source is `src/seohead_desktop/theme/tokens.json`; QSS renders widget states, Qt layouts and splitters control geometry. No web view, per-row widgets or new data store is introduced. The only optional motion is a finite 120 ms navigation-width transition; the Reduce Motion setting and the supported system preference disable it.

| Surface | Presentation and interaction | Data boundary |
| --- | --- | --- |
| Project and scan context | Compact project toolbar, retained-scan selector, explicit state badge | Existing `project-open`, `project-observe` and `project-scans` responses; selecting a scan never starts collection |
| Work | Readable plan and next actions; bounded run table and selected-run measurements | Up to 50 supplied observations; current rate requires fresh telemetry and keeps its unit separate from the request-rate limit |
| URL | Page-local search/sort, bounded metadata, retained headers, diagnostic payload | Existing bounded page/detail responses; absent, empty, false and zero remain distinct; sensitive header values are masked |
| Audit | Native tab decks, independent detail/summary buttons, translated primary tab labels | Catalogue visibility does not imply available evidence; unsupported sections remain explicitly unavailable |
| Scans | Retained history, owned-run controls and known-frontier progress | Existing ownership controls are reused. Progress uses measured done/queued/inflight counts, never the configured URL limit or an estimated site size |
| Journal | Readable time, run ID, phase and event | Last 20 supplied events per run, at most 200 displayed rows; no arbitrary file reads |
| Comparison | Before/after selectors, bounded differences, coverage details and verified-page summary | Explicit `compare-crawls` creates a local immutable package; `verify-fixes` always receives the new scan. Raw disappearance is never displayed as a confirmed repair |
| Manual scan | Spider / sitemap URL choice and an exact visible scope/limits preview | Sitemap submission is disabled unless the core descriptor advertises `capabilities.sitemap_only_retained`; no automatic collector substitution |
| Reports | Explicit unavailable state | Export is not connected by this presentation change |

Below 1100 logical pixels the navigation becomes a 64-pixel rail and the summary initially collapses. The user can reopen the summary or details, restore panels, or temporarily focus the table. The View menu offers 28/32/40-pixel row density. Open/Find/Copy use Qt platform shortcuts; F6 moves between native work areas. Long context labels elide visually while their full source text stays available to accessibility and tooltips. Splitter grips support arrow keys and double-click restore. Flat scrollbars retain native keyboard and pointer behavior. The project picker contains only explicitly opened recent projects (at most 20), with no folder discovery or automatic opening. Read failures are shown in a dismissible plain-text notice.

The original design references and `design/source/Untitled.fig` remain immutable. Existing scans, process ownership, dispatch, cancellation, resume and MCP transport stay in the existing adapters. App hooks invalidate stale comparison responses when the pair/project changes or reading is cancelled. Locally rotated Material chevron derivatives and their hashes are recorded in the existing asset manifest. The verified application bundle is not replaced by this source change.

## Validation scope

`tests/test_presentation.py` covers missing measurements, zero and empty values, fresh versus retained rates, frontier denominators, scan identity, bounded journal rows, compact layout, density, keyboard focus and supplied headers. Existing shell, component and multi-run lifecycle tests remain applicable.

Native acceptance on 2026-10-07 used Cocoa on macOS ARM64 with Retina device pixel ratio 2. The app read three actual retained local fixture scans with seven URLs each through the packaged core. Recorded interactions cover scan selection, URL filtering and details, headers, clipboard, F6, panel restore, partial-run inspection, unavailable tabs, journal and fullscreen. Retained SQLite hashes were unchanged. This is a desktop presentation check; it is not a scale benchmark, VoiceOver audit, mixed-display test or Windows/Linux acceptance.

Before/after captures, native interaction receipt, full test logs, source identities and the reproduction script are stored outside the repository in `Work/tools/reports/seotools-orchestrator/functional-resume-20261007/visual-native/`. The packaged core used for saved-data acceptance is `ceb2eab716c0077355d96b3a99f92ea1eb0bc0b6`; source runtime regression tests use the explicitly recorded current core checkout.

## Populated comparison acceptance

The second native pass uses the local QA project's matched 20-URL pair, not a screenshot mockup. The first bounded comparison page has 100 of 145 rows: the core confirms 13 resolved findings, while 20 remain not verifiable. Those are page-scoped verification counts, not a site health score or a statement that all 29 absent observations were repaired. New observations, persistent findings and coverage warnings remain separate.

The `visual-native/polish/` evidence folder records populated wide/compact/fullscreen captures, source SQLite hashes, pointer/keyboard input, finite animation samples and reduced-motion behavior. The comparison creates reports beneath the explicitly opened QA project; it does not fetch the site or alter either source scan. The native lanes outside macOS remain unverified.
