"""Authorized, idempotent report handoff over retained remote-job artifacts."""

from __future__ import annotations

import os
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path

from seohead.job_contracts import (
    ArtifactReference,
    JobBackend,
    JobNotReady,
    JobResult,
    OpenedArtifact,
)

_PROFILE_KINDS = {"json": "audit_json", "md": "audit_md"}


class DeliveryUnavailable(ValueError):
    """A request cannot safely be delivered and must not claim success."""


@dataclass(frozen=True)
class ReportProfile:
    """A supported retained-report view; formatting never refetches the site."""

    format: str
    findings_only: bool = False

    def artifact_kind(self) -> str:
        if self.findings_only:
            raise DeliveryUnavailable(
                "findings-only delivery is unavailable from the retained remote artifact"
            )
        try:
            return _PROFILE_KINDS[self.format]
        except KeyError:
            raise DeliveryUnavailable(
                f"report format {self.format!r} is unavailable from retained remote artifacts"
            ) from None


class DeliveryReceipts:
    """Small private receipt store that survives adapter restart and retry."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            raise ValueError("delivery receipt store must be private")
        with sqlite3.connect(self.path) as con:
            con.execute(
                """CREATE TABLE IF NOT EXISTS deliveries (
                    job_id TEXT NOT NULL, artifact_id TEXT NOT NULL, destination TEXT NOT NULL,
                    receipt TEXT NOT NULL, state TEXT NOT NULL,
                    PRIMARY KEY(job_id, artifact_id, destination)
                )"""
            )
        os.chmod(self.path, 0o600)

    def reserve(self, job_id: str, artifact_id: str, destination: str) -> tuple[str, str]:
        """Claim one delivery; a concurrent caller receives ``in_progress``."""
        with sqlite3.connect(self.path, isolation_level=None) as con:
            con.execute("BEGIN IMMEDIATE")
            existing = con.execute(
                "SELECT receipt,state FROM deliveries WHERE job_id=? AND artifact_id=? AND destination=?",
                (job_id, artifact_id, destination),
            ).fetchone()
            if existing is not None:
                if existing[1] == "delivered":
                    con.commit()
                    return existing[0], "delivered"
                if existing[1] == "sending":
                    con.commit()
                    return existing[0], "in_progress"
                con.execute(
                    "UPDATE deliveries SET state='sending' WHERE job_id=? AND artifact_id=? AND destination=?",
                    (job_id, artifact_id, destination),
                )
                con.commit()
                return existing[0], "claimed"
            receipt = uuid.uuid4().hex
            con.execute(
                "INSERT INTO deliveries VALUES(?,?,?,?,?)",
                (job_id, artifact_id, destination, receipt, "sending"),
            )
            con.commit()
            return receipt, "claimed"

    def retry(self, job_id: str, artifact_id: str, destination: str, receipt: str) -> None:
        """Release a failed claim so an explicit later attempt can retry it."""
        with sqlite3.connect(self.path) as con:
            con.execute(
                """UPDATE deliveries SET state='pending' WHERE job_id=? AND artifact_id=?
                   AND destination=? AND receipt=? AND state='sending'""",
                (job_id, artifact_id, destination, receipt),
            )

    def delivered(self, job_id: str, artifact_id: str, destination: str, receipt: str) -> None:
        with sqlite3.connect(self.path) as con:
            changed = con.execute(
                """UPDATE deliveries SET state='delivered' WHERE job_id=? AND artifact_id=?
                   AND destination=? AND receipt=?""",
                (job_id, artifact_id, destination, receipt),
            ).rowcount
        if changed != 1:
            raise DeliveryUnavailable("delivery receipt changed while sending")


@dataclass
class AuthorizedReportDelivery:
    """Deliver only complete, authorized retained artifacts through an injected transport."""

    backend: JobBackend
    projects: Iterable[str]
    allowed_destinations: Iterable[str]
    receipts: DeliveryReceipts
    send: Callable[[str, OpenedArtifact, str], None]
    _projects: frozenset[str] = field(init=False, repr=False)
    _destinations: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._projects = frozenset(self.projects)
        self._destinations = frozenset(self.allowed_destinations)
        if not self._projects or not self._destinations:
            raise ValueError("delivery requires explicit projects and destinations")

    def _result(self, project_id: str, job_id: str) -> JobResult:
        if project_id not in self._projects:
            raise PermissionError("project is not authorized for delivery")
        try:
            result = self.backend.get_result(project_id, job_id)
        except JobNotReady:
            result = None
        if result is None or result.coverage != "complete":
            raise DeliveryUnavailable("a complete retained audit is required before delivery")
        return result

    @staticmethod
    def _artifact(result: JobResult, profile: ReportProfile) -> ArtifactReference:
        kind = profile.artifact_kind()
        for artifact in result.artifacts:
            if artifact.kind == kind:
                return artifact
        raise DeliveryUnavailable("the requested retained report artifact is unavailable")

    def preview(self, project_id: str, job_id: str, profile: ReportProfile) -> ArtifactReference:
        """Return the exact artifact that delivery would use, without a side effect."""
        return self._artifact(self._result(project_id, job_id), profile)

    def deliver(
        self, project_id: str, job_id: str, destination: str, profile: ReportProfile
    ) -> str:
        """Send once, or return the durable receipt from a prior successful attempt."""
        if destination not in self._destinations:
            raise PermissionError("destination is not authorized for delivery")
        artifact = self.preview(project_id, job_id, profile)
        receipt, state = self.receipts.reserve(job_id, artifact.artifact_id, destination)
        if state == "delivered":
            return receipt
        if state == "in_progress":
            raise DeliveryUnavailable("delivery is already in progress")
        opened = self.backend.open_artifact(project_id, job_id, artifact.artifact_id)
        if opened is None:
            self.receipts.retry(job_id, artifact.artifact_id, destination, receipt)
            raise DeliveryUnavailable("the retained report artifact is missing or expired")
        try:
            with opened.handle:
                self.send(destination, opened, receipt)
        except BaseException:
            self.receipts.retry(job_id, artifact.artifact_id, destination, receipt)
            raise
        self.receipts.delivered(job_id, artifact.artifact_id, destination, receipt)
        return receipt
