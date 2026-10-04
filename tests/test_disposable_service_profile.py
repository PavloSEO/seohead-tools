"""The service-profile proof is an owned loopback fixture, never a deployment."""

from __future__ import annotations

import json

from scripts import disposable_service_profile


def test_disposable_tls_proxy_profile_writes_redacted_metrics(tmp_path):
    metrics_path = tmp_path / "metrics.json"
    assert disposable_service_profile.main(["--metrics-out", str(metrics_path)]) == 0
    metrics = json.loads(metrics_path.read_text())
    assert metrics["fixture"] == "owned-loopback-tls-reverse-proxy"
    assert metrics["tls_verified"] and metrics["artifact_backup_restore"]
    assert metrics["unauthorized_status"] == 401 and metrics["authorized_status"] == 200
    assert "disposable-profile-token" not in metrics_path.read_text()
