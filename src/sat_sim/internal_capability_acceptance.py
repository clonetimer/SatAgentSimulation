"""Final acceptance contracts for the six active/internal recovery capabilities.

The acceptance path deliberately uses the same Capability Registry, form
projection, validator, compiler, execution port and adapters as product runs.
It does not require an offline wheel bundle to be present in ``third_party``;
Basilisk readiness is determined by importing the runtime and its required
modules in the active Python environment.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import math
import platform
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .capability_registry import get_adapter_for_capability, get_capability
from .form_schema import capability_form_schema
from .legacy_execution.base import LegacyExecutionAdapterBase
from .task_compiler import compile_task_spec, dataclass_to_dict
from .task_validator import validate_task_spec
from .unified_agent import normalize_form_task_spec
from .unified_execution import execute_compiled_task

ACCEPTANCE_SCHEMA_VERSION = "sat-sim.internal-capability-acceptance.v2"
ACCEPTED_BSK_RUNTIME_VERSIONS = frozenset({"2.11.0", "2.11.0+satfix1"})

SIX_ACTIVE_INTERNAL_CAPABILITIES: tuple[str, ...] = (
    "reference.public_satellite_case.v1",
    "subsystem.adcs_basilisk_fsw.v1",
    "subsystem.thermal_reduced_order.v1",
    "whole_spacecraft.basilisk_6dof.v1",
    "whole_spacecraft.orbit_attitude_thermal.v1",
    "whole_spacecraft.bsksim_foundation.v1",
)

BASILISK_CAPABILITIES = frozenset(
    {
        "subsystem.adcs_basilisk_fsw.v1",
        "whole_spacecraft.basilisk_6dof.v1",
        "whole_spacecraft.bsksim_foundation.v1",
    }
)

# Every tuple is an AND requirement. Items inside a frozenset are OR aliases.
REQUIRED_TRACE_GROUPS: dict[str, tuple[frozenset[str], ...]] = {
    "reference.public_satellite_case.v1": (),
    "subsystem.adcs_basilisk_fsw.v1": (
        frozenset({"time_s"}),
        frozenset({"attitude.sigma_bn_norm", "control.sigma_br_norm"}),
        frozenset({"attitude.omega_bn_b_norm_rad_s", "control.omega_br_b_norm_rad_s"}),
        frozenset({"control.cmd_torque_b_norm_nm"}),
        frozenset({"rw.motor_torque_norm_nm"}),
        frozenset({"rw.speed_rad_s_max_abs"}),
    ),
    "subsystem.thermal_reduced_order.v1": (
        frozenset({"time_s"}),
        frozenset({"thermal.node.internal.temp_c"}),
        frozenset({"thermal.radiator.total_reject_w"}),
        frozenset({"thermal.heater.power_w"}),
    ),
    "whole_spacecraft.basilisk_6dof.v1": (
        frozenset({"time_s"}),
        frozenset({"orbit.radius_m", "orbit.altitude_m"}),
        frozenset({"orbit.speed_m_s"}),
        frozenset({"attitude.sigma_bn_norm", "control.sigma_br_norm"}),
        frozenset({"attitude.omega_bn_b_norm_rad_s", "control.omega_br_b_norm_rad_s"}),
        frozenset({"control.cmd_torque_b_norm_nm"}),
        frozenset({"rw.speed_rad_s_max_abs"}),
    ),
    "whole_spacecraft.orbit_attitude_thermal.v1": (
        frozenset({"time_s"}),
        frozenset({"thermal.node.internal.temp_c"}),
        frozenset({"thermal.radiator.total_reject_w"}),
        frozenset({"attitude.pointing_error_deg", "attitude.mode", "environment.shadow_factor"}),
    ),
    "whole_spacecraft.bsksim_foundation.v1": (
        frozenset({"time_s"}),
        frozenset({"orbit.theta_rad"}),
        frozenset({"orbit.radius_m"}),
        frozenset({"attitude.pointing_error_deg"}),
        frozenset({"attitude.sigma_br_norm"}),
        frozenset({"adcs.rw.speed_rad_s_0"}),
    ),
}


@dataclass(frozen=True)
class RuntimeDependencyEvidence:
    importable: bool
    distribution_name: str | None
    distribution_version: str | None
    version_accepted: bool
    module_path: str | None
    required_module_imports: dict[str, bool]
    error: str | None = None

    @property
    def ready(self) -> bool:
        return self.importable and self.version_accepted and all(self.required_module_imports.values())

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["ready"] = self.ready
        payload["accepted_versions"] = sorted(ACCEPTED_BSK_RUNTIME_VERSIONS)
        payload["evidence_mode"] = "runtime_import_and_distribution_identity"
        return payload


@dataclass(frozen=True)
class TraceQualityEvidence:
    row_count: int
    field_count: int
    fields: tuple[str, ...]
    time_axis_required: bool
    time_axis_present: bool
    time_monotonic: bool
    time_unique: bool
    finite_numeric_values: bool
    missing_required_groups: tuple[tuple[str, ...], ...]

    @property
    def passed(self) -> bool:
        return (
            self.row_count > 0
            and (not self.time_axis_required or self.time_axis_present)
            and (not self.time_axis_required or self.time_monotonic)
            and (not self.time_axis_required or self.time_unique)
            and self.finite_numeric_values
            and not self.missing_required_groups
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["passed"] = self.passed
        return payload


def basilisk_runtime_evidence() -> RuntimeDependencyEvidence:
    required = (
        "Basilisk.architecture.messaging",
        "Basilisk.simulation.spacecraft",
        "Basilisk.simulation.reactionWheelStateEffector",
        "Basilisk.simulation.simpleNav",
        "Basilisk.simulation.extForceTorque",
        "Basilisk.fswAlgorithms.inertial3D",
        "Basilisk.fswAlgorithms.attTrackingError",
        "Basilisk.fswAlgorithms.mrpFeedback",
        "Basilisk.fswAlgorithms.rwMotorTorque",
        "Basilisk.utilities.SimulationBaseClass",
        "Basilisk.utilities.macros",
        "Basilisk.utilities.orbitalMotion",
        "Basilisk.utilities.simIncludeGravBody",
        "Basilisk.utilities.simIncludeRW",
    )
    module_path: str | None = None
    version: str | None = None
    distribution_name: str | None = None
    imports = {name: False for name in required}
    try:
        root = importlib.import_module("Basilisk")
        locations = list(getattr(root, "__path__", ()) or ())
        module_path = str(locations[0]) if locations else str(getattr(root, "__file__", "") or "") or None
        for distribution in ("bsk", "Basilisk"):
            try:
                version = importlib.metadata.version(distribution)
                distribution_name = distribution
                break
            except importlib.metadata.PackageNotFoundError:
                continue
        for name in required:
            importlib.import_module(name)
            imports[name] = True
        return RuntimeDependencyEvidence(True, distribution_name, version, version in ACCEPTED_BSK_RUNTIME_VERSIONS, module_path, imports)
    except Exception as exc:  # runtime identity is evidence; never hide the concrete import error
        return RuntimeDependencyEvidence(
            importable=module_path is not None,
            distribution_name=distribution_name,
            distribution_version=version,
            version_accepted=version in ACCEPTED_BSK_RUNTIME_VERSIONS,
            module_path=module_path,
            required_module_imports=imports,
            error=f"{type(exc).__name__}: {exc}",
        )


def _json_sha256(payload: Any) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _issue_dict(issue: Any) -> dict[str, Any]:
    if hasattr(issue, "to_dict"):
        return dict(issue.to_dict())
    return {
        "severity": str(getattr(issue, "severity", "error")),
        "path": str(getattr(issue, "path", "$")),
        "message": str(getattr(issue, "message", issue)),
        "code": str(getattr(issue, "code", "validation")),
    }


def _is_finite(value: Any) -> bool:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return True
    if isinstance(value, (int, float)):
        return math.isfinite(float(value))
    if isinstance(value, Mapping):
        return all(_is_finite(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return all(_is_finite(item) for item in value)
    return True


def evaluate_trace_quality(capability_id: str, rows: Sequence[Mapping[str, Any]]) -> TraceQualityEvidence:
    fields = tuple(sorted({str(key) for row in rows for key in row.keys()}))
    times: list[float] = []
    time_axis_required = capability_id != "reference.public_satellite_case.v1"
    time_axis_present = bool(rows) and all("time_s" in row for row in rows)
    if time_axis_present:
        try:
            times = [float(row["time_s"]) for row in rows]
        except (TypeError, ValueError):
            time_axis_present = False
    monotonic = time_axis_present and all(b >= a for a, b in zip(times, times[1:]))
    unique = time_axis_present and len(times) == len(set(times))
    finite = all(_is_finite(row) for row in rows)
    field_set = set(fields)
    missing: list[tuple[str, ...]] = []
    for alternatives in REQUIRED_TRACE_GROUPS.get(capability_id, (frozenset({"time_s"}),)):
        if not field_set.intersection(alternatives):
            missing.append(tuple(sorted(alternatives)))
    return TraceQualityEvidence(
        row_count=len(rows),
        field_count=len(fields),
        fields=fields,
        time_axis_required=time_axis_required,
        time_axis_present=time_axis_present,
        time_monotonic=monotonic,
        time_unique=unique,
        finite_numeric_values=finite,
        missing_required_groups=tuple(missing),
    )


def _summary_passed(capability_id: str, summary: Mapping[str, Any]) -> tuple[bool, str]:
    if capability_id == "reference.public_satellite_case.v1":
        passed = str(summary.get("status", "")).lower() in {"pass", "passed", "complete", "completed", "succeeded"}
        return passed, "reference_report_completed" if passed else "reference_report_not_completed"
    if capability_id in {
        "subsystem.thermal_reduced_order.v1",
        "whole_spacecraft.orbit_attitude_thermal.v1",
    }:
        passed = str(summary.get("status", "")).lower() in {"pass", "passed", "complete", "completed", "succeeded"}
        return passed, "physical_orchestration_run_completed" if passed else "physical_orchestration_run_not_completed"
    if capability_id in {
        "subsystem.adcs_basilisk_fsw.v1",
        "whole_spacecraft.basilisk_6dof.v1",
    }:
        passed = summary.get("basilisk_status") == "executed"
        return passed, "basilisk_native_runtime_executed" if passed else f"basilisk_status={summary.get('basilisk_status')!r}"
    if capability_id == "whole_spacecraft.bsksim_foundation.v1":
        passed = bool(summary.get("basilisk_timeline_executed")) and summary.get("runtime_truth_status") == "instantiated_connected_recorded"
        return passed, "basilisk_timeline_instantiated_connected_recorded" if passed else "basilisk_timeline_not_proven"
    return False, "unknown_acceptance_profile"


def build_default_canonical_spec(capability_id: str, *, output_root: str | Path) -> dict[str, Any]:
    form = json.loads(json.dumps(capability_form_schema(capability_id)["default_form"], ensure_ascii=False))
    task_id = f"final_acceptance_{capability_id.replace('.', '_')}"
    form["outputs"]["output_root"] = str(output_root)
    return normalize_form_task_spec(form, task_id=task_id)


def run_capability_acceptance(capability_id: str, *, output_root: str | Path) -> dict[str, Any]:
    if capability_id not in SIX_ACTIVE_INTERNAL_CAPABILITIES:
        raise ValueError(f"unsupported acceptance capability: {capability_id}")
    output_path = Path(output_root)
    output_path.mkdir(parents=True, exist_ok=True)
    contract = get_capability(capability_id)
    adapter = get_adapter_for_capability(capability_id)
    adapter_class = type(adapter)
    implementation = contract.data.get("implementation") if isinstance(contract.data.get("implementation"), Mapping) else {}
    uses_legacy_runner = bool(implementation.get("uses_legacy_runner"))
    inherits_legacy_adapter = issubclass(adapter_class, LegacyExecutionAdapterBase)
    runtime = basilisk_runtime_evidence()
    canonical = build_default_canonical_spec(capability_id, output_root=output_path / "run")
    validation = validate_task_spec(canonical)
    issues = [_issue_dict(issue) for issue in validation.issues]
    errors = [issue for issue in issues if issue.get("severity") == "error"]
    runtime_required_but_missing = capability_id in BASILISK_CAPABILITIES and not runtime.ready
    blocked_by_runtime = runtime_required_but_missing and (
        not errors or all(issue.get("code") == "basilisk_unavailable" for issue in errors)
    )

    base: dict[str, Any] = {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "capability_id": capability_id,
        "profile": "report_only" if capability_id == "reference.public_satellite_case.v1" else "runtime_telemetry",
        "adapter": {
            "class_path": f"{adapter_class.__module__}.{adapter_class.__name__}",
            "adapter_key": contract.adapter_class_path,
            "uses_legacy_runner": uses_legacy_runner,
            "inherits_legacy_adapter": inherits_legacy_adapter,
            "mro": [f"{item.__module__}.{item.__name__}" for item in adapter_class.__mro__],
        },
        "runtime_dependency": runtime.to_dict(),
        "task_spec": canonical,
        "task_spec_sha256": _json_sha256(canonical),
        "validation": {"issues": issues, "error_count": len(errors)},
        "environment": {
            "python": sys.version,
            "python_executable": sys.executable,
            "platform": platform.platform(),
        },
    }
    if errors or runtime_required_but_missing:
        base.update(
            {
                "status": "BLOCKED_RUNTIME_DEPENDENCY" if blocked_by_runtime else "FAILED_VALIDATION",
                "passed": False,
                "failure_reason": "basilisk_runtime_unavailable" if blocked_by_runtime else "task_spec_validation_failed",
                "summary": {},
                "trace_quality": evaluate_trace_quality(capability_id, ()).to_dict(),
            }
        )
        return base

    try:
        compiled = compile_task_spec(canonical)
        result = execute_compiled_task(
            compiled,
            task_spec=canonical,
            output_root=output_path / "run",
            write_dataset=True,
        )
        summary = dict(result.summary)
        rows = tuple(dict(row) for row in result.trace_rows)
        quality = evaluate_trace_quality(capability_id, rows)
        summary_passed, summary_reason = _summary_passed(capability_id, summary)
        passed = (
            summary_passed
            and quality.passed
            and not uses_legacy_runner
            and not inherits_legacy_adapter
        )
        compiled_payload = dataclass_to_dict(compiled)
        evidence_payload = {
            **base,
            "compiled": compiled_payload,
            "compiled_sha256": _json_sha256(compiled_payload),
            "summary": summary,
            "summary_sha256": _json_sha256(summary),
            "trace_quality": quality.to_dict(),
            "trace_sha256": _json_sha256(rows),
            "runtime_metadata": dict(result.runtime_metadata),
            "dataset": None
            if result.dataset is None
            else {
                "output_root": str(result.dataset.output_root),
                "files": dict(result.dataset.files),
                "manifest": dict(result.dataset.manifest),
            },
            "status": "PASS" if passed else "FAIL",
            "passed": passed,
            "failure_reason": None if passed else summary_reason if not summary_passed else "trace_or_nonlegacy_acceptance_failed",
        }
        return evidence_payload
    except Exception as exc:
        base.update(
            {
                "status": "ERROR",
                "passed": False,
                "failure_reason": f"{type(exc).__name__}: {exc}",
                "summary": {},
                "trace_quality": evaluate_trace_quality(capability_id, ()).to_dict(),
            }
        )
        return base


def summarize_acceptance(records: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
    items = [dict(item) for item in records]
    counts: dict[str, int] = {}
    for item in items:
        status = str(item.get("status", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
    passed = sum(1 for item in items if item.get("passed") is True)
    return {
        "schema_version": ACCEPTANCE_SCHEMA_VERSION,
        "status": "PASS" if passed == len(SIX_ACTIVE_INTERNAL_CAPABILITIES) else "INCOMPLETE",
        "passed": passed == len(SIX_ACTIVE_INTERNAL_CAPABILITIES),
        "capability_count": len(items),
        "pass_count": passed,
        "status_counts": counts,
        "records": items,
    }


__all__ = [
    "ACCEPTANCE_SCHEMA_VERSION",
    "ACCEPTED_BSK_RUNTIME_VERSIONS",
    "BASILISK_CAPABILITIES",
    "REQUIRED_TRACE_GROUPS",
    "SIX_ACTIVE_INTERNAL_CAPABILITIES",
    "TraceQualityEvidence",
    "basilisk_runtime_evidence",
    "build_default_canonical_spec",
    "evaluate_trace_quality",
    "run_capability_acceptance",
    "summarize_acceptance",
]
