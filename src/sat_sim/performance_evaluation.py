"""Optional, read-only post-processing of sealed recorded telemetry snapshots.

No runner, queue, configuration writer, or session is imported here. A report
is not a simulation verdict. Formula IDs are an allowlist, never executable text.
The caller is responsible for obtaining an authentic archived/frozen snapshot.
"""
from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256
import json
import math
from typing import Any

REPORT_VERSION = "evaluation-report.v1"
FORMULAS = frozenset({"sample_mean.v1", "sample_rms.v1", "max_abs.v1", "final.v1"})
_BINDING_KEYS = ("owner_object_id", "source_capability_id", "source_field", "unit")


def _number(value: Any) -> bool:
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except (OverflowError, TypeError):
        return False


def _json_copy(value: Mapping[str, Any], label: str) -> tuple[dict[str, Any], str]:
    if not isinstance(value, Mapping):
        raise ValueError(f"EVALUATION_INVALID_{label}")
    try:
        text = json.dumps(dict(value), ensure_ascii=False, sort_keys=True,
                          separators=(",", ":"), allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"EVALUATION_INVALID_{label}") from exc
    return json.loads(text), sha256(text.encode("utf-8")).hexdigest()


def _required_text(row: Mapping[str, Any], key: str) -> None:
    if not isinstance(row.get(key), str) or not row[key].strip():
        raise ValueError(f"EVALUATION_REQUIRED_TEXT:{key}")


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get("schema_version") != "evaluation-plan.v1":
        raise ValueError("EVALUATION_PLAN_VERSION")
    for key in ("plan_id", "version"):
        _required_text(plan, key)
    window = plan.get("window_s")
    if (not isinstance(window, list) or len(window) != 2
            or not all(_number(v) for v in window) or window[0] > window[1]):
        raise ValueError("EVALUATION_INVALID_WINDOW")
    gap = plan.get("max_sample_gap_s")
    if gap is not None and (not _number(gap) or gap <= 0):
        raise ValueError("EVALUATION_INVALID_GAP_LIMIT")
    metrics = plan.get("metrics")
    if not isinstance(metrics, list) or not metrics:
        raise ValueError("EVALUATION_EMPTY_METRICS")
    seen: set[str] = set()
    for metric in metrics:
        if not isinstance(metric, Mapping):
            raise ValueError("EVALUATION_INVALID_METRIC")
        for key in ("metric_id", "display_name_zh", "series_id", *_BINDING_KEYS):
            _required_text(metric, key)
        if metric["metric_id"] in seen:
            raise ValueError("EVALUATION_DUPLICATE_METRIC")
        seen.add(metric["metric_id"])
        if not isinstance(metric.get("formula"), str) or metric["formula"] not in FORMULAS:
            raise ValueError("EVALUATION_UNKNOWN_FORMULA")
        minimum = metric.get("minimum_samples", 1)
        if type(minimum) is not int or minimum < 1:
            raise ValueError("EVALUATION_INVALID_MINIMUM_SAMPLES")
        limits = metric.get("limits")
        if limits is not None:
            if (not isinstance(limits, Mapping) or not limits
                    or not set(limits) <= {"lower", "upper"}
                    or not all(_number(v) for v in limits.values())):
                raise ValueError("EVALUATION_INVALID_LIMITS")
            if "lower" in limits and "upper" in limits and limits["lower"] > limits["upper"]:
                raise ValueError("EVALUATION_REVERSED_LIMITS")


def _reduce(values: list[float], formula: str) -> float:
    if formula == "sample_mean.v1":
        return math.fsum(v / len(values) for v in values)
    if formula == "sample_rms.v1":
        scale = max(abs(v) for v in values)
        return scale * math.sqrt(math.fsum((v / scale) ** 2 for v in values) / len(values)) if scale else 0.0
    if formula == "max_abs.v1":
        return max(abs(v) for v in values)
    return values[-1]  # final.v1, after allowlist validation


def _evaluate_metric(metric: dict[str, Any], series: Mapping[str, Any],
                     plan: Mapping[str, Any]) -> dict[str, Any]:
    result = {**metric, "status": "NOT_EVALUATED", "verdict": "NOT_EVALUATED",
              "value": None, "sample_count": 0, "recorded_window_s": None}
    source = series.get(metric["series_id"])
    reason = None
    if not isinstance(source, Mapping):
        reason = "SOURCE_NOT_RECORDED"
    elif any(source.get(key) != metric[key] for key in _BINDING_KEYS):
        reason = "SOURCE_BINDING_MISMATCH"
    else:
        times, values = source.get("times_s"), source.get("values")
        if (not isinstance(times, list) or not isinstance(values, list)
                or len(times) != len(values)
                or not all(_number(v) for v in values)):
            reason = "INVALID_SERIES"
        elif not times:
            reason = "NO_SAMPLES"
        elif not all(_number(t) for t in times) or any(b <= a for a, b in zip(times, times[1:])):
            reason = "INVALID_TIME_AXIS"
        else:
            start, stop = plan["window_s"]
            selected = [(t, float(v)) for t, v in zip(times, values) if start <= t <= stop]
            gap_limit = plan.get("max_sample_gap_s")
            if times[0] > start or times[-1] < stop:
                reason = "INCOMPLETE_WINDOW"
            elif not selected:
                reason = "NO_SAMPLES"
            elif len(selected) < metric.get("minimum_samples", 1):
                reason = "MINIMUM_SAMPLES"
            elif gap_limit is not None and any(
                    b - a > gap_limit for a, b in zip(times, times[1:]) if a < stop and b > start):
                reason = "SAMPLING_GAP"
            else:
                value = _reduce([v for _, v in selected], metric["formula"])
                limits = metric.get("limits")
                verdict = "NOT_REQUESTED"
                if limits is not None:
                    inside = (("lower" not in limits or value >= limits["lower"])
                              and ("upper" not in limits or value <= limits["upper"]))
                    verdict = "WITHIN_LIMITS" if inside else "OUTSIDE_LIMITS"
                result.update(status="COMPUTED", verdict=verdict, value=value,
                              sample_count=len(selected), recorded_window_s=[selected[0][0], selected[-1][0]])
    result["reason_code"] = reason
    return result


def evaluate_snapshot(snapshot: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    """Compute one report without editing the input or requesting a new simulation.

    Missing/mismatched data is NOT_EVALUATED, not zero and not a pass. Invalid
    plans or non-JSON/nonfinite snapshots raise ValueError. Sample statistics
    are not time-weighted; no interpolation or implicit unit conversion occurs.
    """
    source, source_digest = _json_copy(snapshot, "SNAPSHOT")
    specification, plan_digest = _json_copy(plan, "PLAN")
    if source.get("schema_version") != "recorded-snapshot.v1" or source.get("sealed") is not True:
        raise ValueError("EVALUATION_REQUIRES_SEALED_SNAPSHOT")
    for key in ("run_id", "snapshot_id", "run_status"):
        _required_text(source, key)
    if not isinstance(source.get("series"), Mapping):
        raise ValueError("EVALUATION_INVALID_SERIES_CATALOG")
    _validate_plan(specification)
    metrics = [_evaluate_metric(metric, source["series"], specification) for metric in specification["metrics"]]
    computed = sum(metric["status"] == "COMPUTED" for metric in metrics)
    return {
        "schema_version": REPORT_VERSION,
        "report_id": "eval-" + sha256((source_digest + plan_digest).encode()).hexdigest()[:24],
        "source_run_id": source["run_id"], "source_snapshot_id": source["snapshot_id"],
        "source_run_status": source["run_status"], "source_digest": source_digest,
        "plan_id": specification["plan_id"], "plan_version": specification["version"],
        "plan_digest": plan_digest, "window_s": specification["window_s"],
        "status": "COMPUTED" if computed == len(metrics) else "PARTIAL" if computed else "NOT_EVALUATED",
        "metrics": metrics,
        "semantics": {"read_only": True, "changes_run_status": False,
                      "automatic_configuration_writeback": False, "sampling": "recorded_samples_only"},
    }
