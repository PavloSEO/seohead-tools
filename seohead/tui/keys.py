"""Minimal POSIX raw-mode key reader for the interactive shell.

Deliberately stdlib-only: the shell's single third-party dependency is the
renderer (``rich``), so keyboard input uses ``termios``/``tty`` plus a short
``select`` window to disambiguate a lone Escape from an escape sequence.
"""

from __future__ import annotations

import contextlib
import os
import select
import termios
import tty
from collections.abc import Iterator

#: Symbolic names returned by :func:`read_key`; printable input comes back as
#: ``"char:<c>"`` so callers never have to distinguish length-1 strings.
_SPECIAL = {
    "\r": "enter",
    "\n": "enter",
    "\x7f": "backspace",
    "\x08": "backspace",
    "\x03": "ctrl_c",
    "\x04": "ctrl_d",
    "\t": "tab",
}

_SEQUENCES = {
    "[A": "up",
    "[B": "down",
    "[C": "right",
    "[D": "left",
    "[H": "home",
    "[F": "end",
    "[5~": "page_up",
    "[6~": "page_down",
    "OA": "up",
    "OB": "down",
    "OH": "home",
    "OF": "end",
}

#: How long to wait for the rest of an escape sequence before deciding the
#: key was a lone Escape. 50 ms is imperceptible interactively and generous
#: enough for a PTY writing all three bytes in one call.
ESCAPE_WINDOW_SECONDS = 0.05


@contextlib.contextmanager
def raw_mode(fd: int) -> Iterator[None]:
    """Put the terminal into cbreak-raw mode for the duration of the shell."""
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def _read_available(fd: int, budget: float) -> str:
    """Drain bytes already queued within ``budget`` seconds total."""
    chunk = ""
    while True:
        ready, _, _ = select.select([fd], [], [], budget)
        if not ready:
            return chunk
        data = os.read(fd, 8).decode("utf-8", errors="replace")
        if not data:
            return chunk
        chunk += data
        if chunk in _SEQUENCES:
            return chunk
        if not any(seq.startswith(chunk) for seq in _SEQUENCES):
            return chunk
        budget = 0.0


def read_key(fd: int, timeout: float | None = None) -> str:
    """Read one keypress, or return ``timeout`` for a bounded observer refresh."""
    if timeout is not None:
        ready, _, _ = select.select([fd], [], [], timeout)
        if not ready:
            return "timeout"
    first = os.read(fd, 1).decode("utf-8", errors="replace")
    if not first:
        return "ctrl_d"
    if first != "\x1b":
        return _SPECIAL.get(first, f"char:{first}")
    tail = _read_available(fd, ESCAPE_WINDOW_SECONDS)
    if not tail:
        return "escape"
    return _SEQUENCES.get(tail, "escape")
