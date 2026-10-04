"""The manually curated gap map must not contradict its reconciled registry rows."""

from pathlib import Path

from seohead.sf.core.registry import CHECKS


def test_reconciliation_snapshot_names_current_registry_evidence():
    text = (Path(__file__).resolve().parents[1] / "docs" / "COVERAGE_GAPS.md").read_text(
        encoding="utf-8"
    )
    assert f"registry.py`, {len(CHECKS)} checks" in text
    for check_id in (
        "NO_AUTHOR_BYLINE",
        "NO_CONTENT_DATES",
        "CANONICAL_TARGET_ERROR",
        "CANONICAL_HOMEPAGE_GROUP",
        "ONLY_NOFOLLOW_INLINKS",
        "HTTP_LINK_ON_HTTPS",
        "URL_SESSION_ID",
        "URL_TRAILING_SLASH_INCONSISTENT",
        "DUPLICATE_ID",
        "DECLARED_MIME_MISMATCH",
        "PLACEHOLDER_MARKER",
    ):
        assert check_id in CHECKS
        assert check_id in text
