"""Paired-run causal and parameter-effect validation.

A sealed single run proves execution, wiring and bounded numerical behaviour.
It does not prove counterfactual causality.  This module compares a baseline
Run Bundle with a declared one-factor perturbation and evaluates source change,
sustained sink response, direction, pre-intervention equivalence and lag.
"""
from __future__ import annotations

import csv
import json
from bisect import bisect_left
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import fmean, median
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class CausalExpectation:
    coupling_id: str
    source_field: str
    response_field: str
    intervention_time_s: float = 0.0
    expected_direction: str = "change"  # increase, decrease, change
    min_source_delta: float = 0.0
    min_response_delta: float = 0.0
    max_lag_s: float | None = None
    alignment_tolerance_s: float = 1.0e-6
    min_pre_samples: int = 0
    pre_source_tolerance: float = 0.0
    pre_response_tolerance: float = 0.0
    min_post_samples: int = 1
    min_consecutive_response_samples: int = 1
    min_response_fraction: float = 0.0


@dataclass(frozen=True)
class CausalCheck:
    coupling_id: str
    status: str
    aligned_sample_count: int
    pre_sample_count: int
    post_sample_count: int
    source_delta: float
    source_delta_median: float
    source_delta_max_abs: float
    response_delta: float
    response_delta_median: float
    response_delta_max_abs: float
    observed_direction: str
    source_change_time_s: float | None
    first_response_time_s: float | None
    first_response_lag_s: float | None
    response_fraction: float
    max_consecutive_response_samples: int
    pre_equivalent: bool
    source_changed: bool
    response_changed: bool
    sustained_response_ok: bool
    direction_ok: bool
    lag_ok: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ParameterEffectExpectation:
    parameter_path: str
    baseline_value: float
    perturbed_value: float
    response_field: str
    expected_direction: str = "change"
    min_response_delta: float = 0.0
    intervention_time_s: float = 0.0
    alignment_tolerance_s: float = 1.0e-6
    min_post_samples: int = 1
    min_consecutive_response_samples: int = 1
    min_response_fraction: float = 0.0


@dataclass(frozen=True)
class ParameterEffectCheck:
    parameter_path: str
    response_field: str
    status: str
    parameter_delta: float
    response_delta: float
    response_delta_median: float
    response_delta_max_abs: float
    observed_direction: str
    response_fraction: float
    max_consecutive_response_samples: int
    response_changed: bool
    sustained_response_ok: bool
    direction_ok: bool
    reason_codes: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _number(row: Mapping[str, Any], field: str) -> float | None:
    value = row.get(field)
    if isinstance(value, bool) or value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _time(row: Mapping[str, Any]) -> float | None:
    for key in ("time_s", "simulation.time_s", "sim_time_s"):
        value = _number(row, key)
        if value is not None:
            return value
    return None


def _timed(rows: Iterable[Mapping[str, Any]]) -> list[tuple[float, Mapping[str, Any]]]:
    result = [(float(time_s), row) for row in rows if (time_s := _time(row)) is not None]
    result.sort(key=lambda item: item[0])
    return result


def _aligned(
    baseline_rows: Sequence[Mapping[str, Any]],
    perturbed_rows: Sequence[Mapping[str, Any]],
    tolerance_s: float,
) -> list[tuple[float, Mapping[str, Any], Mapping[str, Any]]]:
    baseline = _timed(baseline_rows)
    perturbed = _timed(perturbed_rows)
    p_times = [item[0] for item in perturbed]
    aligned: list[tuple[float, Mapping[str, Any], Mapping[str, Any]]] = []
    for time_s, b_row in baseline:
        index = bisect_left(p_times, time_s)
        candidates = [i for i in (index - 1, index) if 0 <= i < len(perturbed)]
        if not candidates:
            continue
        best = min(candidates, key=lambda i: abs(p_times[i] - time_s))
        if abs(p_times[best] - time_s) <= max(0.0, float(tolerance_s)) + 1.0e-12:
            aligned.append((time_s, b_row, perturbed[best][1]))
    return aligned


def _direction(value: float, threshold: float = 0.0) -> str:
    if value > threshold:
        return "increase"
    if value < -threshold:
        return "decrease"
    return "none"


def _max_consecutive(flags: Sequence[bool]) -> int:
    best = current = 0
    for flag in flags:
        current = current + 1 if flag else 0
        best = max(best, current)
    return best


def _overall_status(statuses: Sequence[str]) -> str:
    if not statuses:
        return "INCONCLUSIVE"
    if any(item == "FAIL" for item in statuses):
        return "FAIL"
    if any(item == "INCONCLUSIVE" for item in statuses):
        return "INCONCLUSIVE"
    return "PASS"


def evaluate_coupling_causality(
    baseline_rows: Sequence[Mapping[str, Any]],
    perturbed_rows: Sequence[Mapping[str, Any]],
    expectations: Sequence[CausalExpectation],
) -> dict[str, Any]:
    checks: list[CausalCheck] = []
    for expectation in expectations:
        aligned = _aligned(baseline_rows, perturbed_rows, expectation.alignment_tolerance_s)
        pre: list[tuple[float, float, float]] = []
        post: list[tuple[float, float, float]] = []
        for time_s, b_row, p_row in aligned:
            values = (
                _number(b_row, expectation.source_field),
                _number(p_row, expectation.source_field),
                _number(b_row, expectation.response_field),
                _number(p_row, expectation.response_field),
            )
            if None in values:
                continue
            sample = (time_s, float(values[1]) - float(values[0]), float(values[3]) - float(values[2]))
            (pre if time_s + 1.0e-9 < float(expectation.intervention_time_s) else post).append(sample)

        reasons: list[str] = []
        if len(post) < max(1, int(expectation.min_post_samples)):
            reasons.append("INSUFFICIENT_POST_INTERVENTION_SAMPLES")
        if len(pre) < max(0, int(expectation.min_pre_samples)):
            reasons.append("INSUFFICIENT_PRE_INTERVENTION_SAMPLES")
        pre_equivalent = (
            len(pre) >= max(0, int(expectation.min_pre_samples))
            and all(abs(item[1]) <= float(expectation.pre_source_tolerance) + 1.0e-12 for item in pre)
            and all(abs(item[2]) <= float(expectation.pre_response_tolerance) + 1.0e-12 for item in pre)
        )
        if int(expectation.min_pre_samples) > 0 and not pre_equivalent:
            reasons.append("PRE_INTERVENTION_EQUIVALENCE_FAILED")

        source_deltas = [item[1] for item in post]
        response_deltas = [item[2] for item in post]
        source_mean = fmean(source_deltas) if source_deltas else 0.0
        source_median = median(source_deltas) if source_deltas else 0.0
        response_mean = fmean(response_deltas) if response_deltas else 0.0
        response_median = median(response_deltas) if response_deltas else 0.0
        source_max = max((abs(value) for value in source_deltas), default=0.0)
        response_max = max((abs(value) for value in response_deltas), default=0.0)
        source_flags = [abs(value) >= float(expectation.min_source_delta) for value in source_deltas]
        response_flags = [abs(value) >= float(expectation.min_response_delta) for value in response_deltas]
        source_changed = any(source_flags)
        response_changed = any(response_flags)
        response_fraction = (sum(response_flags) / len(response_flags)) if response_flags else 0.0
        consecutive = _max_consecutive(response_flags)
        sustained_ok = (
            response_changed
            and consecutive >= max(1, int(expectation.min_consecutive_response_samples))
            and response_fraction + 1.0e-12 >= float(expectation.min_response_fraction)
        )
        observed_direction = _direction(response_median)
        requested_direction = expectation.expected_direction.lower()
        direction_ok = response_changed and (requested_direction == "change" or requested_direction == observed_direction)
        source_time = next((post[i][0] for i, flag in enumerate(source_flags) if flag), None)
        response_time = None
        if source_time is not None:
            response_time = next((post[i][0] for i, flag in enumerate(response_flags) if flag and post[i][0] + 1.0e-9 >= source_time), None)
        lag = None if source_time is None or response_time is None else max(0.0, response_time - source_time)
        lag_ok = expectation.max_lag_s is None or (lag is not None and lag <= float(expectation.max_lag_s) + 1.0e-9)

        if not source_changed:
            reasons.append("SOURCE_PERTURBATION_NOT_OBSERVED")
        if not response_changed:
            reasons.append("SINK_RESPONSE_NOT_OBSERVED")
        if response_changed and not sustained_ok:
            reasons.append("SINK_RESPONSE_NOT_SUSTAINED")
        if response_changed and not direction_ok:
            reasons.append("SINK_RESPONSE_DIRECTION_MISMATCH")
        if not lag_ok:
            reasons.append("SINK_RESPONSE_LAG_EXCEEDED")

        prerequisites = len(post) >= max(1, int(expectation.min_post_samples)) and (
            int(expectation.min_pre_samples) == 0 or pre_equivalent
        )
        status = "PASS" if prerequisites and source_changed and response_changed and sustained_ok and direction_ok and lag_ok else (
            "INCONCLUSIVE" if not prerequisites else "FAIL"
        )
        checks.append(CausalCheck(
            coupling_id=expectation.coupling_id,
            status=status,
            aligned_sample_count=len(aligned),
            pre_sample_count=len(pre),
            post_sample_count=len(post),
            source_delta=source_mean,
            source_delta_median=float(source_median),
            source_delta_max_abs=source_max,
            response_delta=response_mean,
            response_delta_median=float(response_median),
            response_delta_max_abs=response_max,
            observed_direction=observed_direction,
            source_change_time_s=source_time,
            first_response_time_s=response_time,
            first_response_lag_s=lag,
            response_fraction=response_fraction,
            max_consecutive_response_samples=consecutive,
            pre_equivalent=pre_equivalent,
            source_changed=source_changed,
            response_changed=response_changed,
            sustained_response_ok=sustained_ok,
            direction_ok=direction_ok,
            lag_ok=lag_ok,
            reason_codes=tuple(dict.fromkeys(reasons)),
        ))

    statuses = [item.status for item in checks]
    return {
        "schema_version": "v0572a.coupling-causality.v2",
        "status": _overall_status(statuses),
        "check_count": len(checks),
        "pass_count": sum(item.status == "PASS" for item in checks),
        "fail_count": sum(item.status == "FAIL" for item in checks),
        "inconclusive_count": sum(item.status == "INCONCLUSIVE" for item in checks),
        "checks": [item.to_dict() for item in checks],
        "claim_scope": "paired_run_one_factor_counterfactual_causal_evidence",
        "alignment_policy": "nearest_sample_without_interpolation",
    }


def evaluate_parameter_effects(
    baseline_rows: Sequence[Mapping[str, Any]],
    perturbed_rows: Sequence[Mapping[str, Any]],
    expectations: Sequence[ParameterEffectExpectation],
) -> dict[str, Any]:
    checks: list[ParameterEffectCheck] = []
    for expectation in expectations:
        aligned = _aligned(baseline_rows, perturbed_rows, expectation.alignment_tolerance_s)
        deltas: list[float] = []
        for time_s, b_row, p_row in aligned:
            if time_s + 1.0e-9 < float(expectation.intervention_time_s):
                continue
            b_value = _number(b_row, expectation.response_field)
            p_value = _number(p_row, expectation.response_field)
            if b_value is not None and p_value is not None:
                deltas.append(float(p_value) - float(b_value))
        reasons: list[str] = []
        parameter_delta = float(expectation.perturbed_value) - float(expectation.baseline_value)
        if parameter_delta == 0.0:
            reasons.append("PARAMETER_NOT_PERTURBED")
        response_mean = fmean(deltas) if deltas else 0.0
        response_median = median(deltas) if deltas else 0.0
        response_max = max((abs(value) for value in deltas), default=0.0)
        flags = [abs(value) >= float(expectation.min_response_delta) for value in deltas]
        response_changed = any(flags)
        response_fraction = (sum(flags) / len(flags)) if flags else 0.0
        consecutive = _max_consecutive(flags)
        sustained_ok = (
            response_changed
            and consecutive >= max(1, int(expectation.min_consecutive_response_samples))
            and response_fraction + 1.0e-12 >= float(expectation.min_response_fraction)
        )
        observed_direction = _direction(float(response_median))
        requested = expectation.expected_direction.lower()
        direction_ok = response_changed and (requested == "change" or requested == observed_direction)
        enough = len(deltas) >= max(1, int(expectation.min_post_samples))
        if not enough:
            reasons.append("INSUFFICIENT_POST_INTERVENTION_SAMPLES")
        if not response_changed:
            reasons.append("PARAMETER_RESPONSE_NOT_OBSERVED")
        if response_changed and not sustained_ok:
            reasons.append("PARAMETER_RESPONSE_NOT_SUSTAINED")
        if response_changed and not direction_ok:
            reasons.append("PARAMETER_RESPONSE_DIRECTION_MISMATCH")
        status = "PASS" if enough and parameter_delta != 0.0 and response_changed and sustained_ok and direction_ok else (
            "INCONCLUSIVE" if not enough else "FAIL"
        )
        checks.append(ParameterEffectCheck(
            parameter_path=expectation.parameter_path,
            response_field=expectation.response_field,
            status=status,
            parameter_delta=parameter_delta,
            response_delta=response_mean,
            response_delta_median=float(response_median),
            response_delta_max_abs=response_max,
            observed_direction=observed_direction,
            response_fraction=response_fraction,
            max_consecutive_response_samples=consecutive,
            response_changed=response_changed,
            sustained_response_ok=sustained_ok,
            direction_ok=direction_ok,
            reason_codes=tuple(dict.fromkeys(reasons)),
        ))
    statuses = [item.status for item in checks]
    return {
        "schema_version": "v0572a.parameter-effect.v2",
        "status": _overall_status(statuses),
        "check_count": len(checks),
        "pass_count": sum(item.status == "PASS" for item in checks),
        "fail_count": sum(item.status == "FAIL" for item in checks),
        "inconclusive_count": sum(item.status == "INCONCLUSIVE" for item in checks),
        "checks": [item.to_dict() for item in checks],
        "claim_scope": "paired_run_one_factor_parameter_effect_evidence",
    }


def load_telemetry(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path)
    if source.is_dir():
        csv_path = source / "results" / "telemetry.csv"
        jsonl_path = source / "results" / "telemetry.jsonl"
        source = csv_path if csv_path.is_file() else jsonl_path
    if source.suffix.lower() == ".csv":
        with source.open("r", encoding="utf-8", newline="") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    rows: list[dict[str, Any]] = []
    with source.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                payload = json.loads(line)
                if isinstance(payload, dict):
                    rows.append(payload)
    return rows


__all__ = [
    "CausalExpectation", "CausalCheck",
    "ParameterEffectExpectation", "ParameterEffectCheck",
    "evaluate_coupling_causality", "evaluate_parameter_effects", "load_telemetry",
]
