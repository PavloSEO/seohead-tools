- Add bounded terminal-run pagination to `project-observe` in CLI and MCP while
  keeping every stored running record visible. Pages preserve exact run IDs and
  reverse admission order; counts and retention metadata describe the current
  100-record store, not lifetime history. Reading never changes stored runtime
  states. JSON CLI inputs retain their pagination and project scope unless an
  explicit named flag overrides them.
