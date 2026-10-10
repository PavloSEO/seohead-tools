"""Support a user-supplied technology fingerprint database in WebAppAnalyzer format.

Purpose and license boundary
----------------------------
``tech-detect`` ships with its own MIT-licensed fingerprints. Common public
fingerprint databases are distributed under GPL-3.0, so bundling their files in
this MIT repository would change the distribution's licensing obligations. The
database therefore remains an **external resource supplied by the user**, much like
a GeoIP database:

* this package **does not distribute** GPL-licensed fingerprint records;
* users obtain and store a compatible database independently;
* ``tech-detect`` reads it only when ``SEOHEAD_TECH_DB`` points to a directory
  containing ``categories.json`` and ``src/technologies/*.json``;
* when no database is configured, built-in signatures continue to work normally.

This is analogous to GeoIP integrations: an application can read a separately
installed database without embedding that database in its own distribution.

Format summary
--------------
A database directory contains ``categories.json`` (category ID to category name)
and ``src/technologies/{a..w}.json`` (technology name to fingerprint record). A
record has the following shape:

    {"cats": [1, 11], "headers": {"X-Pingback": "pattern"},
     "html": ["pattern"], "meta": {"generator": "pattern\\;version:\\1"},
     "cookies": {"name": "pattern"}, "scripts": ["pattern"], "url": ["pattern"],
     "implies": ["PHP", "MySQL"]}

A pattern is a case-insensitive JavaScript regular expression. Literal ``\\;``
separates optional ``confidence:N`` and ``version:DSL`` tags. The version DSL can
extract a capture group as ``\\1``, ``\\1?Pro:Free``, or ``prefix-\\1``.

Static matching supports ``headers``, ``html``, ``meta``, ``cookies``, ``scripts``,
and ``url``. The ``dom``, ``js``, and ``xhr`` fields require a browser runtime and
are intentionally skipped until browser-backed detection is implemented. ``implies``
relationships are resolved transitively.
"""

from __future__ import annotations

import json
import os
import re
from functools import lru_cache
from typing import Any

# Pattern tags are separated by a literal backslash-semicolon sequence.
_TAG_SEP = "\\;"
# Record fields supported by static, browserless matching.
_STATIC_FIELDS = ("headers", "html", "meta", "cookies", "scripts", "url")


def get_external_db_path() -> str | None:
    """Return the external database path from ``SEOHEAD_TECH_DB``, if configured."""
    return os.environ.get("SEOHEAD_TECH_DB") or None


@lru_cache(maxsize=4)
def load_db(db_dir: str) -> dict[str, Any] | None:
    """Load ``categories.json`` and ``technologies/*.json``, cached by path.

    Return ``{categories: {id: name}, technologies: {name: record}}``, or ``None``
    when the directory does not look like a compatible database.
    """
    if not db_dir or not os.path.isdir(db_dir):
        return None

    cat_path = os.path.join(db_dir, "categories.json")
    categories: dict[str, str] = {}
    if os.path.isfile(cat_path):
        try:
            with open(cat_path, encoding="utf-8") as fh:
                raw_cats = json.load(fh)
            # webappanalyzer: {id: {"name": "...", "priority": N}}
            for cid, meta in raw_cats.items():
                categories[str(cid)] = meta.get("name") if isinstance(meta, dict) else str(meta)
        except (OSError, ValueError):
            pass

    technologies: dict[str, dict[str, Any]] = {}
    # Technologies may live in src/technologies/*.json or directly in the root.
    tech_dirs = [os.path.join(db_dir, "src", "technologies"), db_dir]
    for tech_dir in tech_dirs:
        if not os.path.isdir(tech_dir):
            continue
        for fname in os.listdir(tech_dir):
            if not fname.endswith(".json"):
                continue
            try:
                with open(os.path.join(tech_dir, fname), encoding="utf-8") as fh:
                    chunk = json.load(fh)
            except (OSError, ValueError):
                continue
            if isinstance(chunk, dict):
                technologies.update(chunk)
        if technologies:
            break  # Prefer src/technologies; do not merge a second root-level copy.

    if not technologies:
        return None
    return {"categories": categories, "technologies": technologies}


def _parse_pattern(raw: str) -> tuple[re.Pattern, int, str | None]:
    """Parse a pattern into ``(regex, confidence, version_template)``.

    Tags use a literal ``\\;`` separator: ``"regex\\;confidence:75\\;version:\\1"``.
    """
    parts = raw.split(_TAG_SEP)
    regex_src = parts[0]
    confidence = 100
    version_tpl: str | None = None
    for tag in parts[1:]:
        if tag.startswith("confidence:"):
            try:  # noqa: SIM105 — explicit parsing keeps the invalid-tag path obvious
                confidence = max(0, min(100, int(tag.split(":", 1)[1])))
            except ValueError:
                pass
        elif tag.startswith("version:"):
            version_tpl = tag.split(":", 1)[1]
    try:
        regex = re.compile(regex_src, re.IGNORECASE)
    except re.error:
        # Convert an invalid database regex into a safe expression that never matches.
        regex = re.compile(r"(?!)")
    return regex, confidence, version_tpl


def _apply_version(template: str, match: re.Match) -> str:
    """Apply a capture group to ``\\1``, ``\\1?a:b``, or ``prefix-\\1`` templates."""
    ternary = re.search(r"\\(\d)(\?([^:]*)(?::(.*))?)?", template)
    if not ternary:
        return template  # constant template with no capture-group reference
    n = int(ternary.group(1))
    has_q = ternary.group(2)
    then = ternary.group(3)
    els = ternary.group(4)
    try:
        grp = match.group(n) or ""
    except (IndexError, re.error):
        grp = ""
    if has_q is None:
        value = grp
    elif grp:
        value = then if then is not None else ""
    else:
        value = els if els is not None else ""
    return template[: ternary.start()] + value + template[ternary.end() :]


def _extract_meta(html: str) -> dict[str, str]:
    """Extract ``{meta_name: content}`` for matching a record's ``meta`` field."""
    out: dict[str, str] = {}
    for m in re.finditer(
        r'<meta\s+[^>]*name=["\']?([\w:.-]+)["\']?[^>]*content=["\']([^"\']*)', html, re.IGNORECASE
    ):
        out[m.group(1).lower()] = m.group(2)
    # Also accept markup where ``content`` appears before ``name``.
    for m in re.finditer(
        r'<meta\s+[^>]*content=["\']([^"\']*)[^>]*name=["\']?([\w:.-]+)["\']', html, re.IGNORECASE
    ):
        out[m.group(2).lower()] = m.group(1)
    return out


def _match_record(
    name: str, record: dict[str, Any], ctx: dict[str, Any]
) -> tuple[str, str | None] | None:
    """Return ``(evidence, version)`` when a record matches, otherwise ``None``."""
    meta = ctx["meta"]
    headers = ctx["headers"]
    cookies = ctx["cookies"]
    scripts = ctx["scripts"]
    html = ctx["html"]
    url = ctx["url"]

    # headers: {header_name: pattern}
    for hname, raw in (record.get("headers") or {}).items():
        regex, _conf, vtpl = _parse_pattern(raw)
        value = headers.get(hname.lower(), "")
        m = regex.search(value)
        if m:
            return (f"header {hname}", _apply_version(vtpl, m) if vtpl else None)

    # html: [pattern]
    for raw in record.get("html") or []:
        regex, _conf, vtpl = _parse_pattern(raw)
        m = regex.search(html)
        if m:
            return ("HTML", _apply_version(vtpl, m) if vtpl else None)

    # meta: {meta_name: pattern}
    for mname, raw in (record.get("meta") or {}).items():
        regex, _conf, vtpl = _parse_pattern(raw)
        value = meta.get(mname.lower(), "")
        m = regex.search(value)
        if m:
            ver = _apply_version(vtpl, m) if vtpl else None
            return (f"meta {mname}", ver)

    # cookies: {cookie_name: pattern}; an empty pattern means presence is sufficient.
    for cname, raw in (record.get("cookies") or {}).items():
        regex, _conf, vtpl = _parse_pattern(raw or "")
        cval = cookies.get(cname, "") or ""
        if (raw and regex.search(cval)) or (not raw and cname in cookies):
            return (f"cookie {cname}", None)

    # scripts: [pattern]
    for raw in record.get("scripts") or []:
        regex, _conf, vtpl = _parse_pattern(raw)
        for src in scripts:
            m = regex.search(src)
            if m:
                return ("script", _apply_version(vtpl, m) if vtpl else None)

    # url: [pattern]
    for raw in record.get("url") or []:
        regex, _conf, vtpl = _parse_pattern(raw)
        m = regex.search(url)
        if m:
            return ("URL", _apply_version(vtpl, m) if vtpl else None)

    return None


def _resolve_implies(
    found: dict[str, dict[str, Any]],
    technologies: dict[str, dict[str, Any]],
    categories: dict[str, str],
) -> None:
    """Add implied technologies transitively, mutating ``found`` in place.

    ``implies`` contains strings or condition lists. A string is added whenever its
    parent technology is found. Condition lists use a deliberately conservative
    static approximation: ``and`` and ``or`` both add their named technologies once
    the parent matches, while ``not`` conditions are skipped.
    """
    queue = list(found.values())
    seen = set(found)
    while queue:
        entry = queue.pop()
        record = technologies.get(entry["name"])
        if not record:
            continue
        for imp in record.get("implies") or []:
            names = _implied_names(imp)
            for nm in names:
                if nm in seen:
                    continue
                seen.add(nm)
                impl_record = technologies.get(nm) or {}
                found[nm] = {
                    "name": nm,
                    "category": _category_of(impl_record, categories),
                    "evidence": f"implied by {entry['name']}",
                    "source": "external",
                }
                queue.append(found[nm])


def _category_of(record: dict[str, Any], categories: dict[str, str]) -> str:
    """Return the category named by ``cats[0]``, or ``implied`` when unavailable."""
    cats = record.get("cats") or []
    if cats:
        return categories.get(str(cats[0]), "external")
    return "implied"


def _implied_names(imp: Any) -> list[str]:
    if isinstance(imp, str):
        return [imp.split("\\;")[0]]  # strip optional tags
    if isinstance(imp, list):
        head = imp[0] if imp else None
        if head == "not":  # a negated condition implies nothing
            return []
        if head in ("or", "and"):  # noqa: SIM108 — documents the implicit-AND branch
            rest = imp[1:]
        else:
            rest = imp  # implicit AND
        out: list[str] = []
        for item in rest:
            out.extend(_implied_names(item))
        return out
    return []


def detect_external(
    html: str,
    headers: dict[str, str],
    cookies: dict[str, str],
    scripts: list[str],
    url: str,
    db_dir: str | None = None,
) -> dict[str, Any]:
    """Detect technologies offline with an external database.

    ``db_dir=None`` reads the path from the environment. Return
    ``{ok, db_loaded, technologies: [...], source: "external"}``. A missing database
    is a valid degraded state: ``ok: True, db_loaded: False, technologies: []``.
    """
    db_dir = db_dir if db_dir is not None else get_external_db_path()
    db = load_db(db_dir) if db_dir else None
    if not db:
        return {
            "ok": True,
            "db_loaded": False,
            "db_path": db_dir,
            "technologies": [],
            "note": "External database is not configured (SEOHEAD_TECH_DB)",
        }

    technologies = db["technologies"]
    categories = db["categories"]
    meta = _extract_meta(html)
    ctx = {
        "html": html,
        "headers": {k.lower(): v for k, v in headers.items()},
        "cookies": dict(cookies),
        "scripts": scripts,
        "url": url,
        "meta": meta,
    }

    found: dict[str, dict[str, Any]] = {}
    for name, record in technologies.items():
        if not isinstance(record, dict):
            continue
        result = _match_record(name, record, ctx)
        if result is None:
            continue
        evidence, version = result
        cats = record.get("cats") or []
        cat_name = categories.get(str(cats[0]), "external") if cats else "external"
        entry = {"name": name, "category": cat_name, "evidence": evidence, "source": "external"}
        if version:
            entry["version"] = version
        found[name] = entry

    _resolve_implies(found, technologies, categories)

    return {
        "ok": True,
        "db_loaded": True,
        "db_path": db_dir,
        "technologies_count": len(technologies),
        "technologies": sorted(found.values(), key=lambda e: (e["category"], e["name"].lower())),
    }
