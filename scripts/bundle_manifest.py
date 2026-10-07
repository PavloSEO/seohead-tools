#!/usr/bin/env python3
"""Create and finalize the provenance manifest for a co-shipped core bundle."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

try:  # pragma: no cover - Python 3.11+ takes the first branch.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised by the Python 3.10 CI job.
    import tomli as tomllib


def _run(*args: str, cwd: Path) -> str:
    return subprocess.run(
        args,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _core_identity(source: Path) -> dict[str, str]:
    root = Path(_run("git", "rev-parse", "--show-toplevel", cwd=source))
    if _run("git", "status", "--porcelain", cwd=root):
        raise ValueError("core source must be a clean Git checkout")
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    project = pyproject.get("project", {})
    if project.get("name") != "seohead-seotools":
        raise ValueError("core source is not the SEOHEAD toolkit")
    remote = _run("git", "config", "--get", "remote.origin.url", cwd=root)
    if "@" in remote:
        raise ValueError("core remote must not contain embedded credentials")
    archive = subprocess.run(
        ("git", "archive", "--format=tar", "HEAD"),
        cwd=root,
        check=True,
        capture_output=True,
    ).stdout
    return {
        "distribution": project["name"],
        "version": project["version"],
        "repository": remote,
        "commit": _run("git", "rev-parse", "HEAD", cwd=root),
        "source_archive_sha256": hashlib.sha256(archive).hexdigest(),
        "optional_dependency_declarations": project.get("optional-dependencies", {}),
    }


def create_manifest(core_source: Path, output: Path, platform: str) -> dict:
    payload = {
        "schema": "seohead.desktop.core-manifest.v1",
        "platform": platform,
        "core": _core_identity(core_source.resolve()),
        "build_environment": {
            "python": sys.version,
            "installed_distributions": dict(
                sorted(
                    (
                        distribution.metadata["Name"].lower(),
                        distribution.version,
                    )
                    for distribution in importlib.metadata.distributions()
                    if distribution.metadata.get("Name")
                )
            ),
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def finalize_manifest(manifest: Path, cli: Path) -> dict:
    payload = json.loads(manifest.read_text(encoding="utf-8"))
    relative = cli.resolve().relative_to(manifest.parent.resolve())
    payload["core"]["cli_relpath"] = relative.as_posix()
    payload["core"]["cli_sha256"] = hashlib.file_digest(cli.open("rb"), "sha256").hexdigest()
    manifest.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("--core-source", type=Path, required=True)
    create.add_argument("--output", type=Path, required=True)
    create.add_argument("--platform", choices=("macos", "linux", "windows"), required=True)
    finalize = commands.add_parser("finalize")
    finalize.add_argument("--manifest", type=Path, required=True)
    finalize.add_argument("--cli", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "create":
        create_manifest(args.core_source, args.output, args.platform)
    else:
        finalize_manifest(args.manifest, args.cli)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
