"""Ties a child process's lifetime to this process using a Windows Job Object,
so rtl_fm.exe can never be orphaned holding the SDR device - even if this
process is killed hard (Task Manager "End Task", a crash, taskkill /F)
without running any of its own cleanup code. Windows itself guarantees the
child dies when the job handle closes, if JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
is set.

This is the Windows equivalent of prctl(PR_SET_PDEATHSIG) on Linux; there is
no portable stdlib way to do this, hence the direct ctypes/kernel32 calls.
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Optional

_IS_WINDOWS = sys.platform == "win32"

if _IS_WINDOWS:
    kernel32 = ctypes.windll.kernel32

    JobObjectExtendedLimitInformation = 9
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_int64),
            ("PerJobUserTimeLimit", ctypes.c_int64),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_size_t),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_uint64),
            ("WriteOperationCount", ctypes.c_uint64),
            ("OtherOperationCount", ctypes.c_uint64),
            ("ReadTransferCount", ctypes.c_uint64),
            ("WriteTransferCount", ctypes.c_uint64),
            ("OtherTransferCount", ctypes.c_uint64),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]


class JobObjectUnavailableError(RuntimeError):
    pass


class ChildLifetimeJob:
    """Create once, assign each child process you spawn via assign(process).
    All assigned processes are force-killed when close() runs OR when this
    process exits/dies for any reason (the OS owns that guarantee, not us).
    """

    def __init__(self):
        self._handle: Optional[int] = None
        if not _IS_WINDOWS:
            return

        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise JobObjectUnavailableError(
                f"CreateJobObjectW failed: {ctypes.get_last_error()}"
            )

        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = kernel32.SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            ctypes.byref(info),
            ctypes.sizeof(info),
        )
        if not ok:
            kernel32.CloseHandle(handle)
            raise JobObjectUnavailableError(
                f"SetInformationJobObject failed: {ctypes.get_last_error()}"
            )

        self._handle = handle

    @property
    def available(self) -> bool:
        return self._handle is not None

    def assign(self, popen_process) -> bool:
        """Assigns a subprocess.Popen child to this job. Returns False (and
        does not raise) if unavailable or assignment fails, so callers can
        treat this as best-effort hardening rather than a hard dependency -
        the receiver should still work (just without the orphan guarantee)
        on a system where this somehow isn't available.
        """
        if self._handle is None:
            return False
        win_handle = getattr(popen_process, "_handle", None)
        if win_handle is None:
            return False
        return bool(kernel32.AssignProcessToJobObject(self._handle, int(win_handle)))

    def close(self) -> None:
        if self._handle is not None:
            kernel32.CloseHandle(self._handle)
            self._handle = None
