"""Click depth: shortest internal link path from the scan's start page, derived at query time.

``pages.crawl_depth`` is the frontier depth, and sitemap-seeded URLs enter at 0,
so it is not a click count. The value is computed from the stored ``links`` table
into temp tables for one query; nothing is written to the scan file. A page no
link reaches is absent (orphan). Raises ``LookupError`` when the scan cannot
support the derivation, so the caller can report that instead of a false NULL.
"""

from __future__ import annotations

import json
import sqlite3


def materialize(con: sqlite3.Connection) -> None:
    """Create temp.cd_depth(url_id, depth) for the scan open on ``con``."""
    caps_text, start = con.execute(
        "SELECT capabilities_json, start_url FROM scan WHERE singleton=1"
    ).fetchone()
    try:
        links_state = (json.loads(caps_text).get("links") or {}).get("state")
    except (TypeError, ValueError, AttributeError):
        links_state = None
    if links_state == "unavailable":
        raise LookupError("link observations were not retained for this scan")
    root = (
        con.execute(
            "SELECT u.url_id FROM urls u JOIN pages p ON p.url_id=u.url_id WHERE u.url=?",
            (start,),
        ).fetchone()
        if start
        else None
    )
    if root is None:
        raise LookupError("start page is not stored in this scan")
    for name in ("cd_edges", "cd_depth", "cd_level", "cd_next"):
        con.execute(f"DROP TABLE IF EXISTS temp.{name}")
    # One deduplicated edge copy with an index on source, so each BFS level is an index lookup.
    con.execute(
        "CREATE TEMP TABLE cd_edges AS SELECT DISTINCT source_url_id AS s, destination_url_id AS d "
        "FROM links"
    )
    con.execute("CREATE INDEX temp.cd_edges_s ON cd_edges (s)")
    con.execute("CREATE TEMP TABLE cd_depth (url_id INTEGER PRIMARY KEY, depth INTEGER NOT NULL)")
    con.execute("CREATE TEMP TABLE cd_level (url_id INTEGER PRIMARY KEY)")
    con.execute("CREATE TEMP TABLE cd_next (url_id INTEGER PRIMARY KEY)")
    con.execute("INSERT INTO temp.cd_depth VALUES (?, 0)", (root[0],))
    con.execute("INSERT INTO temp.cd_level VALUES (?)", (root[0],))
    depth = 0
    while True:
        depth += 1
        con.execute("DELETE FROM temp.cd_next")
        # Only crawled pages count as reachable; external destinations are not pages.
        con.execute(
            "INSERT OR IGNORE INTO temp.cd_next (url_id) "
            "SELECT e.d FROM temp.cd_level c "
            "JOIN temp.cd_edges e ON e.s=c.url_id "
            "JOIN pages p ON p.url_id=e.d "
            "WHERE NOT EXISTS (SELECT 1 FROM temp.cd_depth x WHERE x.url_id=e.d)"
        )
        if con.execute("SELECT 1 FROM temp.cd_next LIMIT 1").fetchone() is None:
            break
        con.execute(
            "INSERT INTO temp.cd_depth (url_id, depth) SELECT url_id, ? FROM temp.cd_next",
            (depth,),
        )
        con.execute("DELETE FROM temp.cd_level")
        con.execute("INSERT INTO temp.cd_level (url_id) SELECT url_id FROM temp.cd_next")
    con.execute("DROP TABLE temp.cd_level")
    con.execute("DROP TABLE temp.cd_next")
