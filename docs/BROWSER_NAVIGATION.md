# Observed browser navigation

`render-check` returns `navigation` with schema `seohead.navigation.v1`.
Rendered crawl documents retain the same adjunct under renderer provenance.
The storage encoding uses `settings.navigation_evidence` so older `scan.v1`
readers can still validate the closed renderer envelope. Current readers also
accept the earlier prerelease top-level `navigation` form and reject ambiguous
double encodings. Observed events do not change the renderer-method fingerprint.
`scan-navigation --scan scan.seohead --document-id 3` reads it offline after
reopening a scan. Omit the document ID for a bounded page (`--limit`, `--offset`);
`has_more` and `next_offset` continue the read. `seo_scan_navigation` uses the same
shared handler. Its JSON report includes event counts, document capture states,
and omitted-event counts for the selected documents only.

Events contain source and destination URLs, monotonic elapsed milliseconds from
capture initialization, observation source, and the observed navigation kind:

- `http_redirect`: a main-document HTTP 3xx response, with status and Location.
- `initial_http_navigation`: the first committed document.
- `script_navigation`: Chromium reports a script-initiated document navigation.
- `spa_history_change` and `fragment_navigation`: Chromium's same-document events.
- `anchor_navigation`: an anchor activation; it can originate from JavaScript.
- `form_navigation` and `refresh_navigation`: browser-reported form or refresh causes.
- `document_navigation`: an observed change whose cause was not established.

`user_click` is always null. The renderer performs no clicks and cannot prove that
an anchor activation came from a human. An HTTP request for a destination alone
never establishes a JavaScript cause. Iframe navigations are excluded.

Chromium uses its Page protocol for cause evidence. Engines/transports without
that capability keep frame observations but report `partial` with
`cause_observation_unsupported`. A missing browser, no observed navigation, or
legacy evidence without the versioned adjunct is `unavailable`. Failures retain
already observed events as `partial`; timeouts never create a clean result.

Capture retains at most 32 events per document. Additional events increment
`events_omitted`, making capture partial. Requested wait strategy, navigation
timeout, final URL, engine/version and renderer settings accompany the evidence.
A `complete` state describes this bounded observation window, not every possible
future interaction. No generic JavaScript SEO defect is inferred from a route
change alone.

All requests still use pinned HTTP transport, target/private-network policy,
redirect validation, response byte limits and the existing render budget.
Navigation observation adds no requests and never replays links. Persisted events
use validated URL identities; offline reads expand those references and do not
fetch. Reanalysis preserves the original renderer evidence rather than observing
a new browser run. Navigation output has a 2 MiB response bound in addition to its
document and event bounds.

## Synthetic acceptance

`tests/test_render_navigation_live.py` exercises owned loopback HTTP 301/302,
query and fragment changes, History API, programmatic document navigation,
scripted anchor activation, an event-cap loop and rejected private targets.
The tests use an already available Chromium and explicitly skip if missing.
`tests/test_navigation.py` checks cause validity, legacy unavailability, storage
identity expansion, omissions and tampering without a browser.

`tests/test_render_trusted_proxy_live.py` uses a temporary self-signed certificate
and owned loopback CONNECT interceptor. Explicit `SSL_CERT_FILE` trust enables
POST replay; absent trust, private redirects and response byte caps remain
separate failures. A public-handler case injects this owned interceptor at the
HTTP-client boundary, since `render-check` exposes no standalone proxy setting.
A separate test runs the unmodified public CLI against owned HTTPS with the same
explicit temporary CA and verifies absent-trust rejection and POST success.
This is a portable synthetic trust/interception proof; it does not claim a
Windows desktop or an actual corporate proxy was tested. No system trust store
is changed and no browser is installed by the test.
