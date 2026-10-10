"""Small shared helpers: whitespace collapsing, canonical JSON and UTC timestamps."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any


def collapse_whitespace(text: str) -> str:
    """Collapse every whitespace run to one space and trim both ends."""
    return " ".join(text.split())


def canonical_json(value: Any) -> str:
    """Deterministic JSON text (sorted keys, compact, non-ASCII kept) for hashing."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def utc_iso_z() -> str:
    """Current UTC time as ISO 8601 with a ``Z`` suffix."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
