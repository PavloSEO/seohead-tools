"""Verify a co-shipped core before attaching its producer build identity."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path


_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def verified_bundle_commit(executable: str) -> str | None:
    """Return a bundled core commit only after manifest/path/hash verification.

    External development CLIs deliberately return ``None``. Their own core
    controls build identity and Desktop must not invent a producer revision.
    """
    cli = Path(executable).resolve()
    for parent in (cli.parent, *cli.parents):
        manifest = parent / "core-manifest.json"
        if not manifest.is_file():
            continue
        try:
            document = json.loads(manifest.read_text(encoding="utf-8"))
            core = document["core"]
            relative = core["cli_relpath"]
            declared = (manifest.parent / relative).resolve()
            digest = core["cli_sha256"]
            commit = core["commit"]
            if (
                document.get("schema") != "seohead.desktop.core-manifest.v1"
                or not isinstance(relative, str)
                or declared != cli
                or not declared.is_relative_to(manifest.parent.resolve())
                or not isinstance(digest, str)
                or not _COMMIT.fullmatch(str(commit))
            ):
                return None
            with cli.open("rb") as stream:
                actual = hashlib.file_digest(stream, "sha256").hexdigest()
            return str(commit) if actual == digest else None
        except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError):
            return None
    return None
