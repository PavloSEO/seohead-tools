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


def atomic_write_bytes(path: str | Path, data: bytes, *, mode: int = 0o600) -> None:
    """Publish ``data`` at ``path`` through a unique temp file in the same directory.

    The temp file is fsynced before ``os.replace`` and the directory is fsynced after,
    so readers see either the previous or the complete new content. The temp file is
    removed when publication fails.
    """
    target = Path(path)
    descriptor, staged = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(staged, mode)
        os.replace(staged, target)
        fsync_directory(target.parent)
    finally:
        Path(staged).unlink(missing_ok=True)
