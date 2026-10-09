# Guided scan-adapter conversation contract

The guided-scan roadmap (epic #751) adds a conversational way to configure,
confirm and follow a scan without a terminal. This document is the contract
that every delivery surface binds to. The code that executes it lives in
`seohead/integrations/bot/`:

- `seohead/integrations/bot/contract.py` — the versioned state machine as data
  (`CONTRACT_VERSION = "seohead.integrations.bot.conversation/1"`, states, actions,
  transitions, editable fields).
- `seohead/integrations/bot/wizard.py` — `WizardSession`, the driver: turns events into
  `Reply` objects (text + button tokens), resolves the effective crawl
  configuration through `seohead.crawl.settings`, and hands confirmed jobs
  to a `JobSubmitter` protocol.

The package contains no messaging SDK code, no account identity, no network
calls and no crawl logic. The adapter owns platform identity, markup and
delivery; the driver owns the conversation; the shared core owns the scan.
It is intentionally platform-neutral: this repository does not provision a
bot account, connect to a personal account, or send messages itself.

## States

| State | What the user sees | Actions accepted |
|---|---|---|
| `awaiting_site` | "Send the site address to scan." | `answer`, `help`, `cancel` |
| `awaiting_project` | Project buttons + "new" | `answer`, `back`, `help`, `cancel` |
| `awaiting_policy` | Named scan policies (`quick`, `standard`, `thorough`) | `answer`, `back`, `help`, `cancel` |
| `awaiting_report` | Report format buttons (`xlsx`, `docx`, `csv`, `md`, `json`), optional `problems-only` suffix | `answer`, `back`, `help`, `cancel` |
| `preview` | The resolved effective settings + config fingerprint | `confirm`, `edit`, `back`, `help`, `cancel` |
| `confirming` | Explicit final approval ("press start") | `confirm`, `back`, `help`, `cancel` |
| `running` | Progress updates | `progress`, `finish` (worker), `cancel` |
| `done` | Completion + report handle | `rerun`, `help` |
| `cancelled` | Draft discarded | `rerun` (restart), `help` |

`describe_contract()` returns the same table as JSON for adapters that want
to render it programmatically.

## Transitions

Forward is `answer` through the four collecting states, `confirm` from
`preview` to `confirming`, `confirm` again to submit and enter `running`,
`finish` to `done`. `back` walks the collecting states in reverse and
re-renders `preview` when it lands there. `edit` from `preview` jumps into
one collecting state and returns to `preview` on the next valid `answer`.
`rerun` from `done` re-enters `preview` — a rerun is re-shown and
re-confirmed, never submitted sight unseen — and from `cancelled` it starts
a fresh draft.

Anything outside `allowed_actions(state)` is an invalid step: the driver
replies with a notice and stays. Invalid steps never advance and never
submit.

## Invariants

- **Preview is submission.** The effective configuration is resolved once,
  when `preview` is entered, via `crawl.settings.load()` — the same loader
  the CLI uses — and that same `config`, `manifest()` and `fingerprint()`
  triple is what `JobSubmitter.submit` receives. Confirmation cannot drift
  from what the user was shown.
- **Nothing starts on a bad step.** Invalid answers, disallowed actions and
  expired sessions produce a reply, stay, or reset — and never reach the
  submitter.
- **Expiry recovers loudly.** A session idle past its TTL resets to
  `awaiting_site` with an explicit notice; a lapsed `running` session also
  requests cancellation so no job keeps running unattended.
- **No UI handler implements crawl logic.** `JobSubmitter` is the only
  boundary: `submit(spec) -> job_id`, `cancel(job_id) -> bool`.
  `AuthorizedJobSubmitter` is the reusable implementation for an explicit
  actor/project allowlist over the durable queue. Scan execution is the
  queue/core's job (issue #771 and its siblings).
- **Credentials stay out of chat.** The wizard accepts named policies and
  dotted setting paths resolved by the core loader; credential headers
  remain `env:` references resolved at request time by
  `settings.resolve_credential_headers`, and the preview shows the manifest
  (which redacts credential values) rather than the raw config.
- **Delivery stays authorized and idempotent.** `AuthorizedReportDelivery`
  accepts only an explicit project/destination allowlist, previews the exact
  retained artifact before sending, and stores a receipt after a successful
  injected transport call. That receipt is also passed to the transport as its
  idempotency key. A restarted adapter returns a completed receipt instead of
  sending again; a failed transport remains pending for a deliberate retry.
  When given the same `JobOwnershipStore` and subject as submission, delivery
  also rejects jobs owned by a different subject inside an otherwise shared
  project.
- **Ownership and dispatch survive restart.** The private ownership store retains
  `(job_id, subject, project_id)` plus confirmed specifications and permanent dispatch
  receipts. Each preview has its own `dispatch_id`; retrying that dispatch reuses its
  original queue key, while an intentional rerun receives a new ID even for identical
  settings. Reusing an ID with changed settings, report profile, or chat is refused.
  A confirmed submission is stored before queue acceptance. `recover()` replays only
  pending confirmed submissions in the exact actor/chat scope, recovering the same job
  after a crash between queue acceptance and ownership recording. It never replays an
  uncertain worker execution. No credentials or report bytes are stored in this registry.
  The old experimental fingerprint-only pending table is not interpreted as an exact
  dispatch receipt; operators must inspect any such archived pending work explicitly.
- **Enrollment is explicit and revocable.** `ProjectAuthorizationStore` is a
  private subject/project allowlist. Supplying it to `AuthorizedJobSubmitter`
  requires a `JobOwnershipStore`; construction otherwise fails, so an enrolled
  actor cannot fall back to project-wide job lookup. `AuthorizedReportDelivery`
  requires that same subject and ownership mapping with project authorization.
  Submission, status, cancellation, and delivery are denied until an operator
  has granted that exact project, and a later revocation is effective without
  restarting the adapter. The store records identifiers only; it is not an
  account or credential store.
- **Profiles are derived from retained evidence.** `ReportProfile` supports
  a full retained JSON/Markdown artifact, or an offline regenerated PDF,
  XLSX, DOCX, CSV, Markdown, or JSON view. A findings-only profile can filter
  by severity and check while leaving the source audit's coverage and skipped
  checks intact. An empty filtered result is explicitly marked `empty`; it is
  never presented as an unrun audit. Render failures and delivery-size limits
  are actionable failures, not successful delivery receipts. `preview()` returns a
  `ReportPreview` with measured rendered size, source SHA-256, source and selected row
  counts, empty/measured population, retained coverage/summary, and the exact profile.
  `ReportPreview.size_bytes` measures the preview render. Delivery may render again,
  so ZIP/container metadata can change the byte count; the delivery rechecks the
  actual byte budget while preserving the selected population, source and profile.
  Oversize source audits and unavailable renderers fail during preview; zero size is
  never a placeholder for an unrendered report.

## Optional configured HTTP handoff

`seohead.integrations.bot.service_delivery` is a narrow adapter for a service-owned upload
endpoint. It does not start a listener or discover recipients. An operator
constructs `UploadEndpoint` with an absolute HTTPS URL and a
`CredentialReference("env:NAME")`, then supplies `AuthorizedHTTPUpload.send`
as the `send` callback to `AuthorizedReportDelivery`. The transport sends a
single multipart envelope containing the opaque approved destination, report
metadata, artifact bytes, and the receipt in `Idempotency-Key`.

The endpoint is configuration, not a wizard answer. HTTP URLs, embedded URL
credentials, fragments, arbitrary destination URLs, missing environment
credentials, changed file sizes, non-success responses, and transport errors
all fail before a delivery receipt is marked successful. The adapter does not
implement a bot SDK, account enrolment, public download links, or deployment.
A `202 Accepted` response is also not terminal delivery evidence and is
refused: an asynchronous platform adapter must persist and verify its own
delivery state before it tells this core that the receipt succeeded.

## Optional Telegram Bot API wire adapter

`seohead.integrations.bot.telegram_adapter` supplies Bot API wire code without enabling a
listener, polling loop, or account. `TelegramBotConfig` accepts only an HTTPS
origin and an `env:NAME` token reference. `TelegramChatAuthorizationStore`
is an explicit private actor/chat allowlist. `TelegramGuidedAdapter` accepts
one caller-provided update at a time, derives a stable `telegram:<user_id>`
subject, verifies the chat grant, renders `Reply` buttons as inline keyboards,
acknowledges callbacks, and sends worker-provided progress without inventing a
total. `TelegramDocumentTransport` is constructed with that same subject and
chat store; it rechecks the destination chat grant immediately before
`sendDocument`, so a revoked chat cannot receive an already-retained report.
It also requires the Bot API to confirm a message id.

For a guided scan service, construct `TelegramAuthorizedSessions` with the
shared durable backend, the actor/chat store, `ProjectAuthorizationStore`,
`JobOwnershipStore`, and `TelegramSessionBindingStore`, then use its
`adapter(client)` method. The factory derives the subject from the trusted
update path, reads only its current project grants, creates the matching
ownership-backed submitter, and does not offer an ungranted "new" project.
Its binding store retains actor/chat bindings and exact update receipts. A duplicate
trusted `update_id` returns the same reply without advancing or sending twice; changed
content under the same ID is refused. An interrupted update remains explicitly uncertain
rather than being replayed. A restart discards unconfirmed drafts with a notice, while
already confirmed pending dispatches recover their original queue job. The last confirmed
job is restored to its live running or terminal state in the same actor/chat, so its cancel
button still addresses the original job after restart. A completed rerun returns to preview
and requires a new explicit confirmation. `status()` and
`cancel()` accept an exact retained job ID and recheck actor/chat/project scope after
restart. The same actor cannot use another enrolled chat to inspect or deliver that job.
Preview confirmation and final start use different callback tokens, so duplicate preview
confirmation cannot authorize a start. The host must serialize different updates for one
conversation; no listener or distributed conversation scheduler is provided here.
`report_delivery(...)` constructs the corresponding same-subject, initiating-chat handoff.
Chat/project authorization is checked again after rendering and immediately before sending.

The adapter has no polling, webhook registration, secret provisioning,
chat-reading, or deployment code. Telegram does not provide an upload
idempotency key, so it does not retry an ambiguous `sendDocument` outcome (including malformed or missing success confirmation);
the durable core leaves a failed attempt pending for an explicit operator
decision rather than claiming that a recipient has a report.

## Versioning

`CONTRACT_VERSION` identifies the shape of a persisted conversation. Bump it
whenever a state, action, transition or field changes; adapters and session
stores compare it before resuming, and refuse — with a fresh start — a
conversation they do not understand.

## Synthetic coverage

`tests/test_bot_wizard.py` drives the whole flow offline — site → project →
policy → report → preview → confirm → running → done — plus invalid-answer
stays, back/edit round-trips, cancellation before and during a run, expiry
before and during a run, and rerun identity. `tests/test_bot_contract.py`
pins the transition table against `allowed_actions`.
