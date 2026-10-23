"""Read-only wall-clock health for a *living* continuous Basilisk session.

All numbers are observations from the host OS, the real TelemetryBus and
the fsynced SessionWorkspace ledger. Never derive wall-clock endurance from
simulated seconds, and never mutate the spacecraft, replay TC, or scan the
ever-growing trace on each status poll.
"""
from __future__ import annotations

import os
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import SessionState

_SCHEMA = "sat-continuous-observability.v1"


def _linux_process_metrics(pid: int) -> dict[str, Any]:
    """Best-effort current /proc observation, not process identity proof."""
    data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    tail = data.rsplit(") ", 1)[1].split()
    if len(tail) < 22 or tail[0] in ("Z", "X"):
        raise ProcessLookupError("PROCESS_NOT_RUNNING")
    hz = os.sysconf("SC_CLK_TCK")
    page = os.sysconf("SC_PAGE_SIZE")
    return {
        "resident_bytes": max(0, int(tail[21])) * page,
        "cpu_time_s": (int(tail[11]) + int(tail[12])) / hz,
        "process_state": tail[0],
        "source": "LINUX_PROCFS",
    }


def _windows_process_metrics(pid: int) -> dict[str, Any]:
    import ctypes
    from ctypes import wintypes

    PROCESS_QUERY_INFORMATION = 0x0400
    PROCESS_VM_READ = 0x0010

    class PROCESS_MEMORY_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("cb", wintypes.DWORD),
            ("PageFaultCount", wintypes.DWORD),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
        ]

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel.CloseHandle.restype = wintypes.BOOL
    kernel.GetProcessTimes.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME), ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    kernel.GetProcessTimes.restype = wintypes.BOOL
    psapi.GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE, ctypes.POINTER(PROCESS_MEMORY_COUNTERS), wintypes.DWORD,
    ]
    psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
    handle = kernel.OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ, False, pid)
    if not handle:
        raise ProcessLookupError("PROCESS_RESOURCE_UNAVAILABLE")
    try:
        mem = PROCESS_MEMORY_COUNTERS()
        mem.cb = ctypes.sizeof(mem)
        if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(mem), mem.cb):
            raise OSError("WINDOWS_MEMORY_SAMPLE_FAILED")
        c, e, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
        if not kernel.GetProcessTimes(
            handle, ctypes.byref(c), ctypes.byref(e),
            ctypes.byref(kernel_time), ctypes.byref(user_time),
        ):
            raise OSError("WINDOWS_CPU_SAMPLE_FAILED")
        ticks = lambda ft: (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        return {
            "resident_bytes": int(mem.WorkingSetSize),
            "cpu_time_s": (ticks(kernel_time) + ticks(user_time)) / 10_000_000.,
            "process_state": "RUNNING_OR_EXITING",
            "source": "WINDOWS_PROCESS_API",
        }
    finally:
        kernel.CloseHandle(handle)


def process_resource_observation(pid: int | None) -> dict[str, Any]:
    """Unknown/inaccessible measurements stay unavailable, never fabricated."""
    if pid is None or type(pid) is not int or pid <= 0:
        return {
            "available": False, "pid": pid, "resident_bytes": None,
            "cpu_time_s": None, "process_state": None,
            "source": "UNAVAILABLE", "reason_code": "NO_LIVE_PROCESS_HANDLE",
        }
    try:
        if sys.platform == "linux":
            row = _linux_process_metrics(pid)
        elif sys.platform == "win32":
            row = _windows_process_metrics(pid)
        else:
            raise RuntimeError("PROCESS_RESOURCE_PLATFORM_UNSUPPORTED")
    except (OSError, RuntimeError, ValueError, IndexError) as exc:
        return {
            "available": False, "pid": pid, "resident_bytes": None,
            "cpu_time_s": None, "process_state": None,
            "source": "UNAVAILABLE", "reason_code": type(exc).__name__,
        }
    return {"available": True, "pid": pid, "reason_code": None, **row}


def observe_continuous_session(session: Any) -> dict[str, Any]:
    """Observations only: no native RPC and no session-worker disturbance."""
    if not session.spec.continuous:
        raise ValueError("NOT_A_CONTINUOUS_SESSION")
    now_wall = datetime.now(timezone.utc)
    elapsed = max(0., time.monotonic() - session._created_monotonic)
    snapshot = session.snapshot()
    stream = (
        session.allowed_telemetry_streams[0]
        if session.allowed_telemetry_streams else "whole.continuous"
    )
    last = session.telemetry.latest_frame(stream)
    lag = max(0., (now_wall - last.wall_time).total_seconds()) if last else None
    if snapshot.state in (SessionState.PAUSED, SessionState.READY):
        tm_status = "PAUSED_OR_READY"
    elif last is None:
        tm_status = "NO_TELEMETRY"
    elif snapshot.state in (SessionState.FAILED, SessionState.ABORTED,
                            SessionState.COMPLETED, SessionState.INTERRUPTED):
        tm_status = "TERMINAL"
    elif lag is not None and lag > max(5., session.spec.quantum_s * 5):
        tm_status = "LAGGING"
    else:
        tm_status = "FRESH"
    workspace = session._workspace
    tm = workspace.telemetry_write_statistics() if workspace is not None else None
    disk = None
    if workspace is not None:
        try:
            usage = shutil.disk_usage(workspace.path)
            disk = {
                "available": True, "free_bytes": usage.free,
                "total_bytes": usage.total,
                "reserved_bytes": session.spec.telemetry_min_free_disk_bytes,
            }
        except OSError:
            disk = {
                "available": False, "free_bytes": None, "total_bytes": None,
                "reserved_bytes": session.spec.telemetry_min_free_disk_bytes,
            }
    runtime = session.runtime
    child = getattr(runtime, "child_pid", None)
    supervised = runtime.__class__.__name__ == "SupervisedContinuousNativeRuntime"
    if supervised:
        # Only sample the directly owned, currently living child. A PID left
        # in last_reap is historical and must NOT resolve to a reused process.
        owned = getattr(runtime, "_process", None)
        pid = child if owned is not None and owned.is_alive() else None
        scope = "BASILISK_CHILD"
    else:
        pid = os.getpid()
        scope = "API_AND_BASILISK_PARENT"
    process = process_resource_observation(pid)
    process["scope"] = scope
    return {
        "schema_version": _SCHEMA,
        "sampled_at": now_wall.isoformat(),
        "session_id": session.spec.session_id,
        "session_state": snapshot.state.value,
        "wall_elapsed_s": elapsed,
        "sim_elapsed_s": snapshot.sim_time_s,
        "telemetry": {
            "stream_id": stream, "status": tm_status,
            "lag_wall_s": lag,
            "last_sequence": last.sequence if last else None,
            "last_sim_time_s": last.sim_time_s if last else None,
            "last_wall_time": last.wall_time.isoformat() if last else None,
            "durable_frame_count": tm["durable_frame_count"] if tm else None,
            "durable_bytes": tm["durable_telemetry_bytes"] if tm else None,
        },
        "process": process,
        "disk": disk,
        "resume_supported": False,
        "automatic_tc_replay_allowed": False,
    }
