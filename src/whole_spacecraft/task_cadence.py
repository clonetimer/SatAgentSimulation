"""Runtime task-cadence probes for the composite whole-spacecraft graph.

The configured task period is only a declaration.  These Basilisk ``SysModel``
probes are scheduled on the actual Process/Task graph and record every update
instant so the run report can prove that each physical and recorder task ran at
the requested cadence.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from functools import reduce
from math import floor, gcd
from typing import Any, Mapping

try:
    from Basilisk.architecture import sysModel
    from Basilisk.utilities import macros
except ImportError:  # pragma: no cover - optional runtime dependency
    sysModel = None  # type: ignore[assignment]
    macros = None  # type: ignore[assignment]


if sysModel is not None:
    class TaskCadenceProbe(sysModel.SysModel):
        """Record real Basilisk scheduler activation times for one task."""

        def __init__(self, task_name: str, configured_period_s: float) -> None:
            super().__init__()
            self.ModelTag = f"taskCadenceProbe_{task_name}"
            self.task_name = str(task_name)
            self.configured_period_s = float(configured_period_s)
            self.update_times_ns: list[int] = []

        def Reset(self, CurrentSimNanos: int) -> None:  # noqa: N802
            self.update_times_ns.clear()

        def UpdateState(self, CurrentSimNanos: int) -> None:  # noqa: N802
            value = int(CurrentSimNanos)
            if not self.update_times_ns or self.update_times_ns[-1] != value:
                self.update_times_ns.append(value)
else:  # pragma: no cover - importable without Basilisk
    class TaskCadenceProbe:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("Basilisk is required to instantiate TaskCadenceProbe")


@dataclass(frozen=True)
class TaskCadenceEvidence:
    task_name: str
    configured_period_s: float
    observed_count: int
    expected_count: int
    first_time_s: float | None
    last_time_s: float | None
    min_interval_s: float | None
    max_interval_s: float | None
    mean_interval_s: float | None
    max_abs_jitter_s: float | None
    count_ok: bool
    period_ok: bool
    endpoint_ok: bool
    status: str
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _seconds(value_ns: int) -> float:
    if macros is not None:
        return float(value_ns) * float(macros.NANO2SEC)
    return float(value_ns) * 1.0e-9


def evaluate_task_cadence(
    probes: Mapping[str, Any],
    *,
    duration_s: float,
    absolute_tolerance_s: float = 1.0e-9,
) -> dict[str, Any]:
    """Build runtime evidence from actual task activations.

    Basilisk tasks include both the initial activation at ``t=0`` and the final
    activation when the stop time is an integer multiple of the task period.
    The expected count follows that scheduler contract.
    """

    checks: list[TaskCadenceEvidence] = []
    for logical_name, probe in probes.items():
        period = float(getattr(probe, "configured_period_s", 0.0))
        times_ns = [int(value) for value in getattr(probe, "update_times_ns", ())]
        times_s = [_seconds(value) for value in times_ns]
        intervals = [times_s[index] - times_s[index - 1] for index in range(1, len(times_s))]
        expected_count = floor((float(duration_s) + absolute_tolerance_s) / period) + 1 if period > 0.0 else 0
        count_ok = len(times_s) == expected_count
        period_tolerance = max(absolute_tolerance_s, abs(period) * 1.0e-9)
        period_ok = bool(times_s) and all(abs(value - period) <= period_tolerance for value in intervals)
        endpoint_expected = floor((float(duration_s) + absolute_tolerance_s) / period) * period if period > 0.0 else 0.0
        endpoint_ok = bool(times_s) and abs(times_s[0]) <= absolute_tolerance_s and abs(times_s[-1] - endpoint_expected) <= period_tolerance
        reasons: list[str] = []
        if not times_s:
            reasons.append("TASK_NEVER_EXECUTED")
        if not count_ok:
            reasons.append("TASK_EXECUTION_COUNT_MISMATCH")
        if not period_ok:
            reasons.append("TASK_PERIOD_MISMATCH")
        if not endpoint_ok:
            reasons.append("TASK_ENDPOINT_MISMATCH")
        checks.append(TaskCadenceEvidence(
            task_name=str(logical_name),
            configured_period_s=period,
            observed_count=len(times_s),
            expected_count=expected_count,
            first_time_s=times_s[0] if times_s else None,
            last_time_s=times_s[-1] if times_s else None,
            min_interval_s=min(intervals) if intervals else None,
            max_interval_s=max(intervals) if intervals else None,
            mean_interval_s=(sum(intervals) / len(intervals)) if intervals else None,
            max_abs_jitter_s=max((abs(value - period) for value in intervals), default=None),
            count_ok=count_ok,
            period_ok=period_ok,
            endpoint_ok=endpoint_ok,
            status="PASS" if count_ok and period_ok and endpoint_ok else "FAIL",
            reason_codes=tuple(reasons),
        ))
    status = "PASS" if checks and all(item.status == "PASS" for item in checks) else "FAIL"
    return {
        "schema_version": "v0572a.runtime-task-cadence.v1",
        "status": status,
        "duration_s": float(duration_s),
        "task_count": len(checks),
        "pass_count": sum(item.status == "PASS" for item in checks),
        "fail_count": sum(item.status != "PASS" for item in checks),
        "checks": [item.to_dict() for item in checks],
        "claim_scope": "actual_basilisk_scheduler_activation_evidence",
    }


def validate_task_periods(
    *,
    dynamics_step_s: float,
    fsw_step_s: float,
    orbit_environment_step_s: float,
    thermal_step_s: float,
    recorder_step_s: float,
) -> dict[str, Any]:
    """Validate scheduler-safe task periods without imposing false harmonics.

    Basilisk schedules tasks on integer nanoseconds, so independently selected
    periods such as 0.2 s dynamics and 0.5 s recording are deterministic even
    though one is not an integer multiple of the other.  The contract therefore
    requires positive, nanosecond-representable periods and prevents physical or
    output tasks from claiming a cadence faster than the dynamics base tick.
    Runtime probes remain the authority for the actually observed cadence.
    """
    values = {
        "dynamics": float(dynamics_step_s),
        "fsw": float(fsw_step_s),
        "orbit_environment": float(orbit_environment_step_s),
        "thermal": float(thermal_step_s),
        "recorder": float(recorder_step_s),
    }
    issues: list[dict[str, Any]] = []
    periods_ns: dict[str, int] = {}
    for name, value in values.items():
        if value <= 0.0:
            issues.append({"task": name, "reason_code": "TASK_PERIOD_NOT_POSITIVE", "value_s": value})
            continue
        raw_ns = value * 1.0e9
        rounded_ns = int(round(raw_ns))
        if rounded_ns <= 0 or abs(raw_ns - rounded_ns) > 1.0e-3:
            issues.append({
                "task": name,
                "reason_code": "TASK_PERIOD_NOT_NANOSECOND_REPRESENTABLE",
                "value_s": value,
                "raw_nanoseconds": raw_ns,
            })
            continue
        periods_ns[name] = rounded_ns

    base_ns = periods_ns.get("dynamics", 0)
    if base_ns > 0:
        for name, value_ns in periods_ns.items():
            if name != "dynamics" and value_ns < base_ns:
                issues.append({
                    "task": name,
                    "reason_code": "TASK_PERIOD_FASTER_THAN_DYNAMICS",
                    "value_s": values[name],
                    "dynamics_step_s": values["dynamics"],
                })
    scheduler_quantum_ns = reduce(gcd, periods_ns.values()) if periods_ns else 0
    return {
        "schema_version": "v0572a.task-period-grid.v2",
        "status": "PASS" if not issues else "FAIL",
        "dynamics_base_tick_s": values["dynamics"],
        "scheduler_quantum_ns": scheduler_quantum_ns,
        "scheduler_quantum_s": scheduler_quantum_ns * 1.0e-9,
        "periods_s": values,
        "periods_ns": periods_ns,
        "issues": issues,
        "semantics": "independent_integer_nanosecond_tasks_with_runtime_cadence_evidence",
    }


__all__ = ["TaskCadenceProbe", "TaskCadenceEvidence", "evaluate_task_cadence", "validate_task_periods"]
