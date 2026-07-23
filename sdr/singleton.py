"""Prevents two GrunnAlert Receiver instances from opening the same SDR at once.

Uses an OS-level advisory lock on a lock file. The lock is tied to the process
handle, so it is released automatically if the process crashes or is killed -
no stale-lock cleanup logic needed.
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

try:
    import msvcrt
    _WINDOWS = True
except ImportError:  # pragma: no cover - non-Windows dev/test environments
    import fcntl
    _WINDOWS = False


class AlreadyRunningError(RuntimeError):
    pass


class SingleInstanceLock:
    def __init__(self, lock_path: Path):
        self._lock_path = lock_path
        self._handle: Optional[object] = None

    def acquire(self) -> None:
        self._lock_path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = open(self._lock_path, "a+b")
        try:
            if _WINDOWS:
                # "a+b" mode's initial file position is not guaranteed to be 0
                # on every platform/run; msvcrt.locking locks a byte range
                # starting at the *current* position, so two instances could
                # otherwise lock different, non-overlapping byte ranges and
                # both "succeed". Force a fixed, shared offset.
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:  # pragma: no cover
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._handle.close()
            self._handle = None
            raise AlreadyRunningError(
                f"Another p2000-decoder instance already holds {self._lock_path}"
            ) from exc

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if _WINDOWS:
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None

    def __enter__(self) -> "SingleInstanceLock":
        self.acquire()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()
