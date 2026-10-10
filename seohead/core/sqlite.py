"""SQLite opener helpers shared by the storage, data-source and check modules.

Callers keep their own path normalisation (``resolve()``, ``absolute()``) and their own
PRAGMAs; this module only removes the repeated connect / row-factory / pragma boilerplate.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from pathlib import Path
from typing import Any


def open_readonly(
    path: str | Path,
    *,
    timeout: float = 5,
    factory: type[sqlite3.Connection] = sqlite3.Connection,
    row_factory: Any = None,
    pragmas: Iterable[str] = (),
) -> sqlite3.Connection:
    """Open an existing database through a ``mode=ro`` URI. ``path`` must already be absolute."""
    con = sqlite3.connect(
        Path(path).as_uri() + "?mode=ro", uri=True, timeout=timeout, factory=factory
    )
    return _configure(con, row_factory, pragmas)


def open_writer(
    path: str | Path,
    *,
    timeout: float = 5,
    row_factory: Any = None,
    pragmas: Iterable[str] = (),
) -> sqlite3.Connection:
    """Open a database for reading and writing, creating it when missing."""
    return _configure(sqlite3.connect(path, timeout=timeout), row_factory, pragmas)


def _configure(
    con: sqlite3.Connection, row_factory: Any, pragmas: Iterable[str]
) -> sqlite3.Connection:
    if row_factory is not None:
        con.row_factory = row_factory
    for statement in pragmas:
        con.execute(statement)
    return con
