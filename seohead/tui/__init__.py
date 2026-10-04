"""Optional interactive terminal shell for the ``seohead`` CLI.

This package is a face, not a core layer: it reads the shared CLI command
table but never runs tools itself, and nothing under ``tools/``, ``crawl/``,
``sf/``, ``recon/``, ``audit/``, ``data_sources/`` or ``servers/`` may import
it. The ``rich`` dependency it renders with is optional — install
``seohead-seotools[tui]``; ``seohead tui`` fails with a one-line hint when it
is absent.
"""
