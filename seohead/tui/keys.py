"""Minimal POSIX raw-mode key reader for the interactive shell.

Deliberately stdlib-only: the shell's single third-party dependency is the
renderer (``rich``), so keyboard input uses ``termios``/``tty`` plus a short
selector window to disambiguate a lone Escape from an escape sequence.
"""

from __future__ import annotations

import contextlib
import os
import selectors
import termios
import time
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
    "[3~": "delete",
    "OA": "up",
    "OB": "down",
    "OH": "home",
    "OF": "end",
    "[200~": "paste_start",
    "[201~": "paste_end",
}

#: How long to wait for the rest of an escape sequence before deciding the
#: key was a lone Escape. 50 ms is imperceptible interactively and generous
#: enough for a PTY writing all three bytes in one call.
ESCAPE_WINDOW_SECONDS = 0.05


def _ready(fd: int, timeout: float) -> bool:
    """Wait for terminal/pipe input without select's FD_SETSIZE ceiling."""
    with selectors.DefaultSelector() as selector:
        selector.register(fd, selectors.EVENT_READ)
        return bool(selector.select(timeout))


@contextlib.contextmanager
def raw_mode(fd: int) -> Iterator[None]:
    """Put the terminal into cbreak-raw mode for the duration of the shell."""
    previous = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        # Input is raw, but terminal output still needs LF -> CRLF. Without
        # this, each Rich line starts at the previous line's column.
        attributes = termios.tcgetattr(fd)
        attributes[1] |= termios.OPOST | termios.ONLCR
        termios.tcsetattr(fd, termios.TCSANOW, attributes)
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, previous)


def _read_available(fd: int, budget: float) -> str:
    """Drain bytes already queued within ``budget`` seconds total."""
    chunk = ""
    deadline = time.monotonic() + budget
    while True:
        if not _ready(fd, max(0.0, deadline - time.monotonic())):
            return chunk
        data = os.read(fd, 1).decode("utf-8", errors="replace")
        if not data:
            return chunk
        chunk += data
        if chunk in _SEQUENCES:
            return chunk
        if chunk.startswith("[") and len(chunk) > 1 and "@" <= chunk[-1] <= "~":
            return chunk
        if len(chunk) >= 64 or (
            not chunk.startswith("[") and not any(seq.startswith(chunk) for seq in _SEQUENCES)
        ):
            return chunk


def read_key(fd: int, timeout: float | None = None) -> str:
    """Read one keypress, or return ``timeout`` for a bounded observer refresh."""
    if timeout is not None and not _ready(fd, timeout):
        return "timeout"
    first_byte = os.read(fd, 1)
    if not first_byte:
        return "ctrl_d"
    if first_byte != b"\x1b":
        leading = first_byte[0]
        width = 1 if leading < 0x80 else 2 if leading < 0xE0 else 3 if leading < 0xF0 else 4
        data = first_byte
        while len(data) < width:
            following = os.read(fd, width - len(data))
            if not following:
                return "char:\ufffd"
            data += following
        first = data.decode("utf-8", errors="replace")
        return _SPECIAL.get(first, f"char:{first}")
    first = "\x1b"
    tail = _read_available(fd, ESCAPE_WINDOW_SECONDS)
    if not tail:
        return "escape"
    return _SEQUENCES.get(tail, "unknown")
