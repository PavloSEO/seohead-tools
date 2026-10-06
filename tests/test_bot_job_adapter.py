"""Generic authorized-adapter checks; no messaging platform is involved."""

from __future__ import annotations

import pytest

from seohead.bot import (
    POLICY_PRESETS,
    AuthorizedJobSubmitter,
    JobOwnershipStore,
    ProjectAuthorizationStore,
    ScanJobSpec,
)
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


def test_submit_crash_after_queue_acceptance_recovers_the_private_ownership_receipt(
    monkeypatch, tmp_path
):
    backend = SQLiteJobBackend(
        tmp_path / "jobs", {"alpha": RemoteProjectLimits()}, producer_build="a" * 40
    )
    state = tmp_path / "adapter.sqlite"
    ownership = JobOwnershipStore(state)
    interrupted = AuthorizedJobSubmitter(
        backend,
        "subject",
        {"alpha"},
        ownership=ownership,
        idempotency_key=lambda: "first-attempt",
    )

    def crash(*_args, **_kwargs):
        raise SystemExit("synthetic process crash after queue acceptance")

    monkeypatch.setattr(ownership, "complete_submission", crash)
    spec = _spec()
    with pytest.raises(SystemExit, match="synthetic process crash"):
        interrupted.submit(spec)
    queued = backend.list_jobs("alpha", 0, 1)
    assert len(queued) == 1
    assert JobOwnershipStore(state).project_for(queued[0].job_id, "subject") is None

    restarted = AuthorizedJobSubmitter(
        backend,
        "subject",
        {"alpha"},
        ownership=JobOwnershipStore(state),
        idempotency_key=lambda: "retry-must-not-create-a-second-job",
    )
    assert restarted.recover() == [queued[0].job_id]
    assert restarted.submit(spec) == queued[0].job_id
    assert restarted.status(queued[0].job_id).job_id == queued[0].job_id
    assert len(backend.list_jobs("alpha", 0, 10)) == 1


def test_project_authorization_requires_opt_in_and_honors_revoke(tmp_path):
    backend = SQLiteJobBackend(
        tmp_path / "jobs", {"alpha": RemoteProjectLimits()}, producer_build="a" * 40
    )
    authorization = ProjectAuthorizationStore(tmp_path / "grants.sqlite")
    adapter = AuthorizedJobSubmitter(
        backend,
        "subject",
        {"alpha"},
        ownership=JobOwnershipStore(tmp_path / "ownership.sqlite"),
        authorization=authorization,
        idempotency_key=lambda: "authorized-job",
    )
    with pytest.raises(PermissionError, match="not authorized"):
        adapter.submit(_spec())
    authorization.grant("subject", "alpha")
    job_id = adapter.submit(_spec())
    assert adapter.status(job_id).project_id == "alpha"
    authorization.revoke("subject", "alpha")
    assert adapter.status(job_id) is None
    assert not adapter.cancel(job_id)


def test_project_authorization_requires_durable_ownership(tmp_path):
    backend = SQLiteJobBackend(
        tmp_path / "jobs", {"alpha": RemoteProjectLimits()}, producer_build="a" * 40
    )
    authorization = ProjectAuthorizationStore(tmp_path / "grants.sqlite")
    with pytest.raises(ValueError, match="durable job ownership"):
        AuthorizedJobSubmitter(backend, "subject", {"alpha"}, authorization=authorization)


def test_authorized_foreign_subject_cannot_read_or_cancel_after_restart(tmp_path):
    backend = SQLiteJobBackend(
        tmp_path / "jobs", {"alpha": RemoteProjectLimits()}, producer_build="a" * 40
    )
    grants = ProjectAuthorizationStore(tmp_path / "grants.sqlite")
    grants.grant("subject-a", "alpha")
    grants.grant("subject-b", "alpha")
    owners = tmp_path / "ownership.sqlite"
    owner = AuthorizedJobSubmitter(
        backend,
        "subject-a",
        {"alpha"},
        ownership=JobOwnershipStore(owners),
        authorization=grants,
        idempotency_key=lambda: "owner-job",
    )
    job_id = owner.submit(_spec())
    foreign = AuthorizedJobSubmitter(
        backend,
        "subject-b",
        {"alpha"},
        ownership=JobOwnershipStore(owners),
        authorization=ProjectAuthorizationStore(tmp_path / "grants.sqlite"),
    )
    assert foreign.status(job_id) is None
    assert not foreign.cancel(job_id)


def test_exact_dispatch_receipt_survives_restart_without_collapsing_intentional_runs(tmp_path):
    from dataclasses import replace

    backend = SQLiteJobBackend(
        tmp_path / "jobs", {"alpha": RemoteProjectLimits()}, producer_build="a" * 40
    )
    owners = JobOwnershipStore(tmp_path / "owners.sqlite")
    first = AuthorizedJobSubmitter(backend, "actor", {"alpha"}, ownership=owners)
    spec = _spec()
    job = first.submit(spec)
    restarted = AuthorizedJobSubmitter(backend, "actor", {"alpha"}, ownership=owners)
    assert restarted.submit(spec) == job
    with pytest.raises(PermissionError, match="identity"):
        restarted.submit(replace(spec, report={"format": "pdf"}))
    assert restarted.submit(_spec()) != job
    assert len(backend.list_jobs("alpha", 0, 10)) == 2


@pytest.mark.parametrize("policy", POLICY_PRESETS)
def test_every_policy_matches_the_shared_queue_config(policy, tmp_path):
    from seohead.bot import Action, Event, State, WizardSession

    backend = SQLiteJobBackend(
        tmp_path / "jobs", {"alpha": RemoteProjectLimits()}, producer_build="a" * 40
    )
    wizard = WizardSession(AuthorizedJobSubmitter(backend, "actor", {"alpha"}), projects=("alpha",))
    for text in ("https://example.com/", "alpha", policy, "xlsx"):
        reply = wizard.handle(Event(Action.ANSWER, text))
    assert reply.state == State.PREVIEW
    wizard.handle(Event(Action.CONFIRM))
    assert wizard.handle(Event(Action.CONFIRM)).state == State.RUNNING
