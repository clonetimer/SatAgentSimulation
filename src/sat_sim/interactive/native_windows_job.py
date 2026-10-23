"""Windows child process lifetime fence: Job Object with KILL_ON_JOB_CLOSE.

R7d. This is the Windows equivalent of an OS-enforced *directly assigned*
native Basilisk child lifetime dependency. Parent MUST attach the child's
process handle to its Job Object before sending any native prepare RPC.
The Job handle is private to the parent and stays open for the whole run.

Closing the parent's last Job handle (including on parent process crash)
kills associated processes. Never silently fall back to PID polling or a
Python watchdog if any Win32 call fails. Job Objects are not a universal
proof of the absence of unrelated/detached subprocesses or external effects.
"""
from __future__ import annotations

import os
import sys
from typing import Any

JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
JobObjectExtendedLimitInformation = 9
PROCESS_SET_QUOTA = 0x0100
PROCESS_TERMINATE = 0x0001
PROCESS_QUERY_INFORMATION = 0x0400


class WindowsKillOnCloseJob:
    """Exclusive, non-inheritable Windows job handle owned by one parent."""

    def __init__(self, handle: int, child_pid: int):
        self._handle = handle
        self.child_pid = child_pid

    @classmethod
    def attach(cls, child_pid: int) -> "WindowsKillOnCloseJob":
        if sys.platform != "win32":
            raise RuntimeError("NATIVE_WINDOWS_JOB_UNSUPPORTED_PLATFORM")
        if type(child_pid) is not int or child_pid <= 0:
            raise ValueError("NATIVE_WINDOWS_JOB_BAD_CHILD")
        import ctypes
        from ctypes import wintypes

        SIZE_T = ctypes.c_size_t
        DWORD = wintypes.DWORD
        ULONG_PTR = ctypes.c_size_t

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", DWORD),
                ("MinimumWorkingSetSize", SIZE_T),
                ("MaximumWorkingSetSize", SIZE_T),
                ("ActiveProcessLimit", DWORD),
                ("Affinity", ULONG_PTR),
                ("PriorityClass", DWORD),
                ("SchedulingClass", DWORD),
            ]

        class EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", SIZE_T),
                ("JobMemoryLimit", SIZE_T),
                ("PeakProcessMemoryUsed", SIZE_T),
                ("PeakJobMemoryUsed", SIZE_T),
            ]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, DWORD,
        ]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [DWORD, wintypes.BOOL, DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL

        def raise_winerror(stage: str):
            raise OSError(
                ctypes.get_last_error(),
                "NATIVE_WINDOWS_JOB_" + stage,
            )

        job = kernel.CreateJobObjectW(None, None)
        if not job:
            raise_winerror("CREATE_FAILED")
        process = None
        try:
            limits = EXTENDED_LIMIT_INFORMATION()
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(
                job, JobObjectExtendedLimitInformation,
                ctypes.byref(limits), ctypes.sizeof(limits),
            ):
                raise_winerror("LIMIT_FAILED")
            process = kernel.OpenProcess(
                PROCESS_SET_QUOTA | PROCESS_TERMINATE | PROCESS_QUERY_INFORMATION,
                False, child_pid,
            )
            if not process:
                raise_winerror("OPEN_CHILD_FAILED")
            if not kernel.AssignProcessToJobObject(job, process):
                raise_winerror("ASSIGN_FAILED")
            # Caller retains the sole non-inheritable job handle. All native
            # work must start AFTER this returns successfully.
            return cls(int(job), child_pid)
        except BaseException:
            kernel.CloseHandle(job)
            raise
        finally:
            if process:
                kernel.CloseHandle(process)

    @property
    def attached(self) -> bool:
        return self._handle is not None

    def close(self) -> None:
        handle = self._handle
        if handle is None:
            return
        import ctypes
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self._handle = None
        if not kernel.CloseHandle(wintypes.HANDLE(handle)):
            raise OSError(ctypes.get_last_error(), "NATIVE_WINDOWS_JOB_CLOSE_FAILED")


__all__ = ["WindowsKillOnCloseJob"]
