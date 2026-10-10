"""OS-backed file locks and filesystem operations shared by local workflows."""

from __future__ import annotations

import os
import stat
import tempfile
from pathlib import Path

_WINDOWS = os.name == "nt"

try:
    import fcntl
except ImportError:  # Windows uses the CRT's byte-range locks instead.
    fcntl = None
try:
    import msvcrt
except ImportError:
    msvcrt = None


def require_locking() -> None:
    if fcntl is None and msvcrt is None:
        raise OSError("this platform has no supported OS file-lock backend")


def lock_exclusive(fd: int) -> None:
    """Acquire a nonblocking lifetime lock; an empty file is a valid lock carrier."""
    require_locking()
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    else:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)


def unlock(fd: int) -> None:
    require_locking()
    if fcntl is not None:
        fcntl.flock(fd, fcntl.LOCK_UN)
    else:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)


def open_lock(path: Path, *, create: bool = True) -> int:
    """Open an unaliased regular lock file, refusing symlinks and reparse points."""
    if path.is_symlink():
        raise OSError("writer lock path must not be a symlink")
    flags = os.O_RDWR | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
    if create:
        flags |= os.O_CREAT
    fd = os.open(path, flags, 0o600)
    try:
        opened, named = os.fstat(fd), path.lstat()
        if (
            not stat.S_ISREG(opened.st_mode)
            or opened.st_nlink != 1
            or not os.path.samestat(opened, named)
            or getattr(named, "st_file_attributes", 0)
            & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
        ):
            raise OSError("writer lock must be a regular unaliased file")
        return fd
    except BaseException:
        os.close(fd)
        raise


def fsync_directory(path: str | Path) -> None:
    """Flush directory entries on POSIX; Windows has no equivalent CRT operation.

    File writes still use fsync and SQLite still uses synchronous=FULL. Windows
    atomic publication is retained, without claiming POSIX directory-fsync crash
    durability. Unsupported Windows directory handles must not abort a write.
    """
    if _WINDOWS:
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def stage_bytes(
    directory: Path,
    prefix: str,
    data: bytes,
    *,
    suffix: str = "",
    mode: int | None = None,
    durable: bool = True,
) -> str:
    """Write ``data`` to a unique temp file in ``directory`` and return its path.

    The caller publishes the returned path (``os.replace`` or ``os.link``) or removes it.
    ``mkstemp`` guarantees no two callers share a staging name. ``durable`` fsyncs the file
    before it is returned; a failed write removes the temp file before re-raising.
    """
    descriptor, staged = tempfile.mkstemp(prefix=prefix, suffix=suffix, dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            if durable:
                os.fsync(stream.fileno())
        if mode is not None:
            os.chmod(staged, mode)
    except BaseException:
        Path(staged).unlink(missing_ok=True)
        raise
    return staged


def atomic_write_bytes(
    path: Path,
    data: bytes,
    *,
    prefix: str = ".",
    suffix: str = "",
    mode: int | None = None,
    durable: bool = True,
) -> None:
    """Replace ``path`` with ``data`` so readers see the old or the new file, never a mix.

    ``durable`` fsyncs the staged file and then the parent directory, so a completed
    replace survives a crash on POSIX. ``durable=False`` keeps atomicity but skips both
    fsyncs, for caches where losing the newest entry on power loss is acceptable.
    """
    staged = stage_bytes(path.parent, prefix, data, suffix=suffix, mode=mode, durable=durable)
    try:
        os.replace(staged, path)
    finally:
        Path(staged).unlink(missing_ok=True)
    if durable:
        fsync_directory(path.parent)
