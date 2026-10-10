import pytest

from seohead.semantics.stages import cluster as serp_cluster
from seohead.semantics.store import Store

pytestmark = pytest.mark.usefixtures("semantics_offline")


def test_empty_success_is_cached_without_creating_client_again(tmp_path, monkeypatch):
    calls = []

    class Search:
        def __init__(self, **kwargs):
            calls.append("construct")

        def search_batch(self, queries, **kwargs):
            calls.append("submit")
            return {
                q: {"docs": [], "operation_id": "synthetic-operation", "status": "ok"}
                for q in queries
            }

    monkeypatch.setattr(serp_cluster, "WebSearch", Search)
    with Store(tmp_path / "sya.db") as sem_store:
        sem_store.upsert("synthetic phrase", base=100)
        sem_store.set_status("synthetic phrase", "kept")
        sem_cfg = {"regions": [225]}
        serp_cluster.run(sem_store, sem_cfg)
        serp_cluster.run(sem_store, sem_cfg)
        assert calls == ["construct", "submit"]
        assert sem_store.spend()["yandex_requests"] == 1
        assert sem_store.counts()["_clusters"] == 1


def test_timed_out_batch_cannot_be_paid_for_twice(tmp_path, monkeypatch):
    calls = []

    class Search:
        def __init__(self, **kwargs):
            pass

        def search_batch(self, queries, **kwargs):
            calls.append(queries)
            return {}

    monkeypatch.setattr(serp_cluster, "WebSearch", Search)
    with Store(tmp_path / "sya.db") as sem_store:
        sem_store.upsert("synthetic phrase", base=100)
        sem_store.set_status("synthetic phrase", "kept")
        with pytest.raises(ValueError, match="incomplete"):
            serp_cluster.run(sem_store, {})
        with pytest.raises(ValueError, match="unresolved"):
            serp_cluster.run(sem_store, {})
        assert len(calls) == 1
        assert sem_store.db.execute("SELECT state FROM serp_fetches").fetchone()[0] == "pending"


def test_cached_serp_does_not_overwrite_manual_cluster(tmp_path, monkeypatch):
    def forbidden(**kwargs):
        raise AssertionError("fully cached clustering must not initialize the provider")

    monkeypatch.setattr(serp_cluster, "WebSearch", forbidden)
    with Store(tmp_path / "sya.db") as sem_store:
        sem_store.upsert("synthetic phrase", base=100)
        sem_store.set_status("synthetic phrase", "kept")
        cluster = sem_store.upsert_cluster(
            "synthetic phrase", source="manual_sya", landing_url="https://example.invalid/landing"
        )
        sem_store.assign_cluster("synthetic phrase", cluster)
        sem_store.add_serp(
            "synthetic phrase",
            [{"url": "https://example.invalid/a", "domain": "example.invalid", "pos": 1}],
        )
        serp_cluster.run(sem_store, {})
        row = sem_store.phrases()[0]
        assert row["cluster_id"] == cluster
        assert (
            sem_store.db.execute(
                "SELECT landing_url FROM clusters WHERE id=?", (cluster,)
            ).fetchone()[0]
            == "https://example.invalid/landing"
        )


def test_region_receipts_are_separate_and_can_be_reused(tmp_path, monkeypatch):
    calls = []

    class Search:
        def __init__(self, **kwargs):
            pass

        def search_batch(self, queries, *, region, **kwargs):
            calls.append(region)
            return {
                q: {
                    "docs": [{"url": f"https://example.invalid/{region}", "pos": 1}],
                    "operation_id": f"synthetic-{region}",
                    "status": "ok",
                }
                for q in queries
            }

    monkeypatch.setattr(serp_cluster, "WebSearch", Search)
    with Store(tmp_path / "sya.db") as sem_store:
        sem_store.upsert("synthetic phrase", base=100)
        sem_store.set_status("synthetic phrase", "kept")
        for region in (225, 149, 225):
            serp_cluster.run(sem_store, {"regions": [region]})
        assert calls == [225, 149]
        assert sem_store.db.execute("SELECT COUNT(*) FROM serp_fetches").fetchone()[0] == 2


def test_manual_landing_without_source_keeps_assignment(tmp_path, monkeypatch):
    with Store(tmp_path / "sya.db") as sem_store:
        sem_store.upsert("synthetic phrase", base=100)
        sem_store.set_status("synthetic phrase", "kept")
        cluster = sem_store.upsert_cluster(
            "editorial group", landing_url="https://example.invalid/landing"
        )
        sem_store.assign_cluster("synthetic phrase", cluster)
        sem_store.add_serp("synthetic phrase", [{"url": "https://example.invalid/a", "pos": 1}])
        serp_cluster.run(sem_store, {})
        assert sem_store.phrases()[0]["cluster_id"] == cluster
