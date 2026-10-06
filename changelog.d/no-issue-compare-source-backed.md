- Compare retained audit.v2 scans through an exact key and ordinal index without
  duplicating page and finding payloads in temporary SQLite storage. Full rows,
  deterministic plain/gzip exports, source identities and legacy inputs remain
  supported. Audit readers pin the validated snapshot through indexed row reads.
- Roll back owned comparison output members on late publication failure and refuse
  raced-in destinations; the manifest remains the final package commit marker.
