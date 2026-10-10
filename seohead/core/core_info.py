"""Machine-readable core identity for `seohead version --json` (issue #979).

Reads only constants and the packaged provenance manifest. No network, no
paths, no config values: the output is safe to show in a UI or paste in a bug
report.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, get_args

from seohead import __version__
from seohead.core.job_contracts import ScanSource

CORE_INFO_FORMAT = "seohead.core-info.v1"


def _revision() -> str | None:
    """Git revision of a clean build, or None for a dev tree without a manifest."""
    from seohead._build.provenance import packaged_provenance

    try:
        return packaged_provenance().revision
    except (OSError, ValueError):
        return None


def core_info(commands: Sequence[str]) -> dict[str, Any]:
    from seohead.projects.workspace import PROJECT_FORMAT, PROJECT_VERSION
    from seohead.storage.ledger import FORMAT_VERSION, USER_VERSION

    return {
        "format": CORE_INFO_FORMAT,
        "package_version": __version__,
        "revision": _revision(),
        "project": {"format": PROJECT_FORMAT, "version": PROJECT_VERSION},
        "ledger": {"format_version": FORMAT_VERSION, "user_version": USER_VERSION},
        "scan_formats": list(get_args(ScanSource.model_fields["format_version"].annotation)),
        "commands": list(commands),
    }
