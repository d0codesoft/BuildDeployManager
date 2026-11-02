"""Kernel-owned advisory locks survive crashes without stale lock files."""

from __future__ import annotations

import os
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from scdeploy.errors import BusyError


@contextmanager
def lock(path: Path) -> Iterator[None]:
    """Acquire an exclusive nonblocking lock, released automatically on exit."""
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    try:
        if os.fstat(fd).st_size == 0:
            os.write(fd, b"0")
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            raise BusyError("Operation already running") from None
        yield
    finally:
        os.close(fd)


@contextmanager
def build_slot(directory: Path, count: int) -> Iterator[None]:
    """Limit concurrent builds across independent CLI processes."""
    for number in range(count):
        manager = lock(directory / f"build-slot-{number}.lock")
        try:
            manager.__enter__()
        except BusyError:
            continue
        try:
            yield
        finally:
            manager.__exit__(None, None, None)
        return
    raise BusyError("All build slots are occupied")
