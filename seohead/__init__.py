"""SEOHEAD Tools: a headless SEO toolkit by seohead.tech.

The package exposes three analysis layers over one interface-independent core:

* :mod:`seohead.sf` audits Screaming Frog crawl exports and produces structured findings;
* :mod:`seohead.checks` provides live URL, content, image, log, and structured-data tools;
* :mod:`seohead.recon` inspects domains and infrastructure, including DNS, TLS, CDN,
  technology, security-header, regional, mirror, and backlink signals.

The core does not know which interface invoked it. Two deliberately thin public interfaces—the
``seohead`` CLI and a local stdio MCP server—map inputs to the same shared handlers, so command-line
and agent workflows receive the same structured behavior without a hosted API or GUI.
"""

__version__ = "3.0.0"
__all__ = ["__version__"]
