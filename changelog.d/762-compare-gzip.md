- Add opt-in streaming gzip compression for complete compare.v2 NDJSON files,
  deterministic compressed bytes and exact compressed hashes with decoded row/byte
  counts. Add a bounded reader that validates plain or gzip exports and rejects
  damaged streams or mismatched manifest counts; plain output remains the default.
