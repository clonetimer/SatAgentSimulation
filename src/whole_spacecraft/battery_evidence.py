"""Checked observations of the official composite battery output.

This module reads native recorder payloads and source timestamps. It does not
initialize, repair, integrate or otherwise change the native battery state.
The sole startup exclusion is its exact all-zero output at native time zero.
"""
from __future__ import annotations

from bisect import bisect_right
from dataclasses import dataclass
from math import fsum, isfinite
from numbers import Integral, Real
from typing import Any, Iterable


class NativeBatteryEvidenceError(RuntimeError):
    """A missing or invalid observation prevents a numerical battery claim."""

    def __init__(self, reason_code: str, detail: str) -> None:
        self.reason_code = reason_code
        super().__init__(f"COMPOSITE_BATTERY_NATIVE_EVIDENCE_INVALID:{reason_code}: {detail}")


@dataclass(frozen=True)
class BatterySample:
    record_index: int
    recorder_time_ns: int
    source_time_ns: int
    energy_j: float
    capacity_j: float
    net_power_w: float

    @property
    def is_startup_not_ready(self) -> bool:
        return (
            self.record_index == 0
            and self.recorder_time_ns == self.source_time_ns == 0
            and self.energy_j == self.capacity_j == self.net_power_w == 0.0
        )

    @property
    def soc(self) -> float:
        return self.energy_j / self.capacity_j


@dataclass(frozen=True)
class NativeBatteryEvidence:
    """One complete, contiguous observed native battery interval."""

    samples: tuple[BatterySample, ...]
    record_times_ns: tuple[int, ...]
    startup_excluded_sample_count: int
    storage_delta_j: float
    integrated_net_power_j: float

    @property
    def start_s(self) -> float:
        return self.samples[0].source_time_ns * 1.0e-9

    @property
    def end_s(self) -> float:
        return self.samples[-1].source_time_ns * 1.0e-9

    @property
    def min_soc(self) -> float:
        return min(sample.soc for sample in self.samples)

    @property
    def max_soc(self) -> float:
        return max(sample.soc for sample in self.samples)

    @property
    def max_interval_s(self) -> float:
        return max(
            (right.source_time_ns - left.source_time_ns) * 1.0e-9
            for left, right in zip(self.samples, self.samples[1:])
        )


def _series(recorder: Any, name: str, *, role: str, method: bool = False) -> tuple[Any, ...]:
    if recorder is None:
        raise NativeBatteryEvidenceError("MISSING_RECORDER", role)
    try:
        field = getattr(recorder, name)
    except AttributeError as exc:
        raise NativeBatteryEvidenceError("MISSING_FIELD", f"{role}.{name}") from exc
    except Exception as exc:
        raise NativeBatteryEvidenceError("UNREADABLE_FIELD", f"{role}.{name}: {type(exc).__name__}") from exc
    try:
        return tuple(field() if method else field)
    except Exception as exc:
        raise NativeBatteryEvidenceError("MALFORMED_FIELD", f"{role}.{name}: {type(exc).__name__}") from exc


def _timestamps(values: Iterable[Any], *, field: str) -> tuple[int, ...]:
    checked = []
    for index, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, Integral) or value < 0:
            raise NativeBatteryEvidenceError("MALFORMED_FIELD", f"{field}[{index}] must be a nonnegative integer nanosecond timestamp")
        checked.append(int(value))
    return tuple(checked)


def _checked_samples(recorder: Any, *, role: str) -> tuple[BatterySample, ...]:
    times = _timestamps(_series(recorder, "times", role=role, method=True), field=f"{role}.times")
    written = _timestamps(_series(recorder, "timesWritten", role=role, method=True), field=f"{role}.timesWritten")
    fields = {
        name: _series(recorder, name, role=role)
        for name in ("storageLevel", "storageCapacity", "currentNetPower")
    }
    if len(written) != len(times) or any(len(values) != len(times) for values in fields.values()):
        raise NativeBatteryEvidenceError("FIELD_LENGTH_MISMATCH", role)
    if not times:
        raise NativeBatteryEvidenceError("MISSING_SAMPLES", role)
    if times[0] != 0 or any(right <= left for left, right in zip(times, times[1:])):
        raise NativeBatteryEvidenceError("INVALID_RECORD_TIMES", role)
    samples = []
    for index, (record_time, source_time) in enumerate(zip(times, written)):
        values = []
        for name, field_values in fields.items():
            value = field_values[index]
            if isinstance(value, bool) or not isinstance(value, Real):
                raise NativeBatteryEvidenceError("MALFORMED_FIELD", f"{role}.{name}[{index}]")
            try:
                value = float(value)
            except (OverflowError, ValueError) as exc:
                raise NativeBatteryEvidenceError("NONFINITE_PAYLOAD", f"{role}.{name}[{index}]") from exc
            if not isfinite(value):
                raise NativeBatteryEvidenceError("NONFINITE_PAYLOAD", f"{role}.{name}[{index}]")
            values.append(value)
        sample = BatterySample(index, record_time, source_time, *values)
        if not sample.is_startup_not_ready:
            if sample.capacity_j <= 0.0:
                raise NativeBatteryEvidenceError("INVALID_CAPACITY", f"{role}[{index}] at {record_time}ns")
            if not 0.0 <= sample.energy_j <= sample.capacity_j:
                raise NativeBatteryEvidenceError("OUT_OF_BOUNDS_ENERGY", f"{role}[{index}] at {record_time}ns")
        samples.append(sample)
    return tuple(samples)


def check_native_battery_evidence(
    recorder: Any, *, expected_update_times_ns: Iterable[int],
) -> NativeBatteryEvidence:
    """Check every actual dynamics update before computing a measured balance.

    ``PowerStorageBase`` uses the current output power for the preceding native
    interval. The integral therefore uses right endpoints on the same first-to-
    last valid source-time window as the measured energy delta. A genuine empty
    battery has positive capacity and remains a valid zero-SOC observation.
    """
    samples = _checked_samples(recorder, role="conservation_battery")
    expected = _timestamps(expected_update_times_ns, field="dynamics_update_times_ns")
    times = tuple(sample.recorder_time_ns for sample in samples)
    if times != expected:
        raise NativeBatteryEvidenceError("NATIVE_SAMPLE_GRID_MISMATCH", "battery recorder must cover every actual dynamics activation")
    for sample in samples:
        if sample.source_time_ns != sample.recorder_time_ns:
            raise NativeBatteryEvidenceError("STALE_SOURCE_TIME", f"conservation_battery[{sample.record_index}] recorded at {sample.recorder_time_ns}ns, written at {sample.source_time_ns}ns")
    excluded = int(samples[0].is_startup_not_ready)
    valid = samples[excluded:]
    if len(valid) < 2:
        raise NativeBatteryEvidenceError("INSUFFICIENT_VALID_SAMPLES", "at least two valid native states are required for an observed conservation interval")
    delta_j = valid[-1].energy_j - valid[0].energy_j
    try:
        integrated_j = fsum(
            right.net_power_w * ((right.source_time_ns - left.source_time_ns) * 1.0e-9)
            for left, right in zip(valid, valid[1:])
        )
    except (OverflowError, ValueError) as exc:
        raise NativeBatteryEvidenceError("NONFINITE_BALANCE", "native power integral") from exc
    if not isfinite(delta_j) or not isfinite(integrated_j):
        raise NativeBatteryEvidenceError("NONFINITE_BALANCE", "native energy delta or power integral")
    return NativeBatteryEvidence(valid, times, excluded, delta_j, integrated_j)


def check_public_battery_samples(
    recorder: Any, *, native_evidence: NativeBatteryEvidence,
) -> tuple[BatterySample, ...]:
    """Retain original public indices and check each held native source payload.

    Public and native periods need not be harmonics. A public record is fresh
    when its source timestamp is the latest actual dynamics activation at or
    before that record, and its payload matches that observed native message.
    """
    samples = _checked_samples(recorder, role="battery")
    native_by_time = {sample.source_time_ns: sample for sample in native_evidence.samples}
    valid = []
    for sample in samples:
        expected_index = bisect_right(native_evidence.record_times_ns, sample.recorder_time_ns) - 1
        if expected_index < 0 or sample.source_time_ns != native_evidence.record_times_ns[expected_index]:
            raise NativeBatteryEvidenceError("STALE_SOURCE_TIME", f"battery[{sample.record_index}] recorded at {sample.recorder_time_ns}ns, written at {sample.source_time_ns}ns")
        if sample.is_startup_not_ready and native_evidence.startup_excluded_sample_count:
            continue
        native = native_by_time.get(sample.source_time_ns)
        if native is None or (sample.energy_j, sample.capacity_j, sample.net_power_w) != (native.energy_j, native.capacity_j, native.net_power_w):
            raise NativeBatteryEvidenceError("PUBLIC_SOURCE_MISMATCH", f"battery[{sample.record_index}] must match its native source at {sample.source_time_ns}ns")
        valid.append(sample)
    if not valid:
        raise NativeBatteryEvidenceError("INSUFFICIENT_PUBLIC_SAMPLES", "no valid native battery state was captured on the requested public grid")
    return tuple(valid)
