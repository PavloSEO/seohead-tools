"""Explicit, injected delivery of retained local monitor notices.

There is deliberately no account, endpoint, timer, or transport implementation
here. A host that already has an authorized service destination injects the
small ``send`` callable. The local monitor remains quiet unless that host also
creates this adapter with ``enabled=True`` and calls it explicitly.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import uuid
from collections.abc import Callable, Iterable
from contextlib import closing
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


class DeliveryUnavailable(ValueError):
    """A request cannot safely be delivered and must not claim success."""


class DeliveryReceipts:
    """Small private receipt store that survives adapter restart and retry."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.path.exists() and self.path.stat().st_mode & 0o077:
            raise ValueError("delivery receipt store must be private")
        # Create the file private before SQLite does, so it never exists with wider modes.
        os.close(os.open(self.path, os.O_CREAT | os.O_WRONLY, 0o600))
        with closing(sqlite3.connect(self.path)) as con, con:
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
        with closing(sqlite3.connect(self.path, isolation_level=None)) as con:
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
        with closing(sqlite3.connect(self.path)) as con, con:
            con.execute(
                """UPDATE deliveries SET state='pending' WHERE job_id=? AND artifact_id=?
                   AND destination=? AND receipt=? AND state='sending'""",
                (job_id, artifact_id, destination, receipt),
            )

    def delivered(self, job_id: str, artifact_id: str, destination: str, receipt: str) -> None:
        with closing(sqlite3.connect(self.path)) as con, con:
            changed = con.execute(
                """UPDATE deliveries SET state='delivered' WHERE job_id=? AND artifact_id=?
                   AND destination=? AND receipt=?""",
                (job_id, artifact_id, destination, receipt),
            ).rowcount
        if changed != 1:
            raise DeliveryUnavailable("delivery receipt changed while sending")


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


@dataclass
class MonitorServiceDelivery:
    """Send retained actionable/recovery notices once through an authorized adapter."""

    project_uuid: str
    allowed_destinations: Iterable[str]
    receipts: DeliveryReceipts
    send: Callable[[str, dict[str, Any], str], None]
    enabled: bool = False
    include_recoveries: bool = True
    _destinations: frozenset[str] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._destinations = frozenset(self.allowed_destinations)
        if (
            not isinstance(self.project_uuid, str)
            or not self.project_uuid
            or len(self.project_uuid) > 256
        ):
            raise ValueError("monitor delivery requires a project identity")
        if not self._destinations or any(
            not isinstance(value, str) or not value for value in self._destinations
        ):
            raise ValueError("monitor delivery requires explicit destinations")

    @staticmethod
    def _events(run: dict[str, Any], include_recoveries: bool) -> list[dict[str, Any]]:
        events = [
            {"kind": "alert", "url": alert["url"], "changes": alert["changes"]}
            for alert in run.get("alerts", [])
        ]
        if include_recoveries:
            events.extend(
                {"kind": "recovery", "url": recovery["url"], "change": recovery["change"]}
                for recovery in run.get("recoveries", [])
            )
        return events

    def deliver(self, run: dict[str, Any], destination: str) -> dict[str, Any]:
        """Deliver only retained, complete alert events and return durable receipts."""
        if not self.enabled:
            raise DeliveryUnavailable("monitor service delivery is disabled")
        if destination not in self._destinations:
            raise PermissionError("destination is not authorized for monitor delivery")
        coverage = run.get("coverage")
        if run.get("state") in {"partial", "failed"} or (
            isinstance(coverage, dict) and not coverage.get("complete", False)
        ):
            raise DeliveryUnavailable("partial monitor evidence is retained but is not deliverable")
        scan_id = run.get("scan_id")
        if not isinstance(scan_id, str) or not scan_id:
            raise DeliveryUnavailable("monitor run lacks retained scan identity")
        receipts = []
        for event in self._events(run, self.include_recoveries):
            payload = {"project_uuid": self.project_uuid, "scan_id": scan_id, "event": event}
            artifact_id = hashlib.sha256(_canonical(payload).encode()).hexdigest()
            receipt, state = self.receipts.reserve(self.project_uuid, artifact_id, destination)
            if state == "delivered":
                receipts.append({"receipt": receipt, "state": "delivered", "kind": event["kind"]})
                continue
            if state == "in_progress":
                raise DeliveryUnavailable("monitor delivery is already in progress")
            try:
                self.send(destination, payload, receipt)
            except BaseException:
                self.receipts.retry(self.project_uuid, artifact_id, destination, receipt)
                raise
            self.receipts.delivered(self.project_uuid, artifact_id, destination, receipt)
            receipts.append({"receipt": receipt, "state": "sent", "kind": event["kind"]})
        return {"ok": True, "delivery": "sent" if receipts else "quiet", "receipts": receipts}
