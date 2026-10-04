"""Optional, local-first semantic similarity over normalized retained content.

This module deliberately does not ship a model or turn lexical overlap into a
semantic score.  A caller supplies an embedding adapter and explicitly declares
whether that adapter is local or may transmit text to a provider.  The core
persists only bounded vectors in a local SQLite cache and returns evidence that
keeps semantic groups distinct from exact/near-duplicate findings.
"""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterable, Sequence, Sized
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Protocol

MAX_DOCUMENTS = 10_000
MAX_CANDIDATE_COMPARISONS = 250_000
MAX_VECTOR_DIMENSIONS = 8_192
CACHE_SCHEMA_VERSION = "semantic_cache.v1"


class EmbeddingAdapter(Protocol):
    """Small model boundary; callers own model loading and provider credentials."""

    def describe(self) -> dict[str, Any]: ...

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]: ...


@dataclass(frozen=True)
class LocalEmbeddingAdapter:
    """Declare a caller-provided local embedder without importing an ML runtime."""

    model_id: str
    model_version: str
    model_path: str
    embedder: Callable[[Sequence[str]], Sequence[Sequence[float]]]
    settings: dict[str, Any] | None = None

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "local",
            "model_id": self.model_id,
            "model_version": self.model_version,
            "model_path": self.model_path,
            "settings": self.settings or {},
            "data_transfer": "none",
            "paid": False,
        }

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        return self.embedder(texts)


@dataclass(frozen=True)
class ProviderEmbeddingAdapter:
    """Explicit wrapper for a separately configured provider integration.

    It makes no request by itself.  ``allow_external`` is intentionally false
    by default, so a provider cannot receive retained bodies merely because a
    provider-shaped adapter was constructed.
    """

    provider: str
    model_id: str
    model_version: str
    embedder: Callable[[Sequence[str]], Sequence[Sequence[float]]]
    settings: dict[str, Any] | None = None
    allow_external: bool = False
    paid: bool = False

    def describe(self) -> dict[str, Any]:
        return {
            "kind": "provider",
            "provider": self.provider,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "settings": self.settings or {},
            "data_transfer": "external",
            "paid": self.paid,
            "external_authorized": self.allow_external,
        }

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        if not self.allow_external:
            raise PermissionError("external semantic provider is not explicitly authorized")
        return self.embedder(texts)


@dataclass(frozen=True)
class DeclaredEmbeddingAdapter:
    """Use vectors supplied by the calling agent without loading or calling a model.

    The declaration is still required for cache invalidation and data-transfer
    provenance.  ``analyze_semantic_documents`` consumes each document's
    ``embedding`` field before reaching this method.
    """

    declaration: dict[str, Any]

    def describe(self) -> dict[str, Any]:
        return dict(self.declaration)

    def embed(self, texts: Sequence[str]) -> Sequence[Sequence[float]]:
        raise ValueError("an embedding is missing from the supplied semantic input")


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def adapter_identity(adapter: EmbeddingAdapter) -> dict[str, Any]:
    """Return the reproducible identity that scopes a cached embedding."""
    declared = adapter.describe()
    required = ("kind", "model_id", "model_version", "settings", "data_transfer")
    missing = [name for name in required if name not in declared]
    if missing:
        raise ValueError(f"embedding adapter declaration is missing: {', '.join(missing)}")
    if declared["kind"] not in {"local", "provider"}:
        raise ValueError("embedding adapter kind must be local or provider")
    if not isinstance(declared["settings"], dict):
        raise ValueError("embedding adapter settings must be an object")
    expected_transfer = "none" if declared["kind"] == "local" else "external"
    if declared["data_transfer"] != expected_transfer:
        raise ValueError(f"{declared['kind']} adapter must declare data_transfer={expected_transfer!r}")
    if declared["kind"] == "provider" and not declared.get("external_authorized", False):
        raise PermissionError("external semantic provider is not explicitly authorized")
    # Keep cache/report identity reproducible without leaking local model paths,
    # credential references or arbitrary adapter implementation fields.
    allowed = {
        name: declared[name]
        for name in (
            "kind",
            "provider",
            "model_id",
            "model_version",
            "settings",
            "data_transfer",
            "paid",
            "external_authorized",
        )
        if name in declared
    }
    return {"cache_schema": CACHE_SCHEMA_VERSION, **allowed}


def cache_key(source_sha256: str, identity: dict[str, Any]) -> str:
    """Bind a vector to the normalized source, model and all declared settings."""
    if not isinstance(source_sha256, str) or len(source_sha256) != 64:
        raise ValueError("source_sha256 must be a SHA-256 hex digest")
    return hashlib.sha256(f"{source_sha256}:{_canonical(identity)}".encode()).hexdigest()


def _vector(value: Sequence[float]) -> list[float]:
    vector = [float(part) for part in value]
    if not vector or len(vector) > MAX_VECTOR_DIMENSIONS:
        raise ValueError("embedding vector has an invalid dimension")
    if any(not math.isfinite(part) for part in vector):
        raise ValueError("embedding vector contains a non-finite value")
    return vector


class EmbeddingCache:
    """A bounded, corruption-tolerant SQLite cache; vectors never enter reports."""

    def __init__(self, path: str | Path, *, max_entries: int = MAX_DOCUMENTS):
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.path = str(path)
        self.max_entries = max_entries
        with self._connect() as con:
            con.execute(
                "CREATE TABLE IF NOT EXISTS semantic_embeddings ("
                "cache_key TEXT PRIMARY KEY, source_sha256 TEXT NOT NULL, identity_json TEXT NOT NULL, "
                "vector_json TEXT NOT NULL, created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)"
            )

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self.path, timeout=5)
        con.execute("PRAGMA journal_mode=WAL")
        return con

    def get(self, key: str) -> list[float] | None:
        with self._connect() as con:
            row = con.execute(
                "SELECT vector_json FROM semantic_embeddings WHERE cache_key=?", (key,)
            ).fetchone()
            if row is None:
                return None
            try:
                value = _vector(json.loads(row[0]))
            except (TypeError, ValueError, json.JSONDecodeError):
                con.execute("DELETE FROM semantic_embeddings WHERE cache_key=?", (key,))
                return None
            return value

    def put(self, key: str, source_sha256: str, identity: dict[str, Any], vector: Sequence[float]) -> None:
        encoded = _canonical(_vector(vector))
        with self._connect() as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute(
                "INSERT OR REPLACE INTO semantic_embeddings "
                "(cache_key,source_sha256,identity_json,vector_json) VALUES (?,?,?,?)",
                (key, source_sha256, _canonical(identity), encoded),
            )
            excess = con.execute("SELECT COUNT(*) FROM semantic_embeddings").fetchone()[0] - self.max_entries
            if excess > 0:
                con.execute(
                    "DELETE FROM semantic_embeddings WHERE cache_key IN ("
                    "SELECT cache_key FROM semantic_embeddings ORDER BY created_at, cache_key LIMIT ?)",
                    (excess,),
                )


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding vectors have inconsistent dimensions")
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        raise ValueError("embedding vector has zero magnitude")
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator


def analyze_semantic_documents(
    documents: Iterable[dict[str, Any]],
    adapter: EmbeddingAdapter | None,
    cache: EmbeddingCache,
    *,
    threshold: float = 0.82,
    max_candidate_comparisons: int = MAX_CANDIDATE_COMPARISONS,
) -> dict[str, Any]:
    """Group adapter-measured topical candidates with bounded, inspectable evidence.

    ``documents`` are normalized retained inputs held by the caller.  Each needs
    ``url``, ``text`` and ``normalized_sha256``.  Missing inputs and invalid
    vectors are surfaced as omissions.  This function never calls a model when
    ``adapter`` is absent, and does not use lexical heuristics as a substitute.
    """
    if not 0 < threshold <= 1:
        raise ValueError("semantic threshold must be in (0, 1]")
    if max_candidate_comparisons < 0:
        raise ValueError("max_candidate_comparisons cannot be negative")
    total = len(documents) if isinstance(documents, Sized) else None
    selected = list(islice(documents, MAX_DOCUMENTS + 1))
    truncated = len(selected) > MAX_DOCUMENTS
    if truncated:
        selected.pop()
    coverage: dict[str, Any] = {
        "eligible_documents": total if total is not None else len(selected),
        "analyzed_documents": 0,
        "omitted_documents": 0,
        "omission_reasons": {},
    }
    if adapter is None:
        return {
            "ok": False,
            "reason": "no semantic embedding adapter is selected",
            "coverage": {**coverage, "state": "unavailable"},
            "groups": [],
        }
    identity = adapter_identity(adapter)
    if truncated:
        omitted = (total - len(selected)) if total is not None else 1
        coverage["omitted_documents"] += omitted
        coverage["omission_reasons"]["semantic document limit exceeded"] = omitted
    usable: list[tuple[dict[str, Any], list[float]]] = []
    pending: list[tuple[dict[str, Any], str]] = []
    for document in selected:
        source_hash = document.get("normalized_sha256")
        text = document.get("text")
        if not isinstance(source_hash, str) or not isinstance(text, str) or not text:
            reason = "normalized retained content is unavailable"
            coverage["omitted_documents"] += 1
            coverage["omission_reasons"][reason] = coverage["omission_reasons"].get(reason, 0) + 1
            continue
        key = cache_key(source_hash, identity)
        cached = cache.get(key)
        if cached is None:
            supplied = document.get("embedding")
            if supplied is not None:
                validated = _vector(supplied)
                cache.put(key, source_hash, identity, validated)
                usable.append((document, validated))
            else:
                pending.append((document, key))
        else:
            usable.append((document, cached))
    if pending:
        vectors = adapter.embed([item[0]["text"] for item in pending])
        if len(vectors) != len(pending):
            raise ValueError("embedding adapter returned a different number of vectors")
        for (document, key), vector in zip(pending, vectors, strict=True):
            validated = _vector(vector)
            cache.put(key, document["normalized_sha256"], identity, validated)
            usable.append((document, validated))
    coverage["analyzed_documents"] = len(usable)
    if coverage["omitted_documents"]:
        coverage["state"] = "partial"
    else:
        coverage["state"] = "complete"

    parent = list(range(len(usable)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def join(left: int, right: int) -> None:
        left_root, right_root = root(left), root(right)
        if left_root != right_root:
            parent[right_root] = left_root

    comparisons = 0
    edges: list[dict[str, Any]] = []
    for left in range(len(usable)):
        for right in range(left + 1, len(usable)):
            if comparisons >= max_candidate_comparisons:
                return {
                    "ok": False,
                    "reason": "semantic candidate-comparison budget exceeded",
                    "adapter": identity,
                    "coverage": {**coverage, "state": "partial"},
                    "groups": [],
                }
            comparisons += 1
            try:
                similarity = _cosine(usable[left][1], usable[right][1])
            except ValueError as exc:
                return {
                    "ok": False,
                    "reason": str(exc),
                    "adapter": identity,
                    "coverage": {**coverage, "state": "partial"},
                    "groups": [],
                }
            if similarity >= threshold:
                join(left, right)
                edges.append(
                    {
                        "left": usable[left][0]["url"],
                        "right": usable[right][0]["url"],
                        "similarity": round(similarity, 6),
                    }
                )
    groups: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for index, (document, _) in enumerate(usable):
        groups[root(index)].append(document)
    return {
        "ok": True,
        "adapter": identity,
        "coverage": coverage,
        "candidate_comparisons": comparisons,
        "groups": [
            {
                "kind": "semantic_similarity_candidate",
                "conclusion": "review topical similarity; this is not a duplicate or cannibalization finding",
                "members": [
                    {
                        "url": item["url"],
                        "source_sha256": item["normalized_sha256"],
                        "language": item.get("language"),
                        "excerpt": item["text"][:240],
                    }
                    for item in members
                ],
                "evidence": [
                    edge
                    for edge in edges
                    if edge["left"] in {item["url"] for item in members}
                    and edge["right"] in {item["url"] for item in members}
                ],
            }
            for members in groups.values()
            if len(members) > 1
        ],
    }
