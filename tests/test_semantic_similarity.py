from __future__ import annotations

import sqlite3

import pytest

from seohead.tools import semantic_similarity
from seohead.tools.semantic_similarity import (
    EmbeddingCache,
    LocalEmbeddingAdapter,
    ProviderEmbeddingAdapter,
    analyze_semantic_documents,
)


def _documents():
    return [
        {
            "url": "https://example.test/pumps",
            "text": "pump stations for industrial water supply",
            "normalized_sha256": "a" * 64,
            "language": {"declared_primary": "en"},
        },
        {
            "url": "https://example.test/water",
            "text": "industrial water supply pump equipment",
            "normalized_sha256": "b" * 64,
            "language": {"declared_primary": "en"},
        },
        {
            "url": "https://example.test/cakes",
            "text": "cakes and pastries for a birthday party",
            "normalized_sha256": "c" * 64,
            "language": {"declared_primary": "en"},
        },
    ]


def test_local_adapter_groups_only_supplied_embedding_evidence_and_reuses_cache(tmp_path):
    calls = []

    def embed(texts):
        calls.append(list(texts))
        return [[1.0, 0.0] if "pump" in text or "water" in text else [0.0, 1.0] for text in texts]

    adapter = LocalEmbeddingAdapter("fixture", "1", "/models/fixture", embed)
    cache = EmbeddingCache(tmp_path / "semantic.sqlite")
    first = analyze_semantic_documents(_documents(), adapter, cache, threshold=0.9)
    second = analyze_semantic_documents(_documents(), adapter, cache, threshold=0.9)

    assert first["ok"] is True
    assert first["groups"][0]["kind"] == "semantic_similarity_candidate"
    assert {member["url"] for member in first["groups"][0]["members"]} == {
        "https://example.test/pumps",
        "https://example.test/water",
    }
    assert "duplicate" in first["groups"][0]["conclusion"]
    assert len(calls) == 1
    assert second["coverage"]["state"] == "complete"


def test_missing_adapter_is_unavailable_and_never_invents_a_lexical_score(tmp_path):
    result = analyze_semantic_documents(_documents(), None, EmbeddingCache(tmp_path / "cache.sqlite"))

    assert result["ok"] is False
    assert result["coverage"]["state"] == "unavailable"
    assert result["groups"] == []


def test_provider_is_opt_in_before_any_embed_call(tmp_path):
    adapter = ProviderEmbeddingAdapter(
        "fixture", "fixture", "1", lambda texts: (_ for _ in ()).throw(AssertionError("called"))
    )
    with pytest.raises(PermissionError, match="not explicitly authorized"):
        analyze_semantic_documents(_documents(), adapter, EmbeddingCache(tmp_path / "cache.sqlite"))


def test_cache_invalidates_model_configuration_and_discards_corrupt_vectors(tmp_path):
    calls = []

    def embed(texts):
        calls.append(texts)
        return [[1.0, 0.0] for _ in texts]

    cache = EmbeddingCache(tmp_path / "cache.sqlite", max_entries=3)
    docs = _documents()[:1]
    analyze_semantic_documents(docs, LocalEmbeddingAdapter("m", "1", "/m", embed), cache)
    analyze_semantic_documents(docs, LocalEmbeddingAdapter("m", "2", "/m", embed), cache)
    assert len(calls) == 2

    with sqlite3.connect(tmp_path / "cache.sqlite") as con:
        con.execute("UPDATE semantic_embeddings SET vector_json='not-json'")
    analyze_semantic_documents(docs, LocalEmbeddingAdapter("m", "2", "/m", embed), cache)
    assert len(calls) == 3


def test_candidate_budget_and_invalid_documents_are_explicit(tmp_path):
    adapter = LocalEmbeddingAdapter("m", "1", "/m", lambda texts: [[1.0, 0.0] for _ in texts])
    partial = analyze_semantic_documents(
        [*_documents()[:2], {"url": "https://example.test/missing"}],
        adapter,
        EmbeddingCache(tmp_path / "cache.sqlite"),
        max_candidate_comparisons=0,
    )

    assert partial["ok"] is False
    assert partial["reason"] == "semantic candidate-comparison budget exceeded"
    assert partial["coverage"]["omitted_documents"] == 1


def test_document_bound_is_partial_and_adapter_identity_does_not_leak_a_local_path(tmp_path, monkeypatch):
    monkeypatch.setattr(semantic_similarity, "MAX_DOCUMENTS", 2)
    adapter = LocalEmbeddingAdapter("m", "1", "/private/model", lambda texts: [[1.0] for _ in texts])
    result = analyze_semantic_documents(_documents(), adapter, EmbeddingCache(tmp_path / "cache.sqlite"))

    assert result["coverage"]["state"] == "partial"
    assert result["coverage"]["omission_reasons"] == {"semantic document limit exceeded": 1}
    assert "model_path" not in result["adapter"]
