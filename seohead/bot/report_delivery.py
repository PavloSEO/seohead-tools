"""Authorized, idempotent report handoff over retained remote-job artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import tempfile
import uuid
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from seohead.job_contracts import (
    ArtifactReference,
    JobBackend,
    JobNotReady,
    JobResult,
    OpenedArtifact,
)

_PROFILE_KINDS = {"json": "audit_json", "md": "audit_md"}
_FORMATS = frozenset({"xlsx", "docx", "csv", "md", "json", "pdf"})
_SEVERITIES = frozenset({"critical", "warning", "notice"})


class DeliveryUnavailable(ValueError):
    """A request cannot safely be delivered and must not claim success."""


@dataclass(frozen=True)
class ReportProfile:
    """A supported retained-report view; formatting never refetches the site."""

    format: str
    findings_only: bool = False
    severities: tuple[str, ...] = ()
    checks: tuple[str, ...] = ()
    language: str = "en"

    def __post_init__(self) -> None:
        if self.format not in _FORMATS:
            raise DeliveryUnavailable(f"report format {self.format!r} is unavailable")
        if self.language not in {"en", "ru"}:
            raise DeliveryUnavailable("report language must be 'en' or 'ru'")
        if self.language != "en" and self.format != "pdf":
            raise DeliveryUnavailable("report language is configurable only for PDF output")
        if len(self.severities) != len(set(self.severities)) or any(
            item not in _SEVERITIES for item in self.severities
        ):
            raise DeliveryUnavailable("severity filters support critical, warning, and notice")
        if len(self.checks) != len(set(self.checks)) or any(
            not isinstance(item, str) or not item or len(item) > 128 for item in self.checks
        ):
            raise DeliveryUnavailable("check filters must be unique bounded identifiers")

    def artifact_kind(self) -> str:
        return _PROFILE_KINDS.get(self.format, "audit_json")

    @property
    def needs_render(self) -> bool:
        return (
            self.findings_only
            or bool(self.severities)
            or bool(self.checks)
            or self.format not in _PROFILE_KINDS
            or self.language != "en"
        )

    def fingerprint(self) -> str:
        """Stable receipt component for the exact requested report population."""
        encoded = json.dumps(
            {
                "format": self.format,
                "findings_only": self.findings_only,
                "severities": self.severities,
                "checks": self.checks,
                "language": self.language,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


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
    max_file_bytes: int = 50 * 1024 * 1024
    _projects: frozenset[str] = field(init=False, repr=False)
    _destinations: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._projects = frozenset(self.projects)
        self._destinations = frozenset(self.allowed_destinations)
        if not self._projects or not self._destinations:
            raise ValueError("delivery requires explicit projects and destinations")
        if type(self.max_file_bytes) is not int or not 1 <= self.max_file_bytes <= 2**31:
            raise ValueError("max_file_bytes must be a positive bounded integer")

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
    def _source_artifact(result: JobResult, kind: str) -> ArtifactReference:
        for artifact in result.artifacts:
            if artifact.kind == kind:
                return artifact
        raise DeliveryUnavailable("the required retained report artifact is unavailable")

    @staticmethod
    def _delivery_artifact(source: ArtifactReference, profile: ReportProfile) -> ArtifactReference:
        if not profile.needs_render:
            return source
        artifact_id = hashlib.sha256(
            f"{source.artifact_id}:{profile.fingerprint()}".encode()
        ).hexdigest()[:32]
        return ArtifactReference(
            artifact_id=artifact_id,
            kind=f"delivery_{profile.format}",
            media_type={
                "json": "application/json",
                "md": "text/markdown",
                "csv": "text/csv",
                "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                "pdf": "application/pdf",
            }[profile.format],
            size_bytes=0,
        )

    @staticmethod
    def _filtered_document(document: dict[str, Any], profile: ReportProfile) -> dict[str, Any]:
        """Filter report rows only; source coverage and audit totals remain intact."""
        key = "findings" if isinstance(document.get("findings"), list) else "issues"
        rows = document.get(key)
        if not isinstance(rows, list):
            raise DeliveryUnavailable("retained audit does not contain report findings")
        selected = [
            row
            for row in rows
            if isinstance(row, dict)
            and (not profile.severities or row.get("severity") in profile.severities)
            and (not profile.checks or row.get("check") in profile.checks)
        ]
        if not profile.findings_only and not profile.severities and not profile.checks:
            return document
        summary = dict(document.get("summary") or {})
        summary["finding_view"] = {
            "state": "empty" if not selected else "filtered",
            "counts": {"source_rows": len(rows), "selected_rows": len(selected)},
            "filters": {
                "findings_only": profile.findings_only,
                "severity": list(profile.severities),
                "check": list(profile.checks),
            },
            "coverage_note": "Coverage and skipped checks remain source-wide retained evidence.",
        }
        return {**document, key: selected, "summary": summary}

    @contextmanager
    def _opened_for_profile(
        self, project_id: str, job_id: str, source: ArtifactReference, profile: ReportProfile
    ) -> Iterator[OpenedArtifact]:
        opened = self.backend.open_artifact(project_id, job_id, source.artifact_id)
        if opened is None:
            raise DeliveryUnavailable("the retained report artifact is missing or expired")
        if not profile.needs_render:
            try:
                if opened.size_bytes > self.max_file_bytes:
                    raise DeliveryUnavailable("report exceeds the configured delivery size limit")
                yield opened
            finally:
                opened.handle.close()
            return
        try:
            with opened.handle:
                try:
                    document = json.load(opened.handle)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise DeliveryUnavailable("retained audit JSON cannot be rendered") from exc
        finally:
            # ``opened.handle`` is closed above; this makes the ownership clear
            # when an implementation returns an unusual buffered handle.
            if not opened.handle.closed:
                opened.handle.close()
        if not isinstance(document, dict):
            raise DeliveryUnavailable("retained audit JSON must be an object")
        selected = self._filtered_document(document, profile)
        from seohead.reports import build_report

        with tempfile.TemporaryDirectory(prefix="seohead-delivery-") as directory:
            target = Path(directory) / f"report.{profile.format}"
            built = build_report(
                selected, fmt=profile.format, path=str(target), lang=profile.language
            )
            if not built.get("ok") or not target.is_file():
                raise DeliveryUnavailable(
                    f"requested report profile is unavailable: {built.get('error', 'renderer failed')}"
                )
            size = target.stat().st_size
            if size > self.max_file_bytes:
                raise DeliveryUnavailable("report exceeds the configured delivery size limit")
            with target.open("rb") as handle:
                yield OpenedArtifact(
                    handle=handle,
                    size_bytes=size,
                    filename=target.name,
                    media_type=self._delivery_artifact(source, profile).media_type,
                )

    def preview(self, project_id: str, job_id: str, profile: ReportProfile) -> ArtifactReference:
        """Return the exact artifact that delivery would use, without a side effect."""
        result = self._result(project_id, job_id)
        source = self._source_artifact(
            result, "audit_json" if profile.needs_render else profile.artifact_kind()
        )
        return self._delivery_artifact(source, profile)

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
        try:
            result = self._result(project_id, job_id)
            source = self._source_artifact(
                result, "audit_json" if profile.needs_render else profile.artifact_kind()
            )
            with self._opened_for_profile(project_id, job_id, source, profile) as opened:
                self.send(destination, opened, receipt)
        except BaseException:
            self.receipts.retry(job_id, artifact.artifact_id, destination, receipt)
            raise
        self.receipts.delivered(job_id, artifact.artifact_id, destination, receipt)
        return receipt
