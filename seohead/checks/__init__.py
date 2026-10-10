"""Platform-independent SEO core — pure logic, no servers, no GUI.

Each module is independent (stdlib + its own third-party deps only). The shared
handler layer (``seohead.mcp.handlers``) wraps these for the CLI, MCP,
and HTTP faces.
"""

# clusterer imports scikit-learn lazily; import the module but don't require sklearn
# at import time (it raises only when run_clusterer is actually called).
from . import (  # noqa: F401
    clusterer,
    downloader,
    headers,
    hreflang,
    links,
    optimizer,
    parser,
    redirects,
    robots,
    sitemap,
)

__all__ = [
    "clusterer",
    "downloader",
    "optimizer",
    "parser",
    "redirects",
    "sitemap",
]
