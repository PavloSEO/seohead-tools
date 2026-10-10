"""Directory tree: page counts per URL path segment, bounded for large sites (#1004, S2).

A page is counted once at every directory above it, the root included. A path that
does not end in ``/`` names a file, so its last segment is not a directory. Query
strings and fragments are ignored; the caller passes URLs of one site.

Bounds are applied when the tree is read out, not while it is built, so the build
holds one node per distinct directory. ``max_depth`` cuts the walk, ``max_children``
keeps the busiest children per node and folds the rest into ``other_pages``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from urllib.parse import urlsplit


@dataclass
class DirNode:
    name: str
    pages: int = 0
    children: dict[str, DirNode] = field(default_factory=dict)

    def to_dict(self, max_depth: int, max_children: int) -> dict:
        """Return this node as plain data, cut at ``max_depth`` and ``max_children``."""
        ranked = sorted(self.children.values(), key=lambda n: (-n.pages, n.name))
        if max_depth <= 0:
            kept, folded = [], ranked
        else:
            kept, folded = ranked[:max_children], ranked[max_children:]
        out = {
            "name": self.name,
            "pages": self.pages,
            "children": [c.to_dict(max_depth - 1, max_children) for c in kept],
        }
        if folded:
            out["other_children"] = len(folded)
            out["other_pages"] = sum(c.pages for c in folded)
        return out


def _directory_segments(url: str) -> list[str]:
    path = urlsplit(url).path
    parts = [p for p in path.split("/") if p]
    if parts and not path.endswith("/"):
        parts = parts[:-1]  # the last segment names a file, not a directory
    return parts


def build_directory_tree(
    urls: Iterable[str], *, max_depth: int = 3, max_children: int = 50
) -> dict:
    """Return the directory tree of ``urls`` as plain data, bounded by the given limits.

    ``ponytail: plain dict tree, built in memory | ceiling: one node per distinct
    directory of the whole site | upgrade: count per directory in SQL at query time
    when a 1M-URL scan exceeds the memory budget (S6 of #1004)``
    """
    if max_depth < 0 or max_children < 0:
        raise ValueError("max_depth and max_children must be zero or more")
    root = DirNode("/")
    for url in urls:
        node = root
        node.pages += 1
        for segment in _directory_segments(url):
            child = node.children.get(segment)
            if child is None:
                child = node.children[segment] = DirNode(segment)
            child.pages += 1
            node = child
    return root.to_dict(max_depth, max_children)
