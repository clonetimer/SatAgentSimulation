"""Linux kernel parent-death fence for a real Basilisk spawn worker.

PR_SET_PDEATHSIG operates on the process relationship, not a guessed PID or
SQLite lease. Install SIGKILL before constructing native C++ objects and
recheck PPID to close the race where the parent dies during prctl(). On
non-Linux platforms this facility is *not* an equivalent protection; caller
must report unsupported and never claim process-tree quiescence.

The Linux parent-death signal applies to this child only. It is not a
transitive guarantee for detached grandchildren, unrelated subprocesses
or external actuators, and is not a production recovery authorization.
"""
from __future__ import annotations

import os
import signal
import sys


def arm_native_parent_death_fence(expected_parent_pid: int) -> dict[str, object]:
    if type(expected_parent_pid) is not int or expected_parent_pid <= 1:
        raise ValueError("NATIVE_PARENT_FENCE_BAD_EXPECTED_PARENT")
    if os.getppid() != expected_parent_pid:
        raise RuntimeError("NATIVE_PARENT_FENCE_PARENT_DIED_BEFORE_ARM")
    if sys.platform != "linux":
        # macOS/Windows do not offer Linux's exact parent-death primitive
        # through this API. The caller may run an experimental child, but
        # MUST NOT claim protection against parent death.
        return {
            "supported": False,
            "kind": "UNSUPPORTED_PLATFORM",
            "parent_pid": expected_parent_pid,
        }
    import ctypes

    prctl = ctypes.CDLL(None, use_errno=True).prctl
    prctl.argtypes = [
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
        ctypes.c_ulong, ctypes.c_ulong,
    ]
    prctl.restype = ctypes.c_int
    PR_SET_PDEATHSIG = 1
    rc = prctl(PR_SET_PDEATHSIG, signal.SIGKILL, 0, 0, 0)
    if rc != 0:
        raise OSError(ctypes.get_errno(), "NATIVE_PARENT_FENCE_PRCTL_FAILED")
    # If the parent died between the initial PPID sample and prctl, Linux
    # will not retrospectively deliver the death signal. Detect that race
    # and refuse to create the Basilisk graph.
    if os.getppid() != expected_parent_pid:
        raise RuntimeError("NATIVE_PARENT_FENCE_PARENT_DIED_DURING_ARM")
    return {
        "supported": True,
        "kind": "LINUX_PR_SET_PDEATHSIG_SIGKILL",
        "parent_pid": expected_parent_pid,
    }
