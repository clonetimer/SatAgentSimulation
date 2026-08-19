"""AstroGraph-facing dataset contract and telemetry projection.

This module deliberately sits *after* the normal sat-sim dataset writer.  It does
not replace ``manifest.json`` and does not alter the physical simulation.  It
adds a stable, auditable sidecar that AstroGraph can consume without importing
sat-sim internals.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .simulation_fidelity import classify_simulation_fidelity
from .experiment_record import write_experiment_record
from .rw_actuator_contract import load_reference_rw_actuator_contract

ASTROGRAPH_DATASET_SCHEMA_VERSION = "sat-sim.astrograph-dataset.v2"
ASTROGRAPH_INDEX_SCHEMA_VERSION = "sat-sim.astrograph-dataset-index.v2"
ASTROGRAPH_RW_PROFILE_ID = "astrograph-rw-temporal-basilisk-5ch.v2"

ASTROGRAPH_RW_REQUIRED_CHANNELS: tuple[str, ...] = (
    "sensor.adcs.rw.speed_rad_s_0",
    "command.adcs.rw.motor_torque_nm_0",
    "sensor.adcs.rw.motor_current_a_0",
    "estimate.adcs.rw.actual_torque_nm_0",
    "estimate.adcs.pointing_error_deg",
)
ASTROGRAPH_RW_OPTIONAL_CHANNELS: tuple[str, ...] = (
    "estimate.adcs.rw.friction_torque_nm_0",
    "status.adcs.rw.available_0",
    "estimate.adcs.rw.effective_max_torque_nm_0",
)
ASTROGRAPH_RW_MODEL_INPUT_CHANNELS: tuple[str, ...] = ASTROGRAPH_RW_REQUIRED_CHANNELS
ASTROGRAPH_RW_PHYSICS_ONLY_CHANNELS: tuple[str, ...] = (
    "estimate.adcs.rw.friction_torque_nm_0",
    "status.adcs.rw.available_0",
    "estimate.adcs.rw.effective_max_torque_nm_0",
)


@dataclass(frozen=True)
class ChannelProjection:
    target: str
    sources: tuple[str, ...]
    unit: str
    provenance: str = "direct"
    formula: str | None = None


@dataclass(frozen=True)
class AstroGraphCaseMetadata:
    split: str
    class_id: str
    pair_id: str
    case_role: str
    fault_case_id: str | None = None
    nominal_case_id: str | None = None
    simulator_effect: str | None = None
    simulator_event_kind: str | None = None
    onset_time_s: float | None = None
    end_time_s: float | None = None
    severity: float | None = None
    severity_descriptor: dict[str, Any] | None = None
    target: str = "adcs.reaction_wheel.0"
    independent_run_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class AstroGraphDatasetWriteResult:
    dataset_root: Path
    contract_path: Path
    telemetry_path: Path
    contract: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "dataset_root": str(self.dataset_root),
            "contract_path": str(self.contract_path),
            "telemetry_path": str(self.telemetry_path),
            "contract": self.contract,
        }


_DIRECT_PROJECTIONS: tuple[ChannelProjection, ...] = (
    ChannelProjection(
        "sensor.adcs.rw.speed_rad_s_0",
        ("adcs.rw.speed_rad_s_{wheel}", "adcs.reaction_wheel.speed_rad_s_{wheel}"),
        "rad/s",
    ),
    ChannelProjection(
        "command.adcs.rw.motor_torque_nm_0",
        ("adcs.rw.command_torque_nm_{wheel}", "adcs.reaction_wheel.command_torque_nm_{wheel}"),
        "N*m",
    ),
    ChannelProjection(
        "sensor.adcs.rw.motor_current_a_0",
        ("adcs.rw.motor_current_a_{wheel}",),
        "A",
        provenance="physics_derived_from_native_command",
        formula="rwMotorTorqueOutMsg.motorTorque / rw_motor_torque_constant_nm_per_a",
    ),
    ChannelProjection(
        "estimate.adcs.rw.actual_torque_nm_0",
        ("adcs.control.applied_torque_nm_{wheel}", "adcs.reaction_wheel.applied_torque_nm_{wheel}"),
        "N*m",
    ),
    ChannelProjection(
        "estimate.adcs.pointing_error_deg",
        ("adcs.pointing.error_deg", "adcs.pointing_error_deg"),
        "deg",
    ),
    ChannelProjection(
        "estimate.adcs.rw.effective_max_torque_nm_0",
        ("adcs.rw.effective_max_torque_nm_{wheel}", "adcs.reaction_wheel.effective_max_torque_nm_{wheel}"),
        "N*m",
    ),
    ChannelProjection("label.fault_active", ("label.fault_active",), "bool"),
    ChannelProjection("label.degradation_active", ("label.degradation_active",), "bool"),
    ChannelProjection("label.constraint_active", ("label.constraint_active",), "bool"),
)


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"expected JSON object: {path}")
    return payload


def _read_trace(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".csv":
        with path.open("r", newline="", encoding="utf-8") as handle:
            return [dict(row) for row in csv.DictReader(handle)]
    if path.suffix.lower() == ".jsonl":
        rows: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
        return rows
    raise ValueError(f"AstroGraph projection currently supports CSV/JSONL traces, got {path.name}")


def _first(row: Mapping[str, Any], sources: Sequence[str]) -> Any:
    for name in sources:
        if name in row and row.get(name) not in (None, ""):
            return row.get(name)
    return None


def _float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"true", "1", "yes"}:
        return True
    if text in {"false", "0", "no"}:
        return False
    return None


def _target_wheel_index(metadata: AstroGraphCaseMetadata) -> int:
    try:
        index = int(str(metadata.target).rsplit(".", 1)[-1])
    except (TypeError, ValueError):
        index = 0
    return max(0, index)


def _project_row(row: Mapping[str, Any], metadata: AstroGraphCaseMetadata) -> dict[str, Any]:
    out: dict[str, Any] = {}
    time_value = _first(row, ("time_s", "t_s"))
    out["time_s"] = _float(time_value) if _float(time_value) is not None else time_value
    wheel_index = _target_wheel_index(metadata)

    for item in _DIRECT_PROJECTIONS:
        sources = tuple(source.format(wheel=wheel_index) for source in item.sources)
        value = _first(row, sources)
        if value is None:
            continue
        if item.unit == "bool":
            parsed = _bool(value)
        elif item.unit not in {"text", "enum"}:
            parsed = _float(value)
        else:
            parsed = value
        out[item.target] = value if parsed is None else parsed

    direct_friction = _float(_first(row, (
        f"adcs.rw.effective_friction_torque_nm_{wheel_index}",
        f"adcs.reaction_wheel.friction_torque_nm_{wheel_index}",
    )))
    if direct_friction is not None:
        out["estimate.adcs.rw.friction_torque_nm_0"] = abs(direct_friction)
    else:
        damping = _float(_first(row, (
            f"adcs.rw.effective_drag_nms_{wheel_index}",
            f"adcs.reaction_wheel.damping_nms_{wheel_index}",
        )))
        speed = _float(out.get("sensor.adcs.rw.speed_rad_s_0"))
        if damping is not None and speed is not None:
            out["estimate.adcs.rw.friction_torque_nm_0"] = abs(damping * speed)

    if "sensor.adcs.rw.motor_current_a_0" not in out:
        command_torque = _float(out.get("command.adcs.rw.motor_torque_nm_0"))
        reference_contract = load_reference_rw_actuator_contract()
        kt = _float((metadata.metadata or {}).get("rw_motor_torque_constant_nm_per_a"))
        if kt is None:
            kt = reference_contract.motor_torque_constant_nm_per_a
        current_limit = _float((metadata.metadata or {}).get("rw_motor_current_limit_a"))
        if current_limit is None:
            current_limit = reference_contract.current_limit_a
        if command_torque is not None and kt is not None and kt > 0:
            current = command_torque / kt
            if current_limit is not None and current_limit > 0:
                current = max(-current_limit, min(current_limit, current))
            out["sensor.adcs.rw.motor_current_a_0"] = current

    effective_torque = _float(out.get("estimate.adcs.rw.effective_max_torque_nm_0"))
    if effective_torque is not None:
        out["status.adcs.rw.available_0"] = effective_torque > 1.0e-12

    out["label.class_id"] = metadata.class_id
    out["label.split"] = metadata.split
    out["label.pair_id"] = metadata.pair_id
    out["label.case_role"] = metadata.case_role
    return out


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        for key in row:
            if key not in fields:
                fields.append(str(key))
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields or ["time_s"])
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})


def _channel_contract(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    present = sorted({str(key) for row in rows for key in row})
    required = list(ASTROGRAPH_RW_REQUIRED_CHANNELS)
    optional = list(ASTROGRAPH_RW_OPTIONAL_CHANNELS)
    available = [name for name in required if name in present]
    missing = [name for name in required if name not in present]
    available_optional = [name for name in optional if name in present]
    model_inputs_available = [name for name in ASTROGRAPH_RW_MODEL_INPUT_CHANNELS if name in present]
    model_inputs_missing = [name for name in ASTROGRAPH_RW_MODEL_INPUT_CHANNELS if name not in present]
    derivations = [
        {
            "target": "estimate.adcs.rw.friction_torque_nm_0",
            "formula": "abs(native friction torque) or abs(Basilisk damping coefficient * wheel speed)",
            "provenance": "direct_or_physics_derived",
        },
        {
            "target": "sensor.adcs.rw.motor_current_a_0",
            "formula": "rwMotorTorqueOutMsg.motorTorque / rw_motor_torque_constant_nm_per_a, clipped to current limit",
            "provenance": "physics_derived_from_native_command",
        },
        {
            "target": "status.adcs.rw.available_0",
            "formula": "estimate.adcs.rw.effective_max_torque_nm_0 > 1e-12",
            "provenance": "engineering_derived",
        },
    ]
    return {
        "profile_id": ASTROGRAPH_RW_PROFILE_ID,
        "required_channels": required,
        "available_required_channels": available,
        "missing_required_channels": missing,
        "optional_channels": optional,
        "available_optional_channels": available_optional,
        "coverage_ratio": len(available) / len(required) if required else 1.0,
        "all_projected_channels": present,
        "derivations": derivations,
        "training_feature_policy": {
            "allowed_model_input_channels": list(ASTROGRAPH_RW_MODEL_INPUT_CHANNELS),
            "available_model_input_channels": model_inputs_available,
            "missing_model_input_channels": model_inputs_missing,
            "physics_validation_only_channels": list(ASTROGRAPH_RW_PHYSICS_ONLY_CHANNELS),
            "forbidden_label_prefixes": ["label."],
            "target_leakage_guard": True,
            "policy_reason": "fault-injection truth and directly derived availability/authority channels cannot train AstroGraph models",
        },
        "profile_compatibility": {
            "frozen_six_channel_lstm_candidate": False,
            "reason": "five model channels include native command torque and signed current derived by I=tau/Kt under an explicit actuator contract",
        },
        "channel_ready": not missing,
        "model_input_ready": not model_inputs_missing,
    }


def write_astrograph_case_package(
    dataset_root: str | Path,
    metadata: AstroGraphCaseMetadata,
) -> AstroGraphDatasetWriteResult:
    """Write canonical telemetry and an AstroGraph contract beside a sat-sim run."""

    root = Path(dataset_root)
    manifest_path = root / "manifest.json"
    task_spec_path = root / "task_spec.json"
    if not manifest_path.exists() or not task_spec_path.exists():
        raise FileNotFoundError("sat-sim manifest.json and task_spec.json are required")
    manifest = _read_json(manifest_path)
    task_spec = _read_json(task_spec_path)
    files = manifest.get("files") if isinstance(manifest.get("files"), Mapping) else {}
    trace_rel = files.get("trace")
    if not trace_rel:
        raise ValueError("sat-sim dataset has no trace file")
    trace_path = root / str(trace_rel)
    source_rows = _read_trace(trace_path)
    task_parameters = task_spec.get("parameters") if isinstance(task_spec.get("parameters"), Mapping) else {}
    merged_metadata = AstroGraphCaseMetadata(**{**metadata.__dict__, "metadata": {**dict(metadata.metadata or {}), **{
        key: task_parameters.get(key) for key in ("rw_motor_torque_constant_nm_per_a", "rw_motor_current_limit_a", "rw_wheel_inertia_kg_m2") if task_parameters.get(key) is not None
    }}})
    projected = [_project_row(row, merged_metadata) for row in source_rows]

    telemetry_path = root / "astrograph" / "telemetry.csv"
    _write_csv(telemetry_path, projected)
    channel_contract = _channel_contract(projected)

    summary: dict[str, Any] = {}
    summary_rel = files.get("summary")
    if summary_rel and (root / str(summary_rel)).exists():
        summary = _read_json(root / str(summary_rel))
    simulation_spec = task_spec.get("simulation") if isinstance(task_spec.get("simulation"), Mapping) else {}
    fidelity = classify_simulation_fidelity(
        capability_id=str(task_spec.get("capability_id") or (manifest.get("task") or {}).get("capability_id") or ""),
        backend=str(simulation_spec.get("backend") or ""),
        bsk_version=str((manifest.get("platform") or {}).get("bsk_version") or ""),
        runtime_summary=summary,
        runtime_evidence=manifest.get("runtime_evidence") if isinstance(manifest.get("runtime_evidence"), Mapping) else {},
    )

    ground_truth = {
        "class_id": metadata.class_id,
        "case_role": metadata.case_role,
        "pair_id": metadata.pair_id,
        "fault_case_id": metadata.fault_case_id,
        "nominal_case_id": metadata.nominal_case_id,
        "simulator_effect": metadata.simulator_effect,
        "simulator_event_kind": metadata.simulator_event_kind,
        "onset_time_s": metadata.onset_time_s,
        "end_time_s": metadata.end_time_s,
        "severity": metadata.severity,
        "severity_descriptor": dict(metadata.severity_descriptor or {}),
        "target": metadata.target,
    }
    contract = {
        "schema_version": ASTROGRAPH_DATASET_SCHEMA_VERSION,
        "dataset_id": manifest.get("dataset_id") or task_spec.get("task_id"),
        "independent_run_id": metadata.independent_run_id or task_spec.get("task_id"),
        "split": metadata.split,
        "status": manifest.get("status"),
        "pairing": {
            "pair_id": metadata.pair_id,
            "case_role": metadata.case_role,
            "fault_case_id": metadata.fault_case_id,
            "nominal_case_id": metadata.nominal_case_id,
        },
        "ground_truth": ground_truth,
        "simulation": {
            "capability_id": task_spec.get("capability_id"),
            "platform_version": (manifest.get("platform") or {}).get("platform_version"),
            "bsk_version": (manifest.get("platform") or {}).get("bsk_version"),
            "seed": (task_spec.get("simulation") or {}).get("seed"),
            "duration_s": (task_spec.get("simulation") or {}).get("duration_s"),
            "sample_s": (task_spec.get("simulation") or {}).get("sample_s"),
            "spec_hash": (manifest.get("task") or {}).get("spec_hash"),
            "backend": simulation_spec.get("backend"),
            "fidelity": fidelity,
        },
        "telemetry": {
            "file": str(telemetry_path.relative_to(root).as_posix()),
            "source_trace_file": str(trace_path.relative_to(root).as_posix()),
            "row_count": len(projected),
            "time_field": "time_s",
            "channel_contract": channel_contract,
        },
        "quality": {
            "nonempty_trace": bool(projected),
            "sat_sim_status_complete": manifest.get("status") == "complete",
            "channel_ready": channel_contract["channel_ready"],
            "model_input_ready": channel_contract["model_input_ready"],
            "formal_training_eligible": fidelity["formal_training_eligible"],
            "physics_validation_status": "PENDING_PAIR_VALIDATION",
            "diagnostic_validation_status": "PENDING_DIAGNOSTIC_VALIDATION",
            "diagnostic_validation_passed": False,
            "diagnostic_score": None,
            "basilisk_diagnostic_candidate": False,
            "formal_diagnostic_qualified": False,
            "signature_review_status": "PENDING",
            "physics_validation_passed": False,
            "formal_training_qualified": False,
            "physics_candidate_ready": False,
            "candidate_training_ready": False,
            "expert_review_status": "PENDING",
            "training_ready": False,
            "expert_review_required": True,
        },
        "claim_boundary": {
            "level": fidelity["fidelity_level"],
            "flight_validated": False,
            "high_fidelity_equivalence_claimed": False,
            "notes": [
                "Formal AstroGraph training candidates require the unified Basilisk runtime and complete runtime evidence.",
                "The five-channel profile includes signed motor current derived from native rwMotorTorqueOutMsg using the explicit actuator telemetry contract; it is not measured flight current.",
                "Derived channels are explicitly identified and must not be presented as measured flight telemetry.",
            ],
        },
        "metadata": {**dict(metadata.metadata), "canonical_target_wheel_index": _target_wheel_index(metadata)},
    }
    simulation_report = {
        "schema_version": "sat-sim.astrograph-simulation-report.v1",
        "dataset_id": contract["dataset_id"],
        "fidelity": fidelity,
        "runtime_summary": summary,
        "runtime_evidence": manifest.get("runtime_evidence", {}),
        "simulation": contract["simulation"],
        "ground_truth": ground_truth,
        "reproducibility": {
            "task_spec_file": "task_spec.json",
            "task_spec_sha256": (manifest.get("task") or {}).get("spec_hash"),
            "seed": simulation_spec.get("seed"),
        },
    }
    simulation_report_path = root / "astrograph" / "simulation_report.json"
    simulation_report_path.parent.mkdir(parents=True, exist_ok=True)
    simulation_report_path.write_text(json.dumps(simulation_report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    contract["simulation_report_file"] = str(simulation_report_path.relative_to(root).as_posix())
    experiment_record_path, _ = write_experiment_record(
        root, contract=contract, manifest=manifest, task_spec=task_spec, runtime_summary=summary
    )
    contract["experiment_record_file"] = str(experiment_record_path.relative_to(root).as_posix())

    contract_path = root / "astrograph" / "dataset_contract.json"
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return AstroGraphDatasetWriteResult(root, contract_path, telemetry_path, contract)


def build_astrograph_dataset_index(case_contracts: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_split: dict[str, int] = {}
    by_class: dict[str, int] = {}
    by_fidelity: dict[str, int] = {}
    training_ready = 0
    formal_eligible = 0
    formal_qualified = 0
    formal_diagnostic_qualified = 0
    diagnostic_candidates = 0
    candidate_ready = 0
    expert_approved = 0
    for item in case_contracts:
        split = str(item.get("split") or "unknown")
        class_id = str((item.get("ground_truth") or {}).get("class_id") or "UNKNOWN")
        fidelity = str((((item.get("simulation") or {}).get("fidelity") or {}).get("fidelity_level")) or "UNKNOWN")
        by_split[split] = by_split.get(split, 0) + 1
        by_class[class_id] = by_class.get(class_id, 0) + 1
        by_fidelity[fidelity] = by_fidelity.get(fidelity, 0) + 1
        quality = item.get("quality") or {}
        if quality.get("formal_training_eligible"):
            formal_eligible += 1
        if quality.get("formal_training_qualified"):
            formal_qualified += 1
        if quality.get("formal_diagnostic_qualified"):
            formal_diagnostic_qualified += 1
        if quality.get("basilisk_diagnostic_candidate"):
            diagnostic_candidates += 1
        if quality.get("candidate_training_ready"):
            candidate_ready += 1
        if quality.get("expert_review_status") == "APPROVED":
            expert_approved += 1
        if quality.get("training_ready"):
            training_ready += 1
    return {
        "schema_version": ASTROGRAPH_INDEX_SCHEMA_VERSION,
        "case_count": len(case_contracts),
        "formal_training_eligible_case_count": formal_eligible,
        "formal_training_qualified_case_count": formal_qualified,
        "basilisk_diagnostic_candidate_case_count": diagnostic_candidates,
        "formal_diagnostic_qualified_case_count": formal_diagnostic_qualified,
        "candidate_training_ready_case_count": candidate_ready,
        "expert_approved_case_count": expert_approved,
        "training_ready_case_count": training_ready,
        "counts_by_split": dict(sorted(by_split.items())),
        "counts_by_class": dict(sorted(by_class.items())),
        "counts_by_fidelity_level": dict(sorted(by_fidelity.items())),
        "contracts": list(case_contracts),
    }


__all__ = [
    "ASTROGRAPH_DATASET_SCHEMA_VERSION",
    "ASTROGRAPH_RW_OPTIONAL_CHANNELS",
    "ASTROGRAPH_RW_MODEL_INPUT_CHANNELS",
    "ASTROGRAPH_RW_PHYSICS_ONLY_CHANNELS",
    "ASTROGRAPH_INDEX_SCHEMA_VERSION",
    "ASTROGRAPH_RW_PROFILE_ID",
    "ASTROGRAPH_RW_REQUIRED_CHANNELS",
    "AstroGraphCaseMetadata",
    "AstroGraphDatasetWriteResult",
    "build_astrograph_dataset_index",
    "write_astrograph_case_package",
]
