"""Prefer allowed performance CPUs for this native worker, never a global policy.

Windows heterogeneous CPU class semantics:
https://learn.microsoft.com/en-us/windows/win32/api/winnt/ns-winnt-system_cpu_set_information
"""
from __future__ import annotations

import os
import sys

import psutil


def _windows_cpu_sets() -> list[dict[str, int]]:
    import ctypes
    from ctypes import wintypes as w
    import struct

    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    kernel.GetSystemCpuSetInformation.argtypes = [
        ctypes.c_void_p, w.ULONG, ctypes.POINTER(w.ULONG), w.HANDLE, w.ULONG]
    kernel.GetSystemCpuSetInformation.restype = w.BOOL
    process = kernel.GetCurrentProcess()  # Pseudo-handle; must not be closed.
    length = w.ULONG()
    kernel.GetSystemCpuSetInformation(None, 0, ctypes.byref(length), process, 0)
    if not 0 < length.value <= 1024 * 1024:
        raise OSError('NATIVE_CPU_SET_BUFFER_INVALID')
    buffer = ctypes.create_string_buffer(length.value)
    if not kernel.GetSystemCpuSetInformation(
            buffer, length, ctypes.byref(length), process, 0):
        raise ctypes.WinError(ctypes.get_last_error())
    offset, result = 0, []
    while offset < length.value:
        if length.value - offset < 8:
            raise OSError('NATIVE_CPU_SET_HEADER_TRUNCATED')
        size, kind = struct.unpack_from('<II', buffer.raw, offset)
        if size < 8 or offset + size > length.value:
            raise OSError('NATIVE_CPU_SET_RECORD_INVALID')
        if kind == 0 and size >= 32:
            _, group, logical, _, _, _, efficiency, flags = struct.unpack_from(
                '<IHBBBBBB', buffer.raw, offset + 8)
            result.append({'group':group, 'logical_processor':logical,
                           'efficiency_class':efficiency, 'flags':flags})
        offset += size
    return result


def _performance_affinity(cpu_sets: list[dict[str, int]], allowed: list[int]) -> list[int]:
    # Do not turn a group-relative index into an unsafe cross-group mask.
    if not cpu_sets or any(row['group'] != 0 for row in cpu_sets):
        return []
    eligible = [row for row in cpu_sets
                if row['logical_processor'] in allowed
                and not row['flags'] & 1  # Parked.
                and not (row['flags'] & 2 and not row['flags'] & 4)  # Allocated elsewhere.
                and not row['flags'] & 8]  # Reserved for real-time use.
    if not eligible:
        return []
    if len({row['efficiency_class'] for row in eligible}) < 2:
        return []  # No heterogeneous choice inside the existing permission mask.
    highest = max(row['efficiency_class'] for row in eligible)
    return sorted({row['logical_processor'] for row in eligible
                   if row['efficiency_class'] == highest})


def configure_native_worker_scheduling() -> dict[str, object]:
    """Self-process only, before graph creation. Failures are disclosed explicitly.

    SAT_SIM_NATIVE_CPU_POLICY=system preserves the operating-system selection.
    No process priority, timer resolution, global power plan, or physics changes.
    """
    policy = os.environ.get('SAT_SIM_NATIVE_CPU_POLICY', 'prefer_performance')
    if policy not in {'system', 'prefer_performance'}:
        raise ValueError('NATIVE_CPU_POLICY_INVALID')
    report: dict[str, object] = {
        'schema_version':'sat-native-cpu-scheduling.v1', 'pid':os.getpid(),
        'requested_policy':policy, 'mode':'SYSTEM_DEFAULT', 'applied':False,
        'scope':'This owned worker only; no global power or process-priority changes.',
    }
    if policy == 'system' or sys.platform != 'win32':
        report['reason'] = 'EXPLICIT_SYSTEM_POLICY' if policy == 'system' else 'PLATFORM_SYSTEM_POLICY'
        return report
    process, original, attempted = None, None, False
    try:
        cpu_sets = _windows_cpu_sets()
        process = psutil.Process()  # Always self; no caller-supplied PID.
        original = process.cpu_affinity()
        selected = _performance_affinity(cpu_sets, original)
        report.update(allowed_affinity=original, selected_affinity=selected)
        if not selected:
            report['reason'] = 'NO_SUPPORTED_ELIGIBLE_CPU_SET'
            return report
        if selected == sorted(original):
            report['reason'] = 'ALREADY_USING_HIGHEST_ALLOWED_CLASS'
            return report
        attempted = True
        process.cpu_affinity(selected)
        if sorted(process.cpu_affinity()) != selected:
            raise OSError('NATIVE_CPU_AFFINITY_NOT_CONFIRMED')
        report.update(mode='PERFORMANCE_CORES', applied=True,
                      reason='HIGHEST_PERFORMANCE_CLASS_WITHIN_ALLOWED_AFFINITY')
    except (OSError, psutil.Error, AttributeError) as exc:
        if attempted and process is not None and original is not None:
            try:
                process.cpu_affinity(original)
            except (OSError, psutil.Error) as restore_error:
                raise RuntimeError('NATIVE_CPU_AFFINITY_RESTORE_FAILED') from restore_error
        report.update(mode='SYSTEM_DEFAULT', applied=False,
                      reason='SCHEDULING_PREFERENCE_UNAVAILABLE',
                      error=f'{type(exc).__name__}: {exc}')
    return report
