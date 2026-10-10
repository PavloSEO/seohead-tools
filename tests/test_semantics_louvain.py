"""Louvain clustering over cached SERP; needs the optional ``semantics`` extra."""

from __future__ import annotations

import pytest

from seohead.semantics.stages import cluster as serp_cluster

pytestmark = pytest.mark.usefixtures("semantics_offline")


def test_louvain_groups_cached_serp_without_a_provider(sem_store, monkeypatch):
    pytest.importorskip("networkx")
    pytest.importorskip("community")

    def forbidden(**_kwargs):
        raise AssertionError("cached clustering must not construct a provider")

    monkeypatch.setattr(serp_cluster, "WebSearch", forbidden)
    topics = {"audit": ["seo аудит сайта", "аудит сайта онлайн", "технический аудит сайта"]}
    topics["crawl"] = ["краулер сайта", "краулер для seo", "парсер сайта для seo"]
    for topic, phrases in topics.items():
        for phrase in phrases:
            sem_store.upsert(phrase, base=100)
            sem_store.set_status(phrase, "kept")
            sem_store.add_serp(
                phrase,
                [{"url": f"https://example.invalid/{topic}/{n}", "pos": n + 1} for n in range(4)],
            )
    result = serp_cluster.run(sem_store, {}, method="louvain")
    assert result["method"] == "louvain" and result["requests"] == 0
    ids = {row["norm"]: row["cluster_id"] for row in sem_store.phrases()}
    assert len({ids[p] for p in topics["audit"]}) == 1
    assert {ids[p] for p in topics["audit"]}.isdisjoint({ids[p] for p in topics["crawl"]})


def test_unknown_cluster_method_is_rejected(sem_store):
    with pytest.raises(ValueError, match="serp or louvain"):
        serp_cluster.run(sem_store, {}, method="random")
