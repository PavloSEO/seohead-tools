"""Portable guards for the Linux-only real SSH/tmpfs acceptance fixture."""

from __future__ import annotations

import errno
import http.client
import json
import sqlite3

import pytest

from scripts import disposable_worker_recovery as fixture


def test_non_linux_refuses_before_mount_or_subprocess(monkeypatch, tmp_path):
    monkeypatch.setattr(fixture.sys, "platform", "darwin")
    monkeypatch.setattr(fixture, "run", lambda *_args, **_kwargs: pytest.fail("subprocess started"))
    output = tmp_path / "metrics.json"
    assert (
        fixture.main(["--metrics-out", str(output), "--evidence-out", str(tmp_path / "evidence")])
        == 1
    )
    result = json.loads(output.read_text())
    assert result == {
        "ok": False,
        "stage": "preflight",
        "failure": {"type": "RuntimeError", "stage": "preflight"},
    }
    assert not (tmp_path / "evidence").exists()


def test_fill_guard_cannot_write_into_the_host_filesystem(monkeypatch, tmp_path):
    monkeypatch.setattr(fixture, "require_linux", lambda: None)
    marker = tmp_path / "untouched"
    marker.write_bytes(b"existing data")
    with pytest.raises(ValueError, match="ordinary filesystem"):
        fixture.fill_tmpfs(marker)
    assert marker.read_bytes() == b"existing data"


@pytest.mark.parametrize("value", ["127.0.0.1", "169.254.1.2", "8.8.8.8", "::1", "0.0.0.0"])
def test_target_fixture_accepts_only_owned_rfc1918_addresses(value):
    with pytest.raises(ValueError, match="RFC1918"):
        fixture.private_address(value)


@pytest.mark.parametrize("code", [errno.EACCES, errno.EFBIG, errno.EIO])
def test_other_failures_are_not_counted_as_enospc(code):
    assert not fixture.observed_enospc([{"errno": code}])
    assert not fixture.observed_enospc([{"sqlite_errorcode": 14}])
    assert fixture.observed_enospc([{"errno": errno.ENOSPC}])
    assert fixture.observed_enospc([{"sqlite_errorcode": 13}])


def test_worker_rejects_an_unbound_directory_before_queue_creation(tmp_path):
    state = tmp_path / "state"
    state.mkdir()
    (state / fixture.MARKER).write_text(json.dumps({"build": "different"}))
    with pytest.raises(ValueError, match="not bound"):
        fixture.backend(state, "a" * 40)
    assert not (state / "jobs.sqlite").exists()


def test_failure_metrics_do_not_expose_exception_arguments(monkeypatch, tmp_path):
    secret = "synthetic-do-not-record-token"

    def fail(*_):
        raise RuntimeError(secret)

    monkeypatch.setattr(fixture, "execute", fail)
    output = tmp_path / "metrics.json"
    assert (
        fixture.main(["--metrics-out", str(output), "--evidence-out", str(tmp_path / "out")]) == 1
    )
    assert secret not in output.read_text()


def test_evidence_preserves_sqlite_state_but_not_filler_or_ssh_keys(tmp_path):
    state = tmp_path / "state"
    job = state / "projects" / "fixture" / "job"
    job.mkdir(parents=True)
    (job / "audit.json").write_bytes(b'{"synthetic":true}')
    (job / "owned-filler").write_bytes(b"not evidence")
    (tmp_path / "client-key").write_bytes(b"not for upload")
    with sqlite3.connect(state / "jobs.sqlite") as con:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("CREATE TABLE jobs(job_id TEXT, state TEXT)")
        con.execute("INSERT INTO jobs VALUES('job','failed')")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    fixture.preserve_state(state, evidence)
    with sqlite3.connect(evidence / "jobs.sqlite") as con:
        assert con.execute("SELECT * FROM jobs").fetchall() == [("job", "failed")]
    assert (evidence / "projects/fixture/job/audit.json").read_bytes() == b'{"synthetic":true}'
    assert not list(evidence.rglob("owned-filler"))
    assert not list(evidence.rglob("client-key"))


def test_actual_loopback_asgi_fixture_preserves_authentication(tmp_path):
    pytest.importorskip("uvicorn")
    from seohead.job_contracts import Principal
    from seohead.remote_api.app import TokenAuthenticator, create_app

    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    (state / fixture.MARKER).write_text(
        json.dumps({"fixture": "disposable-worker-recovery.v1", "build": "a" * 40})
    )
    queue = fixture.backend(state, "a" * 40)
    token = "synthetic-fixture-token-00001"
    app = create_app(
        queue,
        TokenAuthenticator(
            {
                TokenAuthenticator.digest(token): Principal(
                    "fixture", {"fixture": frozenset({"scan:read"})}
                )
            }
        ),
        target_policy=queue,
    )
    with fixture.api_server(app) as port:
        client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        client.request("GET", "/api/v1/openapi.json")
        response = client.getresponse()
        assert response.status == 401
        response.read()
        client.request("GET", "/api/v1/openapi.json", headers={"Authorization": f"Bearer {token}"})
        response = client.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["info"]["version"] == "1.0.0"
        client.close()
