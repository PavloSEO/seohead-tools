"""Explicit, injected delivery of retained local monitor notices.

There is deliberately no account, endpoint, timer, or transport implementation
here. A host that already has an authorized service destination injects the
small ``send`` callable. The local monitor remains quiet unless that host also
creates this adapter with ``enabled=True`` and calls it explicitly.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any

from seohead.bot.report_delivery import DeliveryReceipts, DeliveryUnavailable


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
