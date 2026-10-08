# Headless capability inventory

`HEADLESS_CAPABILITIES.json` is the versioned machine-readable inventory for the
#743 headless workflow. Generate it with:

```sh
python scripts/generate_headless_capability_matrix.py
```

Every setting comes from `seohead.crawl.settings.describe_settings()`, the same
source used by `crawl-site --config-help` and `seo_crawl_describe_settings`.
Each row names its implementation, CLI/MCP contract, configuration test,
behavioral test reference and linked acceptance gaps. Additional workflow rows
cover exports, providers, run isolation, interruption/recovery, reanalysis,
comparison and declarative extraction.

References follow the public contract, including explicit refusals. For example,
`discovery.external.crawl` is supported by the legacy site-crawl route, while the
shared CLI/MCP handler refuses native SQLite and list-mode requests. Lower-level
native capture tests demonstrate an internal implementation, not public routing.
Local run isolation refers to native scan writer locks and concurrent CLI scans;
remote job queues and browser sessions have separate contracts.

A declared setting or a referenced test is not a blanket support claim. The
matrix deliberately distinguishes the configuration surface from behavioral
and scale acceptance. Runtime results must bind a passing observation to the
source, configuration, corpus and environment; otherwise the linked gap remains.
No paid provider access, UI, arbitrary runtime plugin system, universal renderer
compatibility or large-site capacity follows from this inventory.

The generated inventory freshness test checks complete setting coverage and
valid source/test references. The existing configuration-surface tests verify
shared defaults, validation and routing. Browser and scale acceptance use their
separate bounded synthetic profiles, including `JS_BRIDGE_BENCHMARK.md`.
The semantic reference tests additionally exercise native writer isolation and
external-crawl refusals through the implementations named in the matrix.
