"""Network-free tests for the external WebAppAnalyzer-format fingerprint database.

All fixture technologies are fictional (AcmeCMS, AcmeCart, and Hyperspeed); none
were copied from a real GPL database. This preserves a clean licensing boundary.
"""

import json

from seohead.recon import tech_db


def _make_db(tmp_path, technologies: dict, categories: dict | None = None) -> str:
    """Build a test database with categories and one technology shard."""
    tech_dir = tmp_path / "src" / "technologies"
    tech_dir.mkdir(parents=True)
    (tech_dir / "a.json").write_text(json.dumps(technologies), encoding="utf-8")
    (tmp_path / "categories.json").write_text(
        json.dumps(
            categories
            or {"1": {"name": "CMS"}, "2": {"name": "Ecommerce"}, "9": {"name": "Runtime"}}
        ),
        encoding="utf-8",
    )
    return str(tmp_path)


def test_load_db_reads_categories_and_technologies(tmp_path):
    db_dir = _make_db(tmp_path, {"AcmeCMS": {"cats": [1], "html": ["acme-content"]}})
    db = tech_db.load_db(db_dir)
    assert db is not None
    assert "AcmeCMS" in db["technologies"]
    assert db["categories"]["1"] == "CMS"


def test_load_db_returns_none_when_not_a_db(tmp_path):
    (tmp_path / "notadb.txt").write_text("x")
    assert tech_db.load_db(str(tmp_path)) is None


def test_detect_external_matches_html_pattern(tmp_path):
    db_dir = _make_db(tmp_path, {"AcmeCMS": {"cats": [1], "html": ["acme-content"]}})
    r = tech_db.detect_external(
        '<html><script src="/acme-content/x.js"></script>',
        {},
        {},
        [],
        "https://example.com/",
        db_dir=db_dir,
    )
    assert r["db_loaded"] is True
    names = [t["name"] for t in r["technologies"]]
    assert "AcmeCMS" in names


def test_detect_external_extracts_version_from_group(tmp_path):
    db_dir = _make_db(
        tmp_path, {"AcmeCMS": {"cats": [1], "html": [r"acme-ver-([0-9.]+)\;version:\1"]}}
    )
    r = tech_db.detect_external(
        '<meta name="x" content="acme-ver-2.4.1">',
        {},
        {},
        [],
        "https://example.com/",
        db_dir=db_dir,
    )
    acme = next(t for t in r["technologies"] if t["name"] == "AcmeCMS")
    assert acme.get("version") == "2.4.1"


def test_detect_external_meta_field(tmp_path):
    db_dir = _make_db(
        tmp_path, {"AcmeCMS": {"cats": [1], "meta": {"generator": "Acme CMS ([0-9.]+)"}}}
    )
    r = tech_db.detect_external(
        '<meta name="generator" content="Acme CMS 3.0">',
        {},
        {},
        [],
        "https://example.com/",
        db_dir=db_dir,
    )
    assert any(t["name"] == "AcmeCMS" for t in r["technologies"])


def test_detect_external_headers_and_scripts(tmp_path):
    db_dir = _make_db(
        tmp_path,
        {
            "AcmeCMS": {"cats": [1], "headers": {"X-Acme": "powered-by-acme"}},
            "AcmeCart": {"cats": [2], "scripts": ["acme-cart\\.js"]},
        },
    )
    r = tech_db.detect_external(
        "<html></html>",
        {"X-Acme": "powered-by-acme"},
        {},
        ["https://cdn.example/acme-cart.js"],
        "https://example.com/",
        db_dir=db_dir,
    )
    names = {t["name"] for t in r["technologies"]}
    assert "AcmeCMS" in names
    assert "AcmeCart" in names


def test_implies_resolved_transitively(tmp_path):
    db_dir = _make_db(
        tmp_path,
        {
            "AcmeCMS": {"cats": [1], "html": ["acme-content"], "implies": ["Hyperspeed"]},
            "Hyperspeed": {"cats": [9]},
        },
    )
    r = tech_db.detect_external(
        '<script src="/acme-content/x.js"></script>',
        {},
        {},
        [],
        "https://example.com/",
        db_dir=db_dir,
    )
    names = {t["name"] for t in r["technologies"]}
    assert "AcmeCMS" in names
    assert "Hyperspeed" in names  # Included through a transitive implication.
    hyper = next(t for t in r["technologies"] if t["name"] == "Hyperspeed")
    assert hyper["category"] == "Runtime"
    assert hyper["evidence"].startswith("implied by")


def test_no_db_returns_empty_not_error(monkeypatch):
    monkeypatch.delenv("SEOHEAD_TECH_DB", raising=False)
    r = tech_db.detect_external("<html></html>", {}, {}, [], "https://example.com/")
    assert r["ok"] is True
    assert r["db_loaded"] is False
    assert r["technologies"] == []


def test_version_ternary_forms():
    import re

    m = re.search(r"v(\d)", "v3 and v5")
    assert tech_db._apply_version(r"\1", m) == "3"
    assert tech_db._apply_version(r"prefix-\1", m) == "prefix-3"
    assert tech_db._apply_version(r"\1?Pro:Free", m) == "Pro"


def test_parse_pattern_extracts_confidence_and_version():
    regex, conf, vtpl = tech_db._parse_pattern(r"acme-([0-9.]+)\;confidence:50\;version:\1")
    assert conf == 50
    assert vtpl == r"\1"
    assert regex.search("acme-1.2.3")


# ── Russian-market fingerprints ──────────────────────────────────────────────
# General-purpose public databases focus on Western products and often omit
# Russian-market platforms, payment providers, and call-tracking services. The
# localized product names below are intentional recognition fixtures.


def test_russian_platforms_are_recognised():
    from seohead.recon.tech import SIGNATURES

    by_name: dict[str, list[tuple]] = {}
    for sig in SIGNATURES:
        by_name.setdefault(sig[1], []).append(sig)

    # Localized product names are intentional fingerprint data for the supported market.
    for name, category in [
        ("ocStore", "cms"),
        ("UMI.CMS", "cms"),
        ("NetCat", "cms"),
        ("HostCMS", "cms"),
        ("Webasyst", "cms"),
        ("AdvantShop", "ecommerce"),
        ("Ecwid", "ecommerce"),
        ("Roistat", "marketing"),
        ("CoMagic", "marketing"),
        ("Callibri", "marketing"),
        ("Envybox", "widget"),
        ("Битрикс24", "widget"),  # noqa: RUF001
        ("ЮKassa", "payment"),
        ("Тинькофф Касса", "payment"),  # noqa: RUF001
        ("PickPoint", "logistics"),
    ]:
        assert name in by_name, f"missing fingerprint for {name}"
        assert all(s[0] == category for s in by_name[name]), (
            f"{name} is assigned to the wrong category"
        )


def test_signature_markers_are_not_empty():
    """Empty markers would match every page; ``_match`` handles case folding."""
    from seohead.recon.tech import SIGNATURES

    bad = [s for s in SIGNATURES if not s[3].strip()]
    assert not bad, f"empty markers: {bad[:5]}"


def test_signature_kinds_are_known():
    from seohead.recon.tech import SIGNATURES

    allowed = {"header", "value", "cookie", "html", "script"}
    bad = sorted({s[2] for s in SIGNATURES} - allowed)
    assert not bad, f"unknown signature kinds that can never match: {bad}"


# ── False positives observed in production-style audits ─────────────────────


def test_php_is_matched_by_header_value_not_by_its_presence():
    """A Next.js ``x-powered-by`` header must not be reported as PHP.

    Matching only the header's presence caused false PHP detections and sent the
    rest of the stack analysis in the wrong direction.
    """
    from seohead.recon.tech import SIGNATURES

    php = [s for s in SIGNATURES if s[1] == "PHP"]
    assert php, "the PHP signature is missing"
    assert all(s[2] == "value" for s in php), (
        "PHP must be detected from a header value, not merely a header's presence"
    )
    assert not any(s[3] == "x-powered-by" for s in php)


def test_ant_design_marker_is_specific_enough():
    """The broad ``ant-`` marker must not match Tailwind's CSS properties."""
    from seohead.recon.tech import SIGNATURES

    antd = [s for s in SIGNATURES if s[1] == "Ant Design"]
    assert antd, "the Ant Design signature is missing"
    for _, _, _, marker in antd:
        assert marker not in "font-variant-numeric", (
            f"marker {marker!r} still matches a Tailwind property"
        )
        assert marker not in "important-note gigant-banner restaurant-menu", (
            f"marker {marker!r} is too broad and matches ordinary CSS classes"
        )


def test_a_negated_implication_implies_nothing():
    # ["not", X] means "X is absent"; it must not add X or the literal "not"
    assert tech_db._implied_names(["not", "WordPress"]) == []
    assert tech_db._implied_names(["WordPress", ["not", "Joomla"]]) == ["WordPress"]
