"""HF-8 physical validation checks for Route-B model outputs.

The checks in this module are intentionally conservative engineering gates. They
are not proof of high-fidelity physics; they catch obvious non-physical outputs
and make model limits auditable in summaries and manifests.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping, Sequence
import math

HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION = "hf8.physical_validation.v2"
MAX_PHYSICAL_ISSUE_SAMPLES = 2048

SOC_FIELDS = ("eps.battery.soc", "eps.battery_soc", "battery_soc")
FUEL_FIELDS = ("propulsion.propellant.remaining_kg", "propulsion.fuel_mass_kg", "propellant_remaining_kg")
POINTING_FIELDS = ("adcs.pointing_error_deg", "attitude.pointing_error_deg")
POWER_PROXY_FIELDS = (
    "eps.solar.array_power_w", "eps.solar_array_power_w",
    "eps.loads.requested_power_w", "eps.loads.adcs_power_w",
    "eps.loads.payload_power_w", "eps.loads.comm_power_w", "eps.loads.heater_power_w",
    "thermal.heater.power_w", "coupled.energy.solar_to_eps_w",
    "coupled.energy.eps_to_thermal_heat_w", "comm.power_w", "payload.power_w",
    "propulsion.thrust_n", "propulsion.total_thrust_n", "propulsion.electrical_power_w",
)


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
    try:
        out = float(value)
    except OverflowError:
        return None
    return out if math.isfinite(out) else None


def _rows(rows: Iterable[Mapping[str, Any]] | None) -> list[dict[str, Any]]:
    return [dict(row) for row in rows or []]


def _values(trace_rows: Sequence[Mapping[str, Any]], field_names: Sequence[str]) -> list[tuple[int, str, float]]:
    out: list[tuple[int, str, float]] = []
    wanted = tuple(dict.fromkeys(field_names))
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


def _is_kelvin_field(name: str) -> bool:
    return name.endswith(("temp_k", "temperature_k"))


def _is_temperature_field(name: str) -> bool:
    return name.endswith(("temp_c", "temperature_c")) or _is_kelvin_field(name)


def check_soc_bounds(trace_rows: Sequence[Mapping[str, Any]], *, tolerance: float = 1.0e-9) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    for idx, key, soc in _values(trace_rows, SOC_FIELDS):
        if soc < -tolerance or soc > 1.0 + tolerance:
            issues.append(PhysicalValidationIssue("fail", "soc_bounds", f"trace[{idx}].{key}", "battery SOC must remain in [0, 1]", soc, "[0, 1]"))
        elif soc < 0.02 or soc > 0.98:
            issues.append(PhysicalValidationIssue("warning", "soc_bounds", f"trace[{idx}].{key}", "battery SOC is near an operational boundary", soc, "warning outside [0.02, 0.98]"))
    return issues


def _is_thermal_safety_field(name: str) -> bool:
    return name == "thermal.safe" or (name.startswith("thermal.") and name.endswith("_safe"))


def check_thermal_bounds(trace_rows: Sequence[Mapping[str, Any]], *, warning_min_c: float = -60.0, warning_max_c: float = 90.0, fail_min_c: float = -120.0, fail_max_c: float = 150.0) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    for idx, key, temp in _matching_values(trace_rows, _is_temperature_field):
        offset = 273.15 if _is_kelvin_field(key) else 0.0
        unit = "K" if offset else "degC"
        if temp - offset < fail_min_c or temp - offset > fail_max_c:
            issues.append(PhysicalValidationIssue("fail", "thermal_bounds", f"trace[{idx}].{key}", "temperature is outside hard physical validation envelope", temp, f"[{fail_min_c + offset}, {fail_max_c + offset}] {unit}"))
        elif temp - offset < warning_min_c or temp - offset > warning_max_c:
            issues.append(PhysicalValidationIssue("warning", "thermal_bounds", f"trace[{idx}].{key}", "temperature is outside nominal engineering warning envelope", temp, f"[{warning_min_c + offset}, {warning_max_c + offset}] {unit}"))
    for idx, row in enumerate(trace_rows):
        for key, raw in row.items():
            if _is_thermal_safety_field(str(key)) and raw in (0, False):
                issues.append(PhysicalValidationIssue("fail", "thermal_bounds", f"trace[{idx}].{key}",
                    "model-declared thermal operating limit was exceeded", raw, "safe == 1"))
    return issues


def check_orbit_altitude_sanity(trace_rows: Sequence[Mapping[str, Any]], *, min_altitude_m: float = 100_000.0, max_altitude_m: float = 3_000_000.0) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    for idx, key, alt in _values(trace_rows, ("orbit.altitude_m",)):
        if alt < min_altitude_m or alt > max_altitude_m:
            issues.append(PhysicalValidationIssue("fail", "orbit_altitude_sanity", f"trace[{idx}].{key}", "LEO Route-B orbit altitude is outside supported envelope", alt, f"[{min_altitude_m}, {max_altitude_m}] m"))
    return issues


def check_fuel_monotonicity(trace_rows: Sequence[Mapping[str, Any]], *, tolerance_kg: float = 1.0e-9) -> list[PhysicalValidationIssue]:
    issues: list[PhysicalValidationIssue] = []
    values = _values(trace_rows, FUEL_FIELDS)
    previous: dict[str, float] = {}
    for idx, key, fuel in values:
        if fuel < -tolerance_kg:
            issues.append(PhysicalValidationIssue("fail", "fuel_monotonicity", f"trace[{idx}].{key}", "propellant remaining must be non-negative", fuel, ">= 0 kg"))
        if key in previous and fuel > previous[key] + tolerance_kg:
            issues.append(PhysicalValidationIssue("fail", "fuel_monotonicity", f"trace[{idx}].{key}", "propellant remaining must be monotonic non-increasing", fuel, f"<= previous {previous[key]}"))
        previous[key] = fuel
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
    values = _values(trace_rows, POINTING_FIELDS)
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
    for idx, key, value in _values(trace_rows, POWER_PROXY_FIELDS):
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
_CHECK_FIELDS = {
    "soc_bounds": SOC_FIELDS,
    "orbit_altitude_sanity": ("orbit.altitude_m",),
    "fuel_monotonicity": FUEL_FIELDS,
    "data_conservation": ("coupled.data.conservation_error_bits",),
    "pointing_convergence": POINTING_FIELDS,
    "energy_balance_proxy": POWER_PROXY_FIELDS,
}


def physical_validation_options(task_spec: Mapping[str, Any] | None) -> dict[str, Any]:
    """Read the same legacy/canonical physical-check contract in all runners."""

    spec = task_spec if isinstance(task_spec, Mapping) else {}
    root = spec.get("validation") if isinstance(spec.get("validation"), Mapping) else {}
    model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
    model_validation = model.get("validation") if isinstance(model.get("validation"), Mapping) else {}
    config = {**root, **model_validation}
    requested = config.get("physical_checks", config.get("checks"))
    if requested is not None and (not isinstance(requested, Sequence) or isinstance(requested, (str, bytes))):
        requested = ("invalid_physical_check_selection",)
    return {
        "checks": requested,
        "strict": bool(config.get("strict_physical", config.get("strict", False))),
    }


def _check_evidence_items(check_id: str, row: Mapping[str, Any]):
    if check_id in _CHECK_FIELDS:
        return ((name, row[name]) for name in _CHECK_FIELDS[check_id] if name in row)
    predicate = (lambda name: _is_temperature_field(name) or _is_thermal_safety_field(name)) if check_id == "thermal_bounds" else lambda name: name.endswith("quaternion_norm")
    return ((str(name), value) for name, value in row.items() if predicate(str(name)))


class PhysicalValidationAccumulator:
    """Shared batch/stream checks with bounded findings and explicit coverage.

    Memory scales with the selected checks, telemetry field names and the fixed
    issue sample budget, never with frame count. All issues are counted; each
    check/severity group retains its first and last finding even after sampling
    is exhausted. Original telemetry remains the full finding-location source.
    """

    def __init__(self, *, checks: Sequence[str] | None = None, strict: bool = False,
                 max_issue_samples: int = MAX_PHYSICAL_ISSUE_SAMPLES) -> None:
        self.selected = tuple(dict.fromkeys(str(value) for value in (DEFAULT_CHECKS if checks is None else checks)))
        self.explicit_checks = checks is not None
        self.strict = bool(strict)
        self.count = 0
        self.max_issue_samples = max(0, int(max_issue_samples))
        self.issues: list[PhysicalValidationIssue] = []
        self.issue_counts: dict[tuple[str, str], dict[str, Any]] = {}
        self.coverage = {name: {"valid_row_count": 0, "valid_sample_count": 0,
                               "invalid_sample_count": 0, "fields": {}}
                         for name in self.selected}
        self.last_fuel: dict[str, float] = {}
        self.pointing: dict[str, dict[str, Any]] = {}
        for name in self.selected:
            if name not in CHECK_FUNCTIONS:
                self._push(PhysicalValidationIssue(
                    "fail" if self.strict else "warning", "unknown_check", "validation.checks",
                    f"unknown physical validation check: {name}", name, sorted(CHECK_FUNCTIONS),
                ))

    def _push(self, issue: PhysicalValidationIssue) -> None:
        group = self.issue_counts.setdefault((issue.check_id, issue.severity), {
            "check_id": issue.check_id, "severity": issue.severity, "count": 0,
            "first_issue": issue.to_dict(), "last_issue": issue.to_dict(),
        })
        group["count"] += 1
        group["last_issue"] = issue.to_dict()
        if len(self.issues) < self.max_issue_samples:
            self.issues.append(issue)

    def add(self, row: Mapping[str, Any]) -> None:
        index = self.count
        self.count += 1
        for name in self.selected:
            if name not in CHECK_FUNCTIONS:
                continue
            coverage = self.coverage[name]
            values: dict[str, float] = {}
            for key, raw in _check_evidence_items(name, row):
                safety_flag = name == "thermal_bounds" and _is_thermal_safety_field(str(key))
                value = float(raw) if safety_flag and isinstance(raw, (bool, int, float)) and raw in (0, 1) else _finite_number(raw)
                if value is None or (safety_flag and value not in (0., 1.)):
                    coverage["invalid_sample_count"] += 1
                    self._push(PhysicalValidationIssue(
                        "fail", name, f"trace[{index}].{key}",
                        "physical check evidence must be a finite numeric value",
                        repr(raw), "finite non-boolean number",
                    ))
                    continue
                values[str(key)] = value
                coverage["fields"][str(key)] = coverage["fields"].get(str(key), 0) + 1
            if not values:
                continue
            coverage["valid_row_count"] += 1
            coverage["valid_sample_count"] += len(values)
            if name == "fuel_monotonicity":
                for key, fuel in values.items():
                    if fuel < -1e-9:
                        self._push(PhysicalValidationIssue(
                            "fail", name, f"trace[{index}].{key}", "propellant remaining must be non-negative", fuel, ">= 0 kg"))
                    if key in self.last_fuel and fuel > self.last_fuel[key] + 1e-9:
                        self._push(PhysicalValidationIssue(
                            "fail", name, f"trace[{index}].{key}", "propellant remaining must be monotonic non-increasing",
                            fuel, f"<= previous {self.last_fuel[key]}"))
                    self.last_fuel[key] = fuel
            elif name == "pointing_convergence":
                for key, value in values.items():
                    state = self.pointing.setdefault(key, {"count": 0, "first": value, "minimum": value})
                    state.update(count=state["count"] + 1, last=value, last_index=index,
                                 minimum=min(state["minimum"], value))
            else:
                for issue in CHECK_FUNCTIONS[name]([row]):
                    self._push(replace(issue, path=issue.path.replace("trace[0]", f"trace[{index}]")))

    def result(self, *, summary: Mapping[str, Any] | None = None) -> dict[str, Any]:
        # Build final-only findings on a bounded copy: result() is idempotent,
        # and callers can obtain an intermediate report without corrupting state.
        issues = list(self.issues)
        groups = {key: dict(value) for key, value in self.issue_counts.items()}

        def append(issue: PhysicalValidationIssue) -> None:
            group = groups.setdefault((issue.check_id, issue.severity), {
                "check_id": issue.check_id, "severity": issue.severity, "count": 0,
                "first_issue": issue.to_dict(), "last_issue": issue.to_dict(),
            })
            group["count"] += 1
            group["last_issue"] = issue.to_dict()
            if len(issues) < self.max_issue_samples:
                issues.append(issue)

        for key, state in self.pointing.items():
            if state["count"] >= 3 and state["last"] > state["first"] * 1.05 and state["last"] > state["minimum"] + .1:
                append(PhysicalValidationIssue(
                    "warning", "pointing_convergence", f"trace[{state['last_index']}].{key}",
                    "pointing error did not show convergence over the run", state["last"], f"initial {state['first']} deg"))
        run: list[str] = []
        missing: list[str] = []
        check_results: dict[str, dict[str, Any]] = {}
        for name, coverage in self.coverage.items():
            minimum = 3 if name == "pointing_convergence" else 2 if name == "fuel_monotonicity" else 1
            sufficient = name in CHECK_FUNCTIONS and max(coverage["fields"].values(), default=0) >= minimum
            if sufficient:
                run.append(name)
            else:
                missing.append(name)
                if self.strict:
                    append(PhysicalValidationIssue(
                        "fail", name, "validation.physical_checks",
                        "required physical check has insufficient input evidence", coverage["valid_row_count"],
                        f"at least {minimum} finite sample(s) of one supported field"))
            severities = {severity for (check, severity), group in groups.items() if check == name and group["count"]}
            status = "NOT_EVALUATED" if not sufficient else "FAIL" if "fail" in severities else "WARNING" if "warning" in severities else "PASS"
            check_results[name] = {
                "status": status, "evaluated": sufficient,
                "reason_code": "UNKNOWN_PHYSICAL_CHECK" if name not in CHECK_FUNCTIONS else "PHYSICAL_EVIDENCE_MISSING" if not sufficient else "PHYSICAL_CHECK_EVALUATED",
                "minimum_samples_per_field": minimum, "valid_row_count": coverage["valid_row_count"],
                "valid_sample_count": coverage["valid_sample_count"], "invalid_sample_count": coverage["invalid_sample_count"],
                "fields_observed": sorted(coverage["fields"]),
                "finding_fail_count": groups.get((name, "fail"), {}).get("count", 0),
                "finding_warning_count": groups.get((name, "warning"), {}).get("count", 0),
            }
        if self.strict and not self.selected:
            append(PhysicalValidationIssue(
                "fail", "physical_validation", "validation.physical_checks",
                "strict physical validation requires at least one selected check", 0, ">= 1 check"))
        errors = sum(group["count"] for (_, severity), group in groups.items() if severity == "fail")
        warnings = sum(group["count"] for (_, severity), group in groups.items() if severity == "warning")
        issue_count = errors + warnings
        status = ("fail" if errors else "inconclusive" if self.explicit_checks and missing
                  else "not_evaluated" if not run else "warning" if warnings or missing else "pass")
        coverage_status = "NONE" if not run else "PARTIAL" if missing else "COMPLETE"
        return {
            "schema_version": HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION, "route_b_version": "B-5/HF-8",
            "status": status, "gate_status": status, "strict": self.strict,
            "explicit_checks": self.explicit_checks, "trace_row_count": self.count,
            "checks_requested": list(self.selected), "checks_run": run, "checks_missing": missing,
            "coverage_status": coverage_status, "check_results": check_results,
            "fail_count": errors, "warning_count": warnings, "issue_count": issue_count,
            "issues": [issue.to_dict() for issue in issues],
            "issue_sample_limit": self.max_issue_samples, "issues_truncated": issue_count > len(issues),
            "omitted_issue_count": issue_count - len(issues),
            "issue_counts": [groups[key] for key in sorted(groups)],
            "limitations": (["Only checks with supported finite telemetry inputs were evaluated; missing checks are not passes."] if missing else [])
                + (["Issue locations are sampled; exact counts and first/last findings are retained. Consult original telemetry for every location."] if issue_count > len(issues) else []),
            "summary_status": (summary or {}).get("status"), "can_claim_high_fidelity": False,
            "reason_high_fidelity_still_blocked": "HF-8 checks provide scoped internal verification, not calibrated physical accuracy or flight validity.",
        }


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

    accumulator = PhysicalValidationAccumulator(checks=checks, strict=strict)
    for row in trace_rows or ():
        accumulator.add(row)
    return accumulator.result(summary=summary)


def build_hf8_physical_validation_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    options = physical_validation_options(task_spec)
    checks = options["checks"] if options["checks"] is not None else DEFAULT_CHECKS
    return {
        "schema_version": HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION,
        "route_b_version": "B-5/HF-8",
        "status": "implemented_validation_gates_not_benchmark_envelopes",
        "default_checks": list(DEFAULT_CHECKS),
        "requested_checks": [str(x) for x in checks],
        "strict": options["strict"],
        "can_claim_high_fidelity": False,
        "remaining_route_b_dependencies": ["HF-9 benchmark scenarios and tolerance envelopes"],
    }


__all__ = [
    "HF8_PHYSICAL_VALIDATION_SCHEMA_VERSION",
    "PhysicalValidationIssue",
    "PhysicalValidationAccumulator",
    "MAX_PHYSICAL_ISSUE_SAMPLES",
    "physical_validation_options",
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
