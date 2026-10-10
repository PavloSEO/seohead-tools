# Optional remote scan API contract

The `remote` extra defines a versioned, authenticated ASGI adapter for self-hosted scan jobs.
`create_app(backend, authenticator, target_policy=...)` creates an app; it does not start a
listener or public account. The local `seohead` CLI and stdio MCP remain available without the
extra. [The optional SQLite job backend](REMOTE_JOBS.md) supplies the durable queue, worker and
artifact lifecycle; [the target policy](REMOTE_TARGET_SAFETY.md) protects submission and worker
egress. No production service is started by installation alone. The required bind,
proxy, TLS, secret-reference, supervision, backup, rollback and expiry boundary
is in [SELF_HOSTED_SERVICE_PROFILE.md](SELF_HOSTED_SERVICE_PROFILE.md).

Install the adapter only where needed with `python -m pip install '.[remote]'`. Importing the
contract models does not require that extra; calling `create_app` without FastAPI installed gives
an actionable install error. A durable job backend and bearer authenticator are required. A target policy is required for submissions; without one, submit returns 503 and enqueues nothing.

## Endpoints

All paths are under `/api/v1`. Every request requires a bearer token. Project permissions are
separate: `scan:submit`, `scan:list`, `scan:read`, `scan:cancel`, and `scan:result`.

| Method | Path | Contract |
| --- | --- | --- |
| `GET` | `/openapi.json` | Authenticated OpenAPI schema; default public docs routes are disabled. |
| `POST` | `/projects/{project_id}/scans` | Submit one bounded native crawl; `Idempotency-Key` is required. Returns 202 for a new job or 200 for an identical replay. |
| `GET` | `/projects/{project_id}/scans?offset=0&limit=100` | Project-scoped jobs, ordered by the backend, at most 100 per page. |
| `GET` | `/projects/{project_id}/scans/{job_id}` | Status and progress. |
| `POST` | `/projects/{project_id}/scans/{job_id}/cancel` | Request cancellation; the backend owns terminal state. |
| `GET` | `/projects/{project_id}/scans/{job_id}/result` | Terminal coverage, saved scan-status evidence, and opaque artifact references. Pending jobs return 409. |
| `GET` | `/projects/{project_id}/scans/{job_id}/artifacts/{artifact_id}` | Stream one registered artifact after `scan:result` authorization and a project/job-scoped lookup. |

The submit body is `{"target_url":"https://example.test/","options":{...}}`. Options are a
remote-safe subset of native settings: URL, depth, request and wall-clock budgets, concurrency,
and `raw`/`js` rendering mode. The adapter copies the crawler defaults and runs the crawler's
own config validator; it does not accept credential headers, file paths, proxy settings, or
arbitrary crawler configuration. `target_policy.authorize_submission(project_id, target_url,
effective_config)` must approve the target before `backend.submit` is called. Missing policy
returns 503 and enqueues nothing. This preflight is not a substitute for #786's DNS-pinned
redirect and browser-subresource checks at worker dispatch.

The backend must atomically bind an idempotency key to `(project_id, subject, request digest)`.
The same key and body return the original job; a changed body returns 409. It must scope **every**
lookup, cancellation and artifact read to the authorized project, prevent two jobs from
overwriting each other's files, and preserve result evidence across restarts. The API checks
project grants and returned job project **and job IDs** as a second boundary, but cannot make an unsafe
backend durable or isolated. There is no built-in memory queue masquerading as production storage.

Status has explicit `queued`, `running`, `cancel_requested`, `cancelled`, `finished`, `partial`,
and `failed` states with timestamps, finish reason and frontier progress. Terminal result coverage
is `complete`, `partial`, `failed`, or `skipped`; absent audit evidence is named with
`audit_available=false` and `audit_reason`. `evidence_from_scan` projects the same source,
frontier and committed-page-outcome fields as local `scan-status` rather than recalculating a
second verdict. Artifact references are opaque IDs and media metadata, never filesystem paths.

The request body limit is 16 KiB, checked against the actual ASGI stream even when Content-Length
understates it; missing or contradictory lengths are rejected. The app refuses to start without
an operable job backend. Remote crawl options have explicit upper bounds. Anonymous
requests return 401, missing project grants return 404, missing operation grants return 403,
rejected project budgets return 422, a full project queue returns 503, and a rejected target
returns a generic 403 without echoing the URL or policy detail. Bearer
tokens are compared with configured SHA-256 digests; neither tokens nor invalid request values
appear in API errors. A deployment must provision high-entropy tokens and TLS, keep digests
outside the repository, and apply body limits again at its reverse proxy.

Synthetic local verification uses `tests/test_remote_api.py` for the API contract and
`tests/test_remote_backend.py` for API → durable queue → actual native collector → retained
evidence/report download using fake DNS and HTTP. Neither test starts a public listener, contacts
a live provider or demonstrates VPS deployment readiness.

## Stable admission ceiling

The request schema shares the native crawler's maximum of 1,000,000 URLs.
A trusted project may explicitly permit a higher budget than its operational
10,000-URL / 20,000-request defaults, but it cannot exceed the stable URL ceiling.
The separate schema ceiling of 2,000,000 requests remains finite. Requests above
the public URL ceiling fail validation rather than being clamped. The [capacity gate](MILLION_CRAWL_ACCEPTANCE.md) measures the full workflow
separately; schema admission and a small queue/worker roundtrip do not prove
one-million-page runtime capacity. Real remote jobs keep their configured time,
disk, rendering and per-origin limits.

An explicit remote `max_crawl_seconds` may be at most **1,209,600 seconds (14 days)**;
the default remains 3,600 seconds. At two requests per second, one million page
requests alone take at least 500,000 seconds, so a one-day ceiling would prevent
the declared workload from finishing. The service operator must separately raise
the project's `max_job_seconds`, URL, total-request and per-origin request budgets.
Larger, zero and boolean duration values are refused. This does not authorize a
long crawl by default, change worker leases/heartbeats or extend the independent
browser rendering budget.
