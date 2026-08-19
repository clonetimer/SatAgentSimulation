"""Common helpers for subsystem runner outputs.

The helpers here are intentionally light-weight: they standardize runner-facing
metadata without hiding subsystem-specific results or creating another simulation
layer between subsystem runners and their Basilisk builders.
"""
from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Mapping


def to_jsonable(value: Any) -> Any:
    """Convert dataclasses, enums, paths and nested containers to JSON-safe data."""

    if is_dataclass(value):
        return to_jsonable(asdict(value))
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, (tuple, list, set)):
        return [to_jsonable(v) for v in value]
    return value


def scenario_result(
    subsystem: str,
    scenario: str,
    description: str,
    result: Any,
    *,
    mode: str = "normal",
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Wrap a subsystem scenario result with consistent metadata."""

    payload = to_jsonable(result)
    data = dict(payload) if isinstance(payload, dict) else {"result": payload}
    if extra:
        data.update(to_jsonable(dict(extra)))

    runtime_summary = data.get("runtime_injection_summary")
    if isinstance(runtime_summary, Mapping):
        unsupported = int(runtime_summary.get("unsupported_runtime_injection_count", 0) or 0)
        failed_direct = int(runtime_summary.get("failed_direct_application_count", 0) or 0)
        out_of_scope = int(runtime_summary.get("out_of_scope_runtime_injection_count", 0) or 0)
        runtime_status = "PASS" if unsupported == 0 and failed_direct == 0 else "FAIL"
        data["runtime_injection_status"] = runtime_status
        data["runtime_unsupported_count"] = unsupported
        data["runtime_failed_direct_count"] = failed_direct
        data["runtime_out_of_scope_count"] = out_of_scope
        data["runtime_scope_status"] = "PARTIAL_NATIVE_WITH_OUT_OF_SCOPE_EFFECTS" if out_of_scope else "FULLY_NATIVE_RUNTIME_EFFECTS"
        # Do not allow a scenario to report PASS when its scheduled direct
        # runtime injection could not be applied to a Basilisk target.
        if str(data.get("status", "")).upper() == "PASS" and runtime_status != "PASS":
            data["status"] = "FAIL"
            data["status_reason"] = "runtime_injection_not_applied"

    data.update(
        {
            "mode": mode,
            "subsystem": subsystem,
            "scenario": scenario,
            "description": description,
        }
    )
    return data


def scenario_error(
    subsystem: str,
    scenario: str,
    description: str,
    exc: BaseException,
    *,
    mode: str = "normal",
) -> dict[str, Any]:
    """Represent one failed scenario without aborting a batch runner."""

    return {
        "mode": mode,
        "subsystem": subsystem,
        "scenario": scenario,
        "description": description,
        "error": str(exc),
        "exception_type": type(exc).__name__,
    }


def run_scenario_batch(
    subsystem: str,
    scenario_descriptions: Mapping[str, str],
    run_one: Callable[[str], dict[str, Any]],
    *,
    mode: str = "normal",
) -> dict[str, Any]:
    """Run a named scenario batch and keep per-scenario failures observable."""

    results: dict[str, Any] = {}
    for scenario, description in scenario_descriptions.items():
        try:
            results[scenario] = run_one(scenario)
        except Exception as exc:  # pragma: no cover - used by smoke scripts
            results[scenario] = scenario_error(subsystem, scenario, description, exc, mode=mode)
    return results


def recorder_sample_count(recorders: Mapping[str, Any]) -> int:
    """Return the first available Basilisk recorder sample count."""

    for rec in recorders.values():
        if hasattr(rec, "times"):
            return len(rec.times())
    return 0
