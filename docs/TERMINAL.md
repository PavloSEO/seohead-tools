# Terminal workspace

Run `seohead watch --project DIRECTORY` in a POSIX terminal with the optional `tui`
extra installed. The project must already exist. The observer reads retained
project evidence; browsing never starts a collector, provider, workflow, export,
or publication. `seohead tui` opens the command reference without a project.

## Navigation

| Key | Action |
|---|---|
| `1`–`9`, `0` | Overview, tasks, methods, scans, findings, saved views, activity, log, Screaming Frog, inbox |
| `a` | Structured-data checks and methods |
| `p` | Own site, configured competitors and recent triaged competitor suggestions |
| Up / Down, Home / End | Select a row on the current page |
| Page Up / Page Down | Previous / next data page; scroll inside details |
| Enter | Open the selected task, method, scan, finding, saved view, run, log line, or inbox entry |
| Escape / Enter in details | Return to the list |
| `[` / `]` in saved-view details | Previous / next result page |
| `s` in site details | Browse that site's complete scan history; Findings and Views retain its site identity |
| `f`, `c` | Edit / clear the task, method, structured-data, or finding filter |
| `t` in tasks or methods | Cycle all, in-progress, blocked, review, remaining, stale and completed states |
| `s`, `r` in findings | Change sort field / reverse the order |
| `n`, `g` | Compose or resume a note / proposed-goal draft |
| `?` | Keyboard reference |
| `m` | Toggle motion; `SEOHEAD_REDUCED_MOTION=1` disables motion initially |
| `q`, Control-C, Control-D | Exit; unsaved session drafts are printed after the alternate screen closes |

The terminal remains keyboard-driven. Mouse reporting is not enabled, so normal
terminal text selection remains available. Use `--no-color` or `NO_COLOR` for
plain presentation. A compact layout replaces the sidebar in narrow windows;
windows smaller than 60 columns by 18 rows show a resize notice.

## Notes and proposed goals

Enter explicitly submits a note or proposed goal to the existing project inbox.
Submission runs in the reader worker; the editor and navigation remain responsive
while local storage is busy. A visible receipt identifies the saved entry. Saving
does not claim that an agent has received or started the work; read the entry's
triage receipt and linked task state in Inbox.

Escape suspends the current draft. `n` and `g` restore their separate drafts during
the same session. Control-X explicitly discards the current draft. Left / Right,
Home / End, Backspace and Delete edit the text. Bracketed multiline paste is kept
as text until a separate Enter. Oversized drafts are retained in full and cannot
be submitted until shortened to the inbox limit. A refused write retains the draft.

Session drafts are held in memory, not silently submitted or written to a hidden
file. On normal exit their text is printed into terminal scrollback for recovery.
Force-killing the process or closing a terminal without preserving its scrollback
can lose an unsaved draft; submit important notes explicitly. An outstanding
submission keeps the observer open until its local write receipt is available.

## Reading the evidence

Tasks and methods open readable details with scope, reasons, dependencies,
definition, retained attempts and available evidence. Structured data is a view
of existing schema-related checklist work, not a new scan or a rich-result
eligibility guarantee. Scenario and skill meters use the agreed applicable scope;
an unknown scope remains unknown. A registered skill is not evidence of execution.

Home selects active/custom work independently of the current checklist page and
shows the latest inbox entries. Current activity distinguishes primary and
competitor sites. Historical rates are not displayed as current speed when the
backend marks their sample stale or unavailable.

Sites opens each declared site's actual and expected coverage, retained scan
artifacts and source provenance. Recent inbox competitor-triage receipts appear
as suggestions with their source note and reason; a suggestion has no implied
workspace or completed analysis. The full proposal history remains in the paged
Inbox. Site browsing and switching never prepare or scan a competitor. Notes
started from evidence details retain that context and return to those details.
Compact Home keeps the measured collection count/rate, queue, sitemap state and
agreed task coverage visible. A stale live process does not animate as current work.

Lists use bounded data pages. Detail scrolling does not silently shorten evidence
values again; source-projection limits remain visible. The log browser covers the
retained tail and explicitly indicates when older bytes are outside that tail.
All project reads and explicit note writes share one background worker. The UI
polls input independently, refreshes retained data on a half-second cadence when
the reader is available, and names read failures instead of treating missing
evidence as a clean result.

## Validation

Use synthetic reserved-domain projects for terminal QA. Check full process startup,
alternate-screen and input-mode restoration, two-process live updates, resizing
during editing, slow/failed storage, first/last pages, and every detail view. A
Rich text/SVG render is a renderer preview, not a screenshot or acceptance test of
the terminal emulator. Content visibility and keyboard workflows must pass in
addition to width/height bounds.
