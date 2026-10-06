- Stream suppressed-finding scope rows to CSV instead of accumulating their rendered
  explanations in memory. Complete exclusion exports retain their order and source
  facts while keeping allocation bounded for large retained exclusion collections.
