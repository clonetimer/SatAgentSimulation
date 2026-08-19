"""HF-8 physical validation checks for Route-B model outputs.

The checks in this module are intentionally conservative engineering gates. They
are not proof of high-fidelity physics; they catch obvious non-physical outputs
and make model limits auditable in summaries and manifests.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping, Sequence
import math

HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION = "hf8.physical_validation.v1"


@dataclass(frozen=True)
class PhysicalValidationIssue:
    """One physical validation gate finding."""

    severity: str
    check_id: str
    path: str
    message: str
    value: Any = None
    limit: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "check_id": self.check_id,
            "path": self.path,
            "message": self.message,
            "value": self.value,
            "limit": self.limit,
        }


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    out = float(value)
    return out if math.isfinite(out) else None


def _rows(rows: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(row) for row in rows or []]


def _values(trace_rows: Sequence[Mapping[str, Any]], field_names: Sequence[str]) -> list[tuple[int, str, float]]:
    out: list[tuple[int, str, float]] = []
    wanted = set(field_names)
    for idx, row in enumerate(trace_rows):
        for key in wanted:
            if key in row:
                value = _finite_number(row.get(key))
                if value is not None:
                    out.append((idx, key, value))
    return out


def _matching_values(trace_rows: Sequence[Mapping[str, Any]], predicate) -> list[tuple[int, str, float]]:
    out: list[tuple[int, str, float]] = []
    for idx, row in enumerate(trace_rows):
        for key, raw in row.items():
            if predicate(str(key)):
                value = _finite_number(raw)
                if value is not None:
                    out.append((idx, str(key), value))
    return out


def check_soc_bounds(trace_rows: Sequence[Mapping[str, Any]], *, tolerance: float = 1.0e-9) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    for idx, key, soc in _values(trace_rows, ("eps.battery.soc", "battery_soc")):
        if soc < -tolerance or soc > 1.0 + tolerance:
            issues.append(PhysicalValidationIssue("fail", "soc_bounds", f"trace[{idx}].{key}", "battery SOC must remain in [0, 1]", soc, "[0, 1]"))
        elif soc < 0.02 or soc > 0.98:
            issues.append(PhysicalValidationIssue("warning", "soc_bounds", f"trace[{idx}].{key}", "battery SOC is near an operational boundary", soc, "warning outside [0.02, 0.98]"))
    return issues


def check_thermal_bounds(trace_rows: Sequence[Mapping[str, Any]], *, warning_min_c: float = -60.0, warning_max_c: float = 90.0, fail_min_c: float = -120.0, fail_max_c: float = 150.0) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    for idx, key, temp in _matching_values(trace_rows, lambda name: name.endswith("temp_c") or name.endswith("temperature_c") or ".temp_c" in name):
        if temp < fail_min_c or temp > fail_max_c:
            issues.append(PhysicalValidationIssue("fail", "thermal_bounds", f"trace[{idx}].{key}", "temperature is outside hard physical validation envelope", temp, f"[{fail_min_c}, {fail_max_c}] degC"))
        elif temp < warning_min_c or temp > warning_max_c:
            issues.append(PhysicalValidationIssue("warning", "thermal_bounds", f"trace[{idx}].{key}", "temperature is outside nominal engineering warning envelope", temp, f"[{warning_min_c}, {warning_max_c}] degC"))
    return issues


def check_orbit_altitude_sanity(trace_rows: Sequence[Mapping[str, Any]], *, min_altitude_m: float = 100_000.0, max_altitude_m: float = 3_000_000.0) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    for idx, key, alt in _values(trace_rows, ("orbit.altitude_m",)):
        if alt < min_altitude_m or alt > max_altitude_m:
            issues.append(PhysicalValidationIssue("fail", "orbit_altitude_sanity", f"trace[{idx}].{key}", "LEO Route-B orbit altitude is outside supported envelope", alt, f"[{min_altitude_m}, {max_altitude_m}] m"))
    return issues


def check_fuel_monotonicity(trace_rows: Sequence[Mapping[str, Any]], *, tolerance_kg: float = 1.0e-9) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    values = _values(trace_rows, ("propulsion.propellant.remaining_kg", "propellant_remaining_kg"))
    last: float | None = None
    for idx, key, fuel in values:
        if fuel < -tolerance_kg:
            issues.append(PhysicalValidationIssue("fail", "fuel_monotonicity", f"trace[{idx}].{key}", "propellant remaining must be non-negative", fuel, ">= 0 kg"))
        if last is not None and fuel > last + tolerance_kg:
            issues.append(PhysicalValidationIssue("fail", "fuel_monotonicity", f"trace[{idx}].{key}", "propellant remaining must be monotonic non-increasing", fuel, f"<= previous {last}"))
        last = fuel
    return issues


def check_data_conservation(trace_rows: Sequence[Mapping[str, Any]], *, tolerance_bits: float = 1.0e-3) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    for idx, key, err in _values(trace_rows, ("coupled.data.conservation_error_bits",)):
        if abs(err) > tolerance_bits:
            issues.append(PhysicalValidationIssue("fail", "data_conservation", f"trace[{idx}].{key}", "data conservation residual exceeds tolerance", err, f"abs <= {tolerance_bits} bit"))
    return issues


def check_quaternion_norm(trace_rows: Sequence[Mapping[str, Any]], *, warning_tol: float = 1.0e-3, fail_tol: float = 5.0e-2) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    values = _matching_values(trace_rows, lambda name: name.endswith("quaternion_norm") or name in {"adcs.quaternion_norm", "attitude.quaternion_norm"})
    for idx, key, norm in values:
        err = abs(norm - 1.0)
        if err > fail_tol:
            issues.append(PhysicalValidationIssue("fail", "quaternion_norm", f"trace[{idx}].{key}", "quaternion norm drift exceeds hard tolerance", norm, f"abs(norm-1) <= {fail_tol}"))
        elif err > warning_tol:
            issues.append(PhysicalValidationIssue("warning", "quaternion_norm", f"trace[{idx}].{key}", "quaternion norm drift exceeds warning tolerance", norm, f"abs(norm-1) <= {warning_tol}"))
    return issues


def check_pointing_convergence(trace_rows: Sequence[Mapping[str, Any]]) -> list[PhysicalValidationIssue]:
    values = _values(trace_rows, ("adcs.pointing_error_deg", "attitude.pointing_error_deg"))
    if len(values) < 3:
        return []
    first = values[0][2]
    last = values[-1][2]
    min_seen = min(v for _, _, v in values)
    if last > first * 1.05 and last > min_seen + 0.1:
        return [PhysicalValidationIssue("warning", "pointing_convergence", f"trace[{values[-1][0]}].{values[-1][1]}", "pointing error did not show convergence over the run", last, f"initial {first} deg")]
    return []


def check_energy_balance_proxy(trace_rows: Sequence[Mapping[str, Any]]) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    nonnegative_fields = (
        "eps.solar.array_power_w",
        "eps.loads.requested_power_w",
        "thermal.heater.power_w",
        "coupled.energy.solar_to_eps_w",
        "coupled.energy.eps_to_thermal_heat_w",
        "comm.power_w",
        "payload.power_w",
        "propulsion.thrust_n",
    )
    for idx, key, value in _values(trace_rows, nonnegative_fields):
        if value < -1.0e-9:
            issues.append(PhysicalValidationIssue("fail", "energy_balance_proxy", f"trace[{idx}].{key}", "power/thrust proxy fields must be non-negative", value, ">= 0"))
    return issues


CHECK_FUNCTIONS = {
    "soc_bounds": check_soc_bounds,
    "thermal_bounds": check_thermal_bounds,
    "orbit_altitude_sanity": check_orbit_altitude_sanity,
    "fuel_monotonicity": check_fuel_monotonicity,
    "data_conservation": check_data_conservation,
    "quaternion_norm": check_quaternion_norm,
    "pointing_convergence": check_pointing_convergence,
    "energy_balance_proxy": check_energy_balance_proxy,
}

DEFAULT_CHECKS: tuple[str, ...] = tuple(CHECK_FUNCTIONS.keys())


def evaluate_physical_validation(
    trace_rows: Iterable[Mapping[str, Any]] | None,
    *,
    summary: Mapping[str, Any] | None = None,
    checks: Sequence[str] | None = None,
    strict: bool = False,
) -> dict[str, Any]:
    """Run HF-8 physical validation gates over trace rows.

    Unknown checks are recorded as warnings unless ``strict`` is true, in which
    case they become failures.  This keeps the validation layer extensible while
    still auditable.
    """

    rows = _rows(trace_rows)
    selected = tuple(checks or DEFAULT_CHECKS)
    issues: list[PhysicalValidationIssue] = []
    checks_run: list[str] = []
    for check_id in selected:
        fn = CHECK_FUNCTIONS.get(str(check_id))
        if fn is None:
            severity = "fail" if strict else "warning"
            issues.append(PhysicalValidationIssue(severity, "unknown_check", "validation.checks", f"unknown physical validation check: {check_id}", check_id, sorted(CHECK_FUNCTIONS)))
            continue
        checks_run.append(str(check_id))
        issues.extend(fn(rows))
    fail_count = sum(1 for issue in issues if issue.severity == "fail")
    warning_count = sum(1 for issue in issues if issue.severity == "warning")
    status = "fail" if fail_count else "warning" if warning_count else "pass"
    return {
        "schema_version": HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION,
        "route_b_version": "B-5/HF-8",
        "status": status,
        "gate_status": status,
        "strict": bool(strict),
        "trace_row_count": len(rows),
        "checks_run": checks_run,
        "fail_count": fail_count,
        "warning_count": warning_count,
        "issue_count": len(issues),
        "issues": [issue.to_dict() for issue in issues],
        "summary_status": (summary or {}).get("status") if isinstance(summary, Mapping) else None,
        "can_claim_high_fidelity": False,
        "reason_high_fidelity_still_blocked": "HF-8 gates catch basic physical violations; HF-9 benchmark envelopes add regression evidence, but external validation and higher-fidelity model families are still pending.",
    }


def build_hf8_physical_validation_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    validation = spec.get("validation") if isinstance(spec.get("validation"), Mapping) else {}
    checks = validation.get("checks") if isinstance(validation.get("checks"), Sequence) and not isinstance(validation.get("checks"), (str, bytes)) else DEFAULT_CHECKS
    return {
        "schema_version": HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION,
        "route_b_version": "B-5/HF-8",
        "status": "implemented_validation_gates_not_benchmark_envelopes",
        "default_checks": list(DEFAULT_CHECKS),
        "requested_checks": [str(x) for x in checks],
        "strict": bool(validation.get("strict", False)),
        "can_claim_high_fidelity": False,
        "remaining_route_b_dependencies": ["HF-9 benchmark scenarios and tolerance envelopes"],
    }


__all__ = [
    "HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION",
    "PhysicalValidationIssue",
    "DEFAULT_CHECKS",
    "CHECK_FUNCTIONS",
    "evaluate_physical_validation",
    "build_hf8_physical_validation_payload",
    "check_soc_bounds",
    "check_thermal_bounds",
    "check_orbit_altitude_sanity",
    "check_fuel_monotonicity",
    "check_data_conservation",
    "check_quaternion_norm",
    "check_pointing_convergence",
    "check_energy_balance_proxy",
]
