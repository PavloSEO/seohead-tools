"""The provider capability/workflow matrix stays bound to the code it describes."""

from __future__ import annotations

from seohead.cli import COMMANDS
from seohead.data_sources.providers import provider_registry
from seohead.provider_matrix import (
    SUPPORT_STATES,
    UNSUPPORTED_WORK,
    WORKFLOWS,
    render,
)


def test_every_workflow_status_is_a_declared_state():
    assert {row.status for row in WORKFLOWS} <= set(SUPPORT_STATES)


def test_every_surface_command_is_a_real_cli_command():
    referenced = {command for row in WORKFLOWS for command in row.surface}
    missing = referenced - set(COMMANDS)
    assert not missing, f"matrix references commands that do not exist: {sorted(missing)}"


def test_every_referenced_provider_is_registered():
    registered = set(provider_registry()["providers"])
    for row in WORKFLOWS:
        missing = set(row.providers) - registered
        assert not missing, f"{row.workflow} references unregistered providers: {missing}"


def test_every_registered_provider_appears_in_some_workflow():
    covered = {provider for row in WORKFLOWS for provider in row.providers}
    missing = set(provider_registry()["providers"]) - covered
    assert not missing, f"registered providers absent from the matrix: {sorted(missing)}"


def test_unsupported_work_is_named_not_silent():
    assert UNSUPPORTED_WORK, "unsupported work must stay explicit"
    rendered = render()
    for name, _ in UNSUPPORTED_WORK:
        assert name in rendered


def test_no_provider_is_labelled_live_verified():
    # "verified" is deliberately not a SUPPORT_STATE: a declared registry entry is not
    # evidence of live access, so no status cell may claim it.
    assert "verified" not in SUPPORT_STATES and "live-verified" not in SUPPORT_STATES
    assert "nothing in this matrix is labelled live-verified" in render()


def test_render_is_deterministic_and_covers_the_registry():
    first, second = render(), render()
    assert first == second
    for name in provider_registry()["providers"]:
        assert f"`{name}`" in first
