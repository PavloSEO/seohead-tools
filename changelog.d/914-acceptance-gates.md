- Harden the guarded loopback pacing check in the million-URL acceptance gate:
  dispatch slots keep the configured delay floor, but server-observed arrival
  pairs carry connect and scheduling jitter on a loaded host, so the gate now
  verifies the aggregate interval window plus a burst floor a collapsed
  throttle cannot meet.
- Give the streamed BI package export a per-statement read budget instead of
  inheriting the size-derived open-and-validate deadline; a large retained
  scan no longer aborts mid-export with an interrupted cursor.
- Record the 2026-10-08 acceptance receipts: guarded loopback and the
  disposable service profile pass, the Linux-only SSH/tmpfs worker fixture
  remains unrun on macOS, the dense 50,000-page stage blocked at its declared
  1,800-second wall budget during the audit.v2 write, and the 100,000/1M
  stages plus JS cold/warm gates remain unproven.
