"""Generic authorized-adapter checks; no messaging platform is involved."""

from __future__ import annotations

import pytest

from seohead.bot import POLICY_PRESETS, AuthorizedJobSubmitter, JobOwnershipStore, ScanJobSpec
from seohead.crawl import settings
from seohead.remote_api.backend import RemoteProjectLimits, SQLiteJobBackend


def _spec(project="alpha"):
    config = settings.load(overrides=POLICY_PRESETS["quick"])
    return ScanJobSpec(
        url="https://example.com/",
        project=project,
        policy="quick",
        report={"format": "json", "problems_only": False},
        config=config,
        manifest=settings.manifest(config),
        fingerprint=settings.fingerprint(config),
    )


def test_authorized_adapter_submits_and_cancels_only_its_project(tmp_path):
    backend = SQLiteJobBackend(
        tmp_path / "jobs",
        {"alpha": RemoteProjectLimits(), "beta": RemoteProjectLimits()},
        producer_build="a" * 40,
    )
    adapter = AuthorizedJobSubmitter(
        backend, "synthetic-actor", {"alpha"}, idempotency_key=lambda: "adapter-key"
    )
    job_id = adapter.submit(_spec())
    assert adapter.status(job_id).project_id == "alpha"
    assert adapter.cancel(job_id)
    assert adapter.status(job_id).state == "cancelled"
    assert adapter.status("f" * 32) is None


def test_authorized_adapter_refuses_foreign_project_before_queueing(tmp_path):
    backend = SQLiteJobBackend(
        tmp_path / "jobs",
        {"alpha": RemoteProjectLimits(), "beta": RemoteProjectLimits()},
        producer_build="a" * 40,
    )
    adapter = AuthorizedJobSubmitter(backend, "synthetic-actor", {"alpha"})
    with pytest.raises(PermissionError, match="not authorized"):
        adapter.submit(_spec("beta"))
    assert backend.list_jobs("alpha", 0, 1) == []
    assert backend.list_jobs("beta", 0, 1) == []


def test_durable_ownership_hides_other_subject_jobs_after_restart(tmp_path):
    backend = SQLiteJobBackend(
        tmp_path / "jobs", {"alpha": RemoteProjectLimits()}, producer_build="a" * 40
    )
    state = tmp_path / "adapter.sqlite"
    first = AuthorizedJobSubmitter(
        backend,
        "first-subject",
        {"alpha"},
        ownership=JobOwnershipStore(state),
        idempotency_key=lambda: "first-job",
    )
    job_id = first.submit(_spec())
    restarted = AuthorizedJobSubmitter(
        backend, "first-subject", {"alpha"}, ownership=JobOwnershipStore(state)
    )
    foreign = AuthorizedJobSubmitter(
        backend, "other-subject", {"alpha"}, ownership=JobOwnershipStore(state)
    )
    assert restarted.status(job_id).job_id == job_id
    assert foreign.status(job_id) is None
    assert not foreign.cancel(job_id)
