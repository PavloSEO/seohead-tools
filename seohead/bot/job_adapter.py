"""Authorized adapter from the guided conversation to the durable job core.

This deliberately knows an actor and an allowlisted project set, but no chat,
account, token or delivery protocol. A platform adapter supplies those inputs;
the queue remains the only component that starts or cancels a scan.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path

from seohead.bot.wizard import ScanJobSpec
from seohead.job_contracts import JobBackend, JobStatus, ScanOptions, ScanSubmission


class JobOwnershipStore:
    """Private durable mapping from an adapter actor to submitted jobs.

    The shared backend enforces project isolation.  An adapter still needs its
    own subject-to-job mapping: seeing every job in an allowed project would
    otherwise expose another enrolled actor's work after a restart.  This
    private store retains confirmed specifications for crash recovery, never
    tokens or report content. Unconfirmed wizard drafts are not stored.
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
            con.execute(
                """CREATE TABLE IF NOT EXISTS dispatches (
                    subject TEXT NOT NULL, dispatch_id TEXT NOT NULL, project_id TEXT NOT NULL,
                    scope TEXT NOT NULL, fingerprint TEXT NOT NULL, idempotency_key TEXT NOT NULL,
                    spec TEXT NOT NULL, job_id TEXT,
                    PRIMARY KEY(subject, dispatch_id)
                )"""
            )
        os.chmod(self.path, 0o600)

    @staticmethod
    def _record(con: sqlite3.Connection, job_id: str, subject: str, project_id: str) -> None:
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

    def record(self, job_id: str, subject: str, project_id: str) -> None:
        with sqlite3.connect(self.path) as con:
            self._record(con, job_id, subject, project_id)

    def prepare_submission(
        self, subject: str, spec: ScanJobSpec, scope: str, idempotency_key: str
    ) -> str:
        """Persist an exact confirmed dispatch before the queue can accept it."""
        if not spec.dispatch_id or len(spec.dispatch_id) > 128:
            raise ValueError("dispatch_id must be a bounded nonempty identifier")
        payload = json.dumps(asdict(spec), sort_keys=True, separators=(",", ":"))
        fingerprint = hashlib.sha256(payload.encode()).hexdigest()
        with sqlite3.connect(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT scope,fingerprint,idempotency_key FROM dispatches WHERE subject=? AND dispatch_id=?",
                (subject, spec.dispatch_id),
            ).fetchone()
            if row is not None:
                if row[:2] != (scope, fingerprint):
                    raise PermissionError("dispatch identity cannot be reused for another request")
                return row[2]
            con.execute(
                "INSERT INTO dispatches VALUES(?,?,?,?,?,?,?,NULL)",
                (
                    subject,
                    spec.dispatch_id,
                    spec.project,
                    scope,
                    fingerprint,
                    idempotency_key,
                    payload,
                ),
            )
        return idempotency_key

    def complete_submission(
        self, job_id: str, subject: str, project_id: str, dispatch_id: str, idempotency_key: str
    ) -> None:
        """Atomically retain both ownership and the permanent dispatch receipt."""
        with sqlite3.connect(self.path) as con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute(
                "SELECT project_id,idempotency_key,job_id FROM dispatches WHERE subject=? AND dispatch_id=?",
                (subject, dispatch_id),
            ).fetchone()
            if (
                row is None
                or row[:2] != (project_id, idempotency_key)
                or row[2] not in (None, job_id)
            ):
                raise PermissionError("submission receipt is unavailable")
            self._record(con, job_id, subject, project_id)
            con.execute(
                "UPDATE dispatches SET job_id=? WHERE subject=? AND dispatch_id=?",
                (job_id, subject, dispatch_id),
            )

    def pending(self, subject: str, scope: str) -> list[ScanJobSpec]:
        """Recover only already-confirmed dispatches in the same actor/chat scope."""
        with sqlite3.connect(self.path) as con:
            rows = con.execute(
                "SELECT spec FROM dispatches WHERE subject=? AND scope=? AND job_id IS NULL",
                (subject, scope),
            ).fetchall()
        return [ScanJobSpec(**json.loads(row[0])) for row in rows]

    def latest_dispatch(self, subject: str, scope: str) -> tuple[ScanJobSpec, str] | None:
        """Return the last confirmed owned dispatch for an exact actor/chat."""
        with sqlite3.connect(self.path) as con:
            row = con.execute(
                """SELECT spec,job_id FROM dispatches
                   WHERE subject=? AND scope=? AND job_id IS NOT NULL
                   ORDER BY rowid DESC LIMIT 1""",
                (subject, scope),
            ).fetchone()
        return (ScanJobSpec(**json.loads(row[0])), row[1]) if row is not None else None

    def in_scope(self, job_id: str, subject: str, scope: str) -> bool:
        with sqlite3.connect(self.path) as con:
            return (
                con.execute(
                    "SELECT 1 FROM dispatches WHERE job_id=? AND subject=? AND scope=?",
                    (job_id, subject, scope),
                ).fetchone()
                is not None
            )

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

    def projects_for(self, subject: str) -> tuple[str, ...]:
        """Return only the currently granted projects for one adapter subject."""
        if not subject:
            return ()
        with sqlite3.connect(self.path) as con:
            rows = con.execute(
                "SELECT project_id FROM project_grants WHERE subject=? ORDER BY project_id",
                (subject,),
            ).fetchall()
        return tuple(row[0] for row in rows)


@dataclass
class AuthorizedJobSubmitter:
    """Map one authorized actor's confirmed specs to the shared job backend."""

    backend: JobBackend
    subject: str
    projects: Iterable[str]
    idempotency_key: Callable[[], str] = lambda: uuid.uuid4().hex
    ownership: JobOwnershipStore | None = None
    authorization: ProjectAuthorizationStore | None = None
    scope: str = ""
    authorize: Callable[[], None] | None = None
    _projects: frozenset[str] = field(init=False, repr=False)
    _job_projects: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def __post_init__(self) -> None:
        self._projects = frozenset(self.projects)
        if not self.subject or not self._projects:
            raise ValueError("an authorized subject and at least one project are required")
        if self.authorization is not None and self.ownership is None:
            raise ValueError("project authorization requires durable job ownership")

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
        if self.authorize is not None:
            self.authorize()
        submission = self._submission(spec)
        fingerprint = submission.fingerprint()
        idempotency_key = self.idempotency_key()
        if self.ownership is not None:
            idempotency_key = self.ownership.prepare_submission(
                self.subject, spec, self.scope, idempotency_key
            )
        outcome = self.backend.submit(
            spec.project,
            self.subject,
            idempotency_key,
            fingerprint,
            submission,
            submission.options.effective_config(),
        )
        if self.ownership is not None:
            self.ownership.complete_submission(
                outcome.job.job_id,
                self.subject,
                spec.project,
                spec.dispatch_id,
                idempotency_key,
            )
        self._job_projects[outcome.job.job_id] = spec.project
        return outcome.job.job_id

    def recover(self) -> list[str]:
        """Retry confirmed dispatches using their original durable queue keys."""
        if self.ownership is None:
            return []
        return [self.submit(spec) for spec in self.ownership.pending(self.subject, self.scope)]

    def _project_for(self, job_id: str) -> str | None:
        if self.authorize is not None:
            self.authorize()
        if self.scope and (
            self.ownership is None or not self.ownership.in_scope(job_id, self.subject, self.scope)
        ):
            return None
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
        if self.authorization is not None:
            # Construction rejects this combination.  Keep a defensive deny
            # here so a later refactor cannot turn an authorized subject into
            # a project-wide job enumerator.
            return None
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
