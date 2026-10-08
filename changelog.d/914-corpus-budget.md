- Keep retained HTML/DOM byte accounting transaction-local and incremental instead of
  rescanning all stored bodies for every unique response. Reopened writers rebuild the
  counter once; deduplication, rollback, savepoints and body retention limits remain intact.
