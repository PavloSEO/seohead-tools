- Handle Ctrl+C across the complete native post-collection handoff, including
  opening the saved scan, optional follow-up phases, analysis and finalization.
  Unfinished captures retain their data and resumable checkpoint and return an
  explicit interrupted JSON result. An interrupt after the final commit preserves
  the completed artifact and reports its actual audit availability.
  Explicit resume completes unfinished post-collection work even with a drained
  frontier, preserving existing evidence-omission flags and retained sitemap inputs.
