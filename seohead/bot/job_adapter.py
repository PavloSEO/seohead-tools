"""Authorized adapter from the guided conversation to the durable job core.

This deliberately knows an actor and an allowlisted project set, but no chat,
account, token or delivery protocol. A platform adapter supplies those inputs;
the queue remains the only component that starts or cancels a scan.
"""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from seohead.bot.wizard import ScanJobSpec
from seohead.job_contracts import JobBackend, JobStatus, ScanOptions, ScanSubmission


class JobOwnershipStore:
    """Private durable mapping from an adapter actor to submitted jobs.

    The shared backend enforces project isolation.  An adapter still needs its
    own subject-to-job mapping: seeing every job in an allowed project would
    otherwise expose another enrolled actor's work after a restart.  This
    store deliberately contains identifiers only, never URLs, tokens, or scan
    configuration.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            raise ValueError("job ownership store must be private")
        with sqlite3.connect(self.path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS job_owners (
                    job_id TEXT PRIMARY KEY, subject TEXT NOT NULL, project_id TEXT NOT NULL
                )"""
            )
        os.chmod(self.path, 0o600)

    def record(self, job_id: str, subject: str, project_id: str) -> None:
        with sqlite3.connect(self.path) as con:
            row = con.execute(
                "SELECT subject,project_id FROM job_owners WHERE job_id=?", (job_id,)
            ).fetchone()
            if row is None:
                con.execute(
                    "INSERT INTO job_owners(job_id,subject,project_id) VALUES(?,?,?)",
                    (job_id, subject, project_id),
                )
            elif tuple(row) != (subject, project_id):
                raise PermissionError("job ownership cannot be reassigned")

    def project_for(self, job_id: str, subject: str) -> str | None:
        with sqlite3.connect(self.path) as con:
            row = con.execute(
                "SELECT project_id FROM job_owners WHERE job_id=? AND subject=?", (job_id, subject)
            ).fetchone()
        return row[0] if row is not None else None


class ProjectAuthorizationStore:
    """Private opt-in subject/project allowlist for a service adapter.

    Enrollment is deliberately an adapter or operator action; this class has
    no account discovery, network calls, or secret handling.  The durable
    store makes a revocation effective for the next submit, status, or cancel
    request even when an adapter process is already running.
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            raise ValueError("project authorization store must be private")
        with sqlite3.connect(self.path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS project_grants (
                    subject TEXT NOT NULL, project_id TEXT NOT NULL,
                    PRIMARY KEY(subject, project_id)
                )"""
            )
        os.chmod(self.path, 0o600)

    def grant(self, subject: str, project_id: str) -> None:
        if not subject or not project_id:
            raise ValueError("subject and project_id are required")
        with sqlite3.connect(self.path) as con:
            con.execute(
                "INSERT OR IGNORE INTO project_grants(subject,project_id) VALUES(?,?)",
                (subject, project_id),
            )

    def revoke(self, subject: str, project_id: str) -> None:
        with sqlite3.connect(self.path) as con:
            con.execute(
                "DELETE FROM project_grants WHERE subject=? AND project_id=?",
                (subject, project_id),
            )

    def allows(self, subject: str, project_id: str) -> bool:
        with sqlite3.connect(self.path) as con:
            row = con.execute(
                "SELECT 1 FROM project_grants WHERE subject=? AND project_id=?",
                (subject, project_id),
            ).fetchone()
        return row is not None


@dataclass
class AuthorizedJobSubmitter:
    """Map one authorized actor's confirmed specs to the shared job backend."""

    backend: JobBackend
    subject: str
    projects: Iterable[str]
    idempotency_key: Callable[[], str] = lambda: uuid.uuid4().hex
    ownership: JobOwnershipStore | None = None
    authorization: ProjectAuthorizationStore | None = None
    _projects: frozenset[str] = field(init=False, repr=False)
    _job_projects: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._projects = frozenset(self.projects)
        if not self.subject or not self._projects:
            raise ValueError("an authorized subject and at least one project are required")

    @staticmethod
    def _submission(spec: ScanJobSpec) -> ScanSubmission:
        options = ScanOptions(
            max_urls=spec.config["limits"]["max_urls"],
            max_depth=spec.config["limits"]["max_depth"],
            max_requests=spec.config["limits"]["max_requests"],
            max_crawl_seconds=spec.config["limits"]["max_crawl_seconds"],
            concurrency=spec.config["speed"]["concurrency"],
            rendering_mode=spec.config["rendering"]["mode"],
        )
        submission = ScanSubmission(target_url=spec.url, options=options)
        if spec.config != submission.options.effective_config():
            raise ValueError("guided scan config differs from the supported remote job contract")
        return submission

    def submit(self, spec: ScanJobSpec) -> str:
        """Queue the exact previewed spec under the configured actor and project."""
        if spec.project not in self._projects or (
            self.authorization is not None
            and not self.authorization.allows(self.subject, spec.project)
        ):
            raise PermissionError("project is not authorized for this actor")
        submission = self._submission(spec)
        outcome = self.backend.submit(
            spec.project,
            self.subject,
            self.idempotency_key(),
            submission.fingerprint(),
            submission,
            submission.options.effective_config(),
        )
        if self.ownership is not None:
            self.ownership.record(outcome.job.job_id, self.subject, spec.project)
        self._job_projects[outcome.job.job_id] = spec.project
        return outcome.job.job_id

    def _project_for(self, job_id: str) -> str | None:
        if job_id in self._job_projects:
            project = self._job_projects[job_id]
            if self.authorization is None or self.authorization.allows(self.subject, project):
                return project
            return None
        if self.ownership is not None:
            project = self.ownership.project_for(job_id, self.subject)
            if (
                project is None
                or project not in self._projects
                or (
                    self.authorization is not None
                    and not self.authorization.allows(self.subject, project)
                )
            ):
                return None
            self._job_projects[job_id] = project
            return project
        # Compatibility mode is intentionally not durable.  Service adapters
        # should always pass JobOwnershipStore so a restart cannot widen a
        # subject's job visibility to every job in an allowed project.
        for project in self._projects:
            if self.authorization is not None and not self.authorization.allows(
                self.subject, project
            ):
                continue
            if self.backend.get_job(project, job_id) is not None:
                self._job_projects[job_id] = project
                return project
        return None

    def cancel(self, job_id: str) -> bool:
        """Request cancellation only for a job visible through this actor's projects."""
        project = self._project_for(job_id)
        return bool(project and self.backend.cancel_job(project, job_id) is not None)

    def status(self, job_id: str) -> JobStatus | None:
        """Return current queue state without manufacturing a progress total."""
        project = self._project_for(job_id)
        return self.backend.get_job(project, job_id) if project else None
