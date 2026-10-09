"""Narrow CLI adapter for settings. All core reads run outside the Qt UI thread.

Only redacted, declared result fields cross into the UI. No credential writing,
paid operations, sync, OAuth client or app-settings authority lives here.
"""

from __future__ import annotations

import json
import math
import re
import subprocess
from datetime import datetime

from PyQt5 import sip
from PyQt5.QtCore import QObject, QRunnable, QThreadPool, pyqtSignal

IDENTIFIER = re.compile(r"[a-zA-Z0-9_.:-]{1,100}\Z")
REFERENCE = re.compile(r"(?:config:[a-zA-Z0-9_./-]+|env:[A-Z0-9_]+|local-grant:[a-z0-9_]+)\Z")
STATES = {"configured_unverified", "missing", "invalid", "not_required", "not_configured",
          "verified", "ready", "connected", "expired", "revoked", "waiting", "failed"}
VERIFY_PROVIDERS = {"gsc", "yandex_webmaster", "bing_webmaster"}


def redacted_providers(providers):
    """Configuration is never relabelled as authenticated access."""
    result = {}
    for pid, entry in (providers or {}).items():
        if not IDENTIFIER.fullmatch(pid) or not isinstance(entry, dict):
            continue
        refs = {}
        for component, source in (entry.get("credential_sources") or {}).items():
            if not isinstance(source, dict) or not IDENTIFIER.fullmatch(component):
                continue
            references = [r for r in source.get("accepted_source_references", [])
                          if isinstance(r, str) and REFERENCE.fullmatch(r)]
            reference = source.get("source_reference")
            refs[component] = {"source_reference": reference if isinstance(reference, str) and REFERENCE.fullmatch(reference) else None,
                               "accepted_source_references": references}
        state = entry.get("readiness_state", entry.get("state"))
        result[pid] = {"readiness_state": state if state in STATES else "unknown",
                       "verified": entry.get("verified") is True,
                       "credential_components": {k: v is True for k, v in (entry.get("credential_components") or {}).items() if IDENTIFIER.fullmatch(k)},
                       "credential_sources": refs}
    return result


def redacted_spend(report):
    def totals(mapping):
        result = {}
        for name, values in (mapping or {}).items():
            if not IDENTIFIER.fullmatch(name) or not isinstance(values, dict):
                continue
            units = {u: v for u, v in values.items() if IDENTIFIER.fullmatch(u)
                     and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)}
            if units:
                result[name] = units
        return result

    calls = report.get("calls")
    since = report.get("since")
    return {"calls": calls if isinstance(calls, int) and not isinstance(calls, bool) and calls >= 0 else None,
            "since": since if isinstance(since, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", since) else None,
            "by_source": totals(report.get("by_source")),
            "by_operation": totals(report.get("by_operation")),
            "by_day": totals(report.get("by_day")),
            "uncertain_count": len(report.get("uncertain") or [])}


def read_cli(executable, arguments):
    """Never forward stderr, exceptions or arbitrary JSON (may contain secrets)."""
    completed = subprocess.run([executable, *arguments], capture_output=True, text=True,
                               timeout=45, check=False)
    if completed.returncode != 0 or len(completed.stdout) > 4 * 1024 * 1024:
        raise ValueError("Core command unavailable")
    value = json.loads(completed.stdout)
    if not isinstance(value, dict) or value.get("ok") is False:
        raise ValueError("Core command refused")
    return value


def load_sources(executable, operation, project=None, provider=None, *, call=read_cli):
    """Explicit operation allowlist; this adapter cannot launch a paid call or sync."""
    if operation == "snapshot":
        result = {"errors": []}
        commands = {"registry": ["provider-registry"], "readiness": ["provider-readiness"],
                    "spend": ["spend-report", "--since", datetime.now().strftime("%Y-%m-01")]}
        if project:
            commands["status"] = ["sources-status", "--project", project]
        for name, args in commands.items():
            try:
                value = call(executable, args)
                if name == "registry":
                    result[name] = {pid: {"credential_components": [c for c in e.get("credential_components", []) if IDENTIFIER.fullmatch(c)],
                                          "paid": "paid" in e.get("access", "")}
                                    for pid, e in value.get("providers", {}).items() if IDENTIFIER.fullmatch(pid)}
                elif name == "readiness":
                    result[name] = redacted_providers(value.get("providers"))
                elif name == "spend":
                    result[name] = redacted_spend(value)
                else:
                    result[name] = [{k: e.get(k) for k in ("source", "resource", "rows", "last_date", "last_fetched_at", "synced_days", "requested_days")}
                                    for e in value.get("sources", [])[:200]]
            except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
                result["errors"].append(name)
        return result
    if operation == "doctor":
        value = call(executable, ["sources-doctor"])
        return {"providers": redacted_providers(value.get("provider_status"))}
    if operation == "auth-status" and provider == "gsc":
        value = call(executable, ["provider-auth", "--provider", provider, "--action", "status"])
        return {"configured": value.get("configured") is True, "access_verified": value.get("access_verified") is True}
    if operation == "verify" and provider in VERIFY_PROVIDERS:
        value = call(executable, ["provider-verify", "--provider", provider])
        return {"verified": value.get("verified") is True, "state": value.get("state") if value.get("state") in STATES else "unknown"}
    raise ValueError("Unsupported settings operation")


class _Signals(QObject):
    done = pyqtSignal(object)
    failed = pyqtSignal(str)


class _Read(QRunnable):
    def __init__(self, executable, operation, project, provider):
        super().__init__()
        self.signals = _Signals()
        self.args = executable, operation, project, provider

    def run(self):
        try:
            self.signals.done.emit(load_sources(*self.args))
        except (OSError, ValueError, TypeError, AttributeError, subprocess.SubprocessError):
            self.signals.failed.emit("Ядро не вернуло данные. Повторите чтение.")


class SourceService(QObject):
    """Owns workers and drops callbacks for deleted/reset settings pages."""

    def __init__(self, executable, parent=None):
        super().__init__(parent)
        self.executable = executable
        self._pending = {}
        self._serial = 0

    def request(self, operation, callback, on_error, owner, project=None, provider=None):
        if not self.executable:
            on_error("CLI ядра seohead не найден")
            return
        self._serial += 1
        serial = self._serial
        worker = _Read(self.executable, operation, project, provider)
        self._pending[serial] = worker

        def deliver(handler, value):
            self._pending.pop(serial, None)
            if not sip.isdeleted(owner):
                handler(value)

        # The signal receivers live on the UI thread, even when the page closed.
        worker.signals.done.connect(lambda value: deliver(callback, value))
        worker.signals.failed.connect(lambda value: deliver(on_error, value))
        QThreadPool.globalInstance().start(worker)
