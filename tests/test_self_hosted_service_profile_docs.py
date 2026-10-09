"""Keep the operator profile aligned with the intentionally explicit service API."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_operator_profile_names_current_service_boundaries_without_claiming_a_listener():
    guide = (ROOT / "docs" / "SELF_HOSTED_SERVICE_PROFILE.md").read_text(encoding="utf-8")

    for value in (
        "create_app(backend, authenticator,\ntarget_policy=...)",
        "SQLiteJobBackend",
        "TokenAuthenticator",
        "RemoteTargetPolicy",
        "127.0.0.1:<private-port>",
        "prune_terminal(project_id, before=...)",
        "incident record",
        "SQLite-consistent snapshot",
    ):
        assert value in guide
    assert "0.0.0.0" not in guide
    assert "/healthz" in guide
    app_source = (ROOT / "seohead" / "integrations" / "remote_api" / "app.py").read_text(
        encoding="utf-8"
    )
    backend_source = (ROOT / "seohead" / "integrations" / "remote_api" / "backend.py").read_text(
        encoding="utf-8"
    )
    assert '@app.get("/healthz")' not in app_source
    assert "def create_app(" in app_source
    assert "def prune_terminal(" in backend_source


def test_remote_api_points_to_the_operator_profile():
    remote_api = (ROOT / "docs" / "REMOTE_API.md").read_text(encoding="utf-8")
    assert "[SELF_HOSTED_SERVICE_PROFILE.md](SELF_HOSTED_SERVICE_PROFILE.md)" in remote_api
