"""Generate the versioned headless capability inventory from the real settings."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT = ROOT / "docs" / "HEADLESS_CAPABILITIES.json"

# A setting or family maps to implementation and tests, not blanket readiness.
FAMILIES = {
    "http": ("seohead/recon/net.py", "tests/test_crawl_proxy.py", [709, 745]),
    "rendering": (
        "seohead/crawl/sqlite_render.py",
        "tests/test_render_document.py",
        [744, 767, 817, 826],
    ),
    "discovery": (
        "seohead/crawl/sqlite_adapter.py",
        "tests/test_crawl_settings_wired.py",
        [746, 743],
    ),
    "discovery.external.crawl": (
        "seohead/mcp/handlers.py",
        "tests/test_crawl_external.py",
        [746, 743],
    ),
    "scope": ("seohead/crawl/spider.py", "tests/test_crawl_settings_wired.py", [749]),
    "limits": ("seohead/crawl/settings.py", "tests/test_crawl_settings.py", [818]),
    "storage": ("seohead/storage/native_scan.py", "tests/test_native_capture.py", [815, 816]),
    "resources": ("seohead/storage/resource_graph.py", "tests/test_resource_fetch.py", [743]),
    "external_checks": (
        "seohead/storage/external_checks.py",
        "tests/test_native_external_capture.py",
        [746],
    ),
    "analysis": ("seohead/sf/core/registry.py", "tests/test_scan_handler_bridge.py", [748, 816]),
    "output": ("seohead/storage/scan_export.py", "tests/test_scan_exports.py", [747]),
    "cache": ("seohead/crawl/cache.py", "tests/test_crawl_settings.py", [743]),
}
WORKFLOWS = (
    (
        "export_fields",
        "seohead/storage/scan_export.py",
        "scan-export",
        "seo_scan_export",
        "tests/test_scan_export.py",
        [747],
    ),
    (
        "provider_integrations",
        "seohead/data_sources/providers.py",
        "provider-collect",
        "seo_provider_collect",
        "tests/test_provider_matrix.py",
        [753],
    ),
    (
        "run_isolation",
        "seohead/storage/native_scan.py",
        "crawl-site",
        "seo_crawl_site",
        "tests/test_cli_parallel_project_scans.py",
        [743],
    ),
    (
        "interruption_recovery",
        "seohead/storage/native_scan.py",
        "crawl-site --resume",
        "seo_crawl_site resume",
        "tests/test_scan_reanalysis_integration.py",
        [815, 817],
    ),
    (
        "offline_reanalysis",
        "seohead/mcp/reanalysis_handlers.py",
        "scan-reanalyze",
        "seo_scan_reanalyze",
        "tests/test_scan_reanalysis_integration.py",
        [743],
    ),
    (
        "offline_comparison",
        "seohead/sf/core/compare_store.py",
        "compare-crawls",
        "seo_compare_crawls",
        "tests/test_compare_bounded.py",
        [743],
    ),
    (
        "declarative_extraction",
        "seohead/checks/extraction_rules.py",
        "scan-extract",
        "seo_scan_extract",
        "tests/test_custom_extract.py",
        [743],
    ),
)


def issue_links(numbers):
    return [f"https://github.com/PavloSEO/seohead-tools/issues/{number}" for number in numbers]


def document() -> dict:
    from seohead.crawl.settings import describe_settings

    inventory = describe_settings()
    rows = []
    for setting in inventory:
        family = setting["path"].split(".")[0]
        implementation, test, gaps = FAMILIES.get(
            setting["path"],
            FAMILIES.get(
                family, ("seohead/crawl/settings.py", "tests/test_crawl_settings_wired.py", [743])
            ),
        )
        rows.append(
            {
                **setting,
                "family": family,
                "implementation": implementation,
                "cli": "crawl-site --set path=value or --config; inventory: --config-help",
                "mcp": "seo_crawl_site overrides/config; inventory: seo_crawl_describe_settings",
                "configuration_test": "tests/test_crawl_settings.py::test_defaults_load_and_validate",
                "behavioral_test_reference": test,
                "acceptance_state": "declared_surface; verify behavioral scope before claiming support",
                "linked_acceptance_gaps": issue_links(gaps),
            }
        )
    return {
        "schema": "seohead.headless-capabilities.v1",
        "inventory_source": "seohead.crawl.settings.describe_settings",
        "inventory_sha256": hashlib.sha256(
            json.dumps(inventory, sort_keys=True).encode()
        ).hexdigest(),
        "settings_count": len(inventory),
        "settings": rows,
        "workflows": [
            {
                "capability": name,
                "implementation": implementation,
                "cli": cli,
                "mcp": mcp,
                "behavioral_test_reference": test,
                "linked_acceptance_gaps": issue_links(gaps),
                "acceptance_state": "implementation_reference; source-specific acceptance required",
            }
            for name, implementation, cli, mcp, test, gaps in WORKFLOWS
        ],
        "boundaries": [
            "A declared setting or test path is not a passing behavioral or scale claim.",
            "Missing evidence remains a linked gap. Runtime manifests bind results to source, configuration and environment.",
            "Native capture currently requires cache.mode=off; persistent browser profiles are unavailable.",
            "Extension points listed here are declarative extraction/configuration and the developer registry; no arbitrary runtime plugin API is claimed.",
            "Skills, UI, provider credentials and subjective review are not collected evidence.",
        ],
    }


def render() -> str:
    return json.dumps(document(), indent=2, ensure_ascii=False) + "\n"


if __name__ == "__main__":
    OUT.write_text(render(), encoding="utf-8")
