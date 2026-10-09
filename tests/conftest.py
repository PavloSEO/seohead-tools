"""Shared pytest fixtures."""

from __future__ import annotations

import os
import shutil

import pytest

from seohead.sf.core.audit import run_audit

FIXTURES = os.path.join(os.path.dirname(__file__), "fixtures")


@pytest.fixture
def exports_dir(tmp_path):
    """A temp exports dir holding both fixture CSVs."""
    dst = tmp_path / "exports"
    dst.mkdir()
    for name in os.listdir(FIXTURES):
        shutil.copy(os.path.join(FIXTURES, name), dst / name)
    return str(dst)


@pytest.fixture
def internal_only_dir(tmp_path):
    """A temp exports dir with only Internal:All (no inlinks)."""
    dst = tmp_path / "exports_internal"
    dst.mkdir()
    shutil.copy(os.path.join(FIXTURES, "internal_all.csv"), dst / "internal_all.csv")
    return str(dst)


@pytest.fixture
def result(exports_dir):
    return run_audit(input_mode="parse-exports", exports_dir=exports_dir, log=lambda m: None)


def checks_in(result) -> set[str]:
    return {i.check for i in result.issues}


def issues_of(result, check):
    return [i for i in result.issues if i.check == check]


@pytest.fixture(autouse=True)
def isolated_user_configuration(tmp_path, monkeypatch):
    """Isolate shared MCP state; client tests additionally provide a temporary HOME."""
    home = tmp_path / "isolated-home"
    monkeypatch.setenv("SEOHEAD_CONFIG_DIR", str(home / ".config/seohead"))
