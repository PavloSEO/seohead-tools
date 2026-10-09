"""Join a crawl against a user-supplied CSV, on URL.

Search Console exports, analytics exports, log-file summaries: all of them
are a URL-keyed table produced by something other than this toolkit. Two
questions matter and both reduce to the same join:

- **Enrichment.** Attach traffic or search-performance data to a crawled
  page, so a finding on a page with twelve thousand impressions carries more
  weight in the backlog than the same finding on a page with none.
- **Orphan detection.** A set difference between URLs the outside world
  knows about and URLs the crawl actually reached. This only changes the
  audit if those URLs are then allowed to *enter* the crawl -- as a side
  report they are just a curiosity -- which is why ``orphan_urls`` below
  hands back exactly the list ``seohead.crawl.collect.collect_urls`` (list
  mode) takes as input, rather than a report meant only for reading.

The honest failure mode of a join is silence: a mismatched key format drops
rows without complaint, and a report built on the survivors looks complete.
So this module never collapses the two non-match directions into one bucket,
and never quietly relaxes the join key to make more rows match -- every
relaxation (``ignore_query``, ``ignore_scheme``, ``casefold_path``) is a
separate, explicit, off-by-default argument to ``normalize_join_key``, each
independently testable, so a caller who turns one on knows exactly what
looser equivalence they asked for.
"""

from __future__ import annotations

import csv
from collections.abc import Callable, Iterable, Mapping
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from seohead.checks.sitemap import normalize_url

SCHEMA_VERSION = "external_join.v1"

KeyFn = Callable[[str | None], str | None]


class ExternalJoinError(ValueError):
    """The external data cannot be joined as given."""


def _origin(url: str | None) -> str | None:
    key = normalize_join_key(url)
    if key is None:
        return None
    parts = urlsplit(key)
    return urlunsplit((parts.scheme, parts.netloc, "", "", ""))


def normalize_join_key(
    url: str | None,
    *,
    ignore_query: bool = False,
    ignore_scheme: bool = False,
    casefold_path: bool = False,
) -> str | None:
    """Canonicalise one URL into the key both sides of a join are matched on.

    Returns ``None`` for anything that is not a joinable absolute URL (blank,
    missing, relative) rather than raising, so a caller counts "unkeyable"
    rows instead of the whole join crashing on the first malformed export
    line. Starts from :func:`seohead.checks.sitemap.normalize_url` (lower-cases
    the host, drops the default port and the fragment, strips a trailing
    slash) and only goes further when a flag explicitly asks for it.
    """
    if not url or not url.strip():
        return None
    try:
        canonical = normalize_url(url)
    except ValueError:
        return None
    parts = urlsplit(canonical)
    scheme = "https" if ignore_scheme else parts.scheme
    path = parts.path.casefold() if casefold_path else parts.path
    query = "" if ignore_query else parts.query
    return urlunsplit((scheme, parts.netloc, path, query, ""))


def load_csv_rows(path: str, *, url_column: str) -> list[dict[str, str]]:
    """Read a user-supplied CSV, keeping every column as-is.

    Raises by name when ``url_column`` is not one of the CSV's headers,
    rather than silently joining on the wrong column (or on nothing).
    """
    with open(path, newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        if url_column not in fieldnames:
            raise ExternalJoinError(
                f"column {url_column!r} not found in {path!r}; available columns: {fieldnames}"
            )
        return list(reader)


def join_external_data(
    pages: Iterable[Mapping[str, Any]],
    rows: Iterable[Mapping[str, Any]],
    *,
    url_field: str = "url",
    url_column: str = "url",
    key_fn: KeyFn = normalize_join_key,
) -> dict[str, Any]:
    """Join crawled pages against external rows on a normalized URL key.

    ``pages`` is any iterable of dicts carrying a URL under ``url_field``
    (a crawl's page records); ``rows`` is any iterable of dicts carrying a
    URL under ``url_column`` (typically :func:`load_csv_rows`'s output).
    Both non-match directions are reported under their own honest name,
    never merged into one generic "no match" bucket:

    - ``crawl_only`` -- crawled URLs with no matching external row.
    - ``external_only`` -- external rows with no matching crawled URL. See
      :func:`orphan_urls` for reading this as candidates to crawl.
    - ``unkeyable_pages`` / ``unkeyable_rows`` -- inputs whose URL did not
      normalize to a joinable key at all. Kept separate from a clean
      non-match: "the URL was blank" and "the URL did not match anything"
      are different facts and must not look the same in the report.
    - ``crawl_origins`` -- normalized origins observed in joinable crawled
      pages. :func:`orphan_urls` uses these as its default candidate scope.
    """
    pages = list(pages)
    rows = list(rows)

    page_by_key: dict[str, list[Mapping[str, Any]]] = {}
    crawl_origins: set[str] = set()
    unkeyable_pages: list[Any] = []
    for page in pages:
        key = key_fn(page.get(url_field))
        if key is None:
            unkeyable_pages.append(page.get(url_field))
            continue
        page_by_key.setdefault(key, []).append(page)
        if origin := _origin(page.get(url_field)):
            crawl_origins.add(origin)

    row_by_key: dict[str, list[Mapping[str, Any]]] = {}
    unkeyable_rows: list[Mapping[str, Any]] = []
    for row in rows:
        key = key_fn(row.get(url_column))
        if key is None:
            unkeyable_rows.append(row)
            continue
        row_by_key.setdefault(key, []).append(row)

    joined: list[dict[str, Any]] = []
    for key, page_group in page_by_key.items():
        row_group = row_by_key.get(key)
        if not row_group:
            continue
        for page in page_group:
            for row in row_group:
                joined.append({"url": page.get(url_field), "page": page, "external": row})

    crawl_only = sorted(
        page.get(url_field)
        for key, page_group in page_by_key.items()
        if key not in row_by_key
        for page in page_group
    )
    external_only = [
        row for key, row_group in row_by_key.items() if key not in page_by_key for row in row_group
    ]

    return {
        "schema_version": SCHEMA_VERSION,
        "joined": joined,
        "crawl_only": crawl_only,
        "external_only": external_only,
        "crawl_origins": sorted(crawl_origins),
        "unkeyable_pages": unkeyable_pages,
        "unkeyable_rows": unkeyable_rows,
        "summary": {
            "pages": len(pages),
            "rows": len(rows),
            "joined": len(joined),
            "crawl_only": len(crawl_only),
            "external_only": len(external_only),
            "unkeyable_pages": len(unkeyable_pages),
            "unkeyable_rows": len(unkeyable_rows),
        },
    }


def orphan_urls(
    join_result: Mapping[str, Any],
    *,
    url_column: str = "url",
    allowed_origins: Iterable[str] | None = None,
) -> list[str]:
    """URLs the external data knows about but the crawl never reached.

    A named view over ``external_only``: that set already answers "what did
    not join" in general, and this is the specific reading of it that makes
    orphan detection actionable -- feed the result into
    ``seohead.crawl.collect.collect_urls`` for a follow-up list-mode crawl,
    so these URLs are allowed to enter the audit rather than sit in a side
    report nobody acts on. Candidates default to origins observed in the
    crawled pages; pass ``allowed_origins`` to deliberately audit more than
    one origin. Other external rows remain visible under ``external_only``.
    """
    if isinstance(allowed_origins, str):
        allowed_origins = (allowed_origins,)
    allowed = (
        {_origin(origin) for origin in allowed_origins}
        if allowed_origins is not None
        else set(join_result.get("crawl_origins") or ())
    )
    allowed.discard(None)
    urls = {
        row.get(url_column)
        for row in join_result.get("external_only", ())
        if _origin(row.get(url_column)) in allowed
    }
    return sorted(url for url in urls if url)
