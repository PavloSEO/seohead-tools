"""Color, typography and status-badge contract for the interactive shell.

Everything here is plain data plus string helpers so the module imports
without ``rich`` — the renderer turns the returned markup into styled output
when color is enabled and into literal text when it is not.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

#: Status kinds the shell can show. ``ok`` marks a wired capability, ``info``
#: a neutral fact (e.g. terminal size), ``warn`` a degraded mode, ``off`` a
#: disabled feature. Anything not in this set must not be rendered as a badge.
STATUS_KINDS = ("ok", "warn", "info", "off")

_STATUS_STYLE = {
    "ok": "bold green",
    "warn": "bold yellow",
    "info": "bold #1565C0",
    "off": "dim",
}

_STATUS_GLYPH = {
    "ok": "●",
    "warn": "▲",
    "info": "■",
    "off": "○",
}

#: Plain-text fallbacks used when color is off, so the badge still reads as a
#: status rather than a bare word. ASCII only: a dumb terminal must render it.
_STATUS_ASCII = {
    "ok": "[OK]",
    "warn": "[!]",
    "info": "[i]",
    "off": "[-]",
}


@dataclass(frozen=True)
class Palette:
    """Resolved display mode for one shell run."""

    color: bool
    #: Style names consumed by the renderer; kept symbolic so a future theme
    #: change touches this file only.
    accent: str = "bold #1565C0"
    title: str = "bold #f1f5f9"
    body: str = ""
    muted: str = "#94a3b8"
    highlight: str = "bold #f8fafc on #1e3a5f"


def color_enabled(
    *,
    no_color_flag: bool = False,
    env: Mapping[str, str] | None = None,
    stdout_is_tty: bool = True,
) -> bool:
    """Whether the shell may emit styled output.

    Order: an explicit ``--no-color`` flag wins, then the ``NO_COLOR``
    convention (any present value, including empty), then a ``TERM`` that
    declares no capabilities, then a non-tty stdout.
    """
    if no_color_flag:
        return False
    env = os.environ if env is None else env
    if "NO_COLOR" in env:
        return False
    if env.get("TERM", "") == "dumb":
        return False
    return stdout_is_tty


def resolve_palette(*, color: bool) -> Palette:
    return Palette(color=color)


def badge(kind: str, label: str, palette: Palette) -> str:
    """Rich-markup badge string; degrades to an ASCII tag when color is off."""
    if kind not in _STATUS_STYLE:
        raise ValueError(f"unknown status kind: {kind!r} (expected one of {STATUS_KINDS})")
    if not palette.color:
        return f"{_STATUS_ASCII[kind]} {label}"
    return f"[{_STATUS_STYLE[kind]}]{_STATUS_GLYPH[kind]} {label}[/]"
