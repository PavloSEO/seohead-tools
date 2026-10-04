"""Authorized adapter from the guided conversation to the durable job core.

This deliberately knows an actor and an allowlisted project set, but no chat,
account, token or delivery protocol. A platform adapter supplies those inputs;
the queue remains the only component that starts or cancels a scan.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from seohead.bot.wizard import ScanJobSpec
from seohead.job_contracts import JobBackend, JobStatus, ScanOptions, ScanSubmission


@dataclass
class AuthorizedJobSubmitter:
    """Map one authorized actor's confirmed specs to the shared job backend."""

    backend: JobBackend
    subject: str
    projects: Iterable[str]
    idempotency_key: Callable[[], str] = lambda: uuid.uuid4().hex
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
        if spec.project not in self._projects:
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
        self._job_projects[outcome.job.job_id] = spec.project
        return outcome.job.job_id

    def _project_for(self, job_id: str) -> str | None:
        if job_id in self._job_projects:
            return self._job_projects[job_id]
        for project in self._projects:
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
