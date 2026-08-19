"""Dataset writer for TaskSpec-driven simulation runs."""
from __future__ import annotations

import csv
import json
import platform
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .task_compiler import CompiledTask, dataclass_to_dict
from .task_spec import DATASET_MANIFEST_VERSION, canonical_json, spec_sha256, write_json
from .outputs.standard_fields import FIELD_UNITS as _STANDARD_FIELD_UNITS
from .multi_rate_telemetry import write_multi_rate_telemetry, write_native_multi_rate_telemetry
from .fmea import write_fmea_table
from .fault_traceability import build_fault_traceability, write_fault_traceability


TRACE_UNITS: dict[str, str] = {
    "time_s": "s",
    "battery_storage_j": "J",
    "battery_soc": "1",
    "net_power_w": "W",
    "battery_temp_c": "degC",
    "shunt_dissipated_w": "W",
    "data_storage_bits": "bit",
    "instrument_baud_bps": "bit/s",
    "transmitter_baud_bps": "bit/s",
    "thermal_temp_c": "degC",
    "thermal_margin_c": "degC",
    "thermal_safe": "bool",
    "attitude_error_norm": "1",
    "rate_error_norm_rad_s": "rad/s",
    "payload_enabled": "bool",
    "payload_generated_bps": "bit/s",
    "downlink_rate_bps": "bit/s",
    "propellant_remaining_kg": "kg",
    "tank_pressure_pa": "Pa",
    "r_x_m": "m",
    "r_y_m": "m",
    "r_z_m": "m",
    "v_x_m_s": "m/s",
    "v_y_m_s": "m/s",
    "v_z_m_s": "m/s",
    "orbit_radius_m": "m",
    "shadow_factor": "1",
    "mag_norm_t": "T",
    "ground_range_m": "m",
    "ground_elevation_deg": "deg",
    "ground_has_access": "bool",
    "task_id": "none",
    "case_id": "none",
    "sample_index": "count",
    "target_level": "none",
    "target_name": "none",
    "mode": "none",
    "eps.battery.soc": "ratio",
    "eps.battery.storage_wh": "Wh",
    "eps.battery.capacity_wh": "Wh",
    "eps.battery.effective_capacity_wh": "Wh",
    "eps.battery.net_power_w": "W",
    "eps.battery.applied_power_w": "W",
    "eps.battery.shunt_dissipated_wh": "Wh",
    "label.health_state": "enum",
    "label.fault_active": "bool",
    "adcs.reaction_wheel.num_wheels": "count",
    "adcs.reaction_wheel.speed_rad_s_0": "rad/s",
    "adcs.reaction_wheel.command_torque_nm_0": "N*m",
    "adcs.reaction_wheel.applied_torque_nm_0": "N*m",
    "adcs.reaction_wheel.effective_max_torque_nm_0": "N*m",
    "adcs.reaction_wheel.effective_max_speed_rad_s_0": "rad/s",
    "adcs.reaction_wheel.damping_nms_0": "N*m*s",
    "adcs.reaction_wheel.max_abs_speed_rad_s": "rad/s",
    "adcs.reaction_wheel.momentum_norm_nms": "N*m*s",
    "adcs.reaction_wheel.rotational_energy_j": "J",
    "adcs.attitude.q_bn_0": "unit",
    "adcs.attitude.q_bn_1": "unit",
    "adcs.attitude.q_bn_2": "unit",
    "adcs.attitude.q_bn_3": "unit",
    "adcs.attitude.quaternion_norm": "ratio",
    "adcs.attitude.quaternion_norm_error": "ratio",
    "adcs.pointing.error_deg": "deg",
    "adcs.pointing.error_vector_rad_0": "rad",
    "adcs.pointing.error_vector_rad_1": "rad",
    "adcs.pointing.error_vector_rad_2": "rad",
    "adcs.rate.omega_bn_b_rad_s_0": "rad/s",
    "adcs.rate.omega_bn_b_rad_s_1": "rad/s",
    "adcs.rate.omega_bn_b_rad_s_2": "rad/s",
    "adcs.sensor.gyro_measured_rad_s_0": "rad/s",
    "adcs.sensor.gyro_measured_rad_s_1": "rad/s",
    "adcs.sensor.gyro_measured_rad_s_2": "rad/s",
    "adcs.control.command_torque_nm_0": "N*m",
    "adcs.control.command_torque_nm_1": "N*m",
    "adcs.control.command_torque_nm_2": "N*m",
    "adcs.control.applied_torque_nm_0": "N*m",
    "adcs.control.applied_torque_nm_1": "N*m",
    "adcs.control.applied_torque_nm_2": "N*m",
    "adcs.control.torque_saturation_flag": "bool",
    "adcs.rw.speed_rad_s_0": "rad/s",
    "adcs.rw.speed_rad_s_1": "rad/s",
    "adcs.rw.speed_rad_s_2": "rad/s",
    "adcs.rw.max_abs_speed_rad_s": "rad/s",
    "adcs.rw.speed_saturation_flag": "bool",
    "adcs.rw.saturation_flag": "bool",
    "adcs.power.rw_power_w": "W",
    "adcs.model": "enum",
    "adcs.target.mode": "enum",
    "adcs.control.controller_mode": "enum",
    "adcs.sensor.mode": "enum",
    "frame.attitude_ref": "enum",
    "frame.body": "enum",
    "orbit.r_bn_n_m_x": "m",
    "orbit.r_bn_n_m_y": "m",
    "orbit.r_bn_n_m_z": "m",
    "orbit.v_bn_n_m_s_x": "m/s",
    "orbit.v_bn_n_m_s_y": "m/s",
    "orbit.v_bn_n_m_s_z": "m/s",
    "orbit.radius_m": "m",
    "environment.sun_vector_n_x": "unit",
    "environment.sun_vector_n_y": "unit",
    "environment.sun_vector_n_z": "unit",
    "environment.shadow_factor": "ratio",
    "environment.eclipse_flag": "bool",
    "environment.magnetic_field_n_t_x": "T",
    "environment.magnetic_field_n_t_y": "T",
    "environment.magnetic_field_n_t_z": "T",
    "environment.magnetic_field_norm_t": "T",
    "ground.range_m": "m",
    "ground.elevation_deg": "deg",
    "ground.has_access": "bool",
    "utc": "UTC",
    "capability_id": "none",
    "fidelity_level": "enum",
    "frame.orbit_state": "enum",
    "orbit.model": "enum",
    "orbit.altitude_m": "m",
    "orbit.speed_m_s": "m/s",
    "orbit.raan_deg": "deg",
    "orbit.arg_perigee_deg": "deg",
    "orbit.mean_anomaly_deg": "deg",
    "environment.sun_vector_frame": "enum",
    "environment.umbra_flag": "bool",

    "coupled.energy.solar_to_eps_w": "W",
    "coupled.energy.eps_to_thermal_heat_w": "W",
    "coupled.energy.heater_feedback_w": "W",
    "coupled.derating.battery_capacity_ratio": "ratio",
    "payload.enabled": "bool",
    "payload.generated_bps": "bit/s",
    "payload.generated_bits_step": "bit",
    "payload.power_w": "W",
    "comm.transmitter.active": "bool",
    "comm.downlink_rate_bps": "bit/s",
    "comm.downlinked_bits_step": "bit",
    "comm.power_w": "W",
    "storage.stored_bits": "bit",
    "storage.next_stored_bits": "bit",
    "storage.capacity_bits": "bit",
    "storage.fill_fraction": "ratio",
    "storage.overflow_bits_step": "bit",
    "coupled.data.generated_bits_total_if_applied": "bit",
    "coupled.data.downlinked_bits_total_if_applied": "bit",
    "coupled.data.conservation_error_bits": "bit",
    "label.access_active": "bool",
    "label.downlink_active": "bool",
    "label.payload_enabled": "bool",
    "label.storage_full": "bool",
    "label.power_constrained": "bool",
    "environment.penumbra_flag": "bool",
    "orbit.specific_energy_j_kg": "J/kg",
    "orbit.force.central_gravity_norm_m_s2": "m/s^2",
    "orbit.force.j2_norm_m_s2": "m/s^2",
    "orbit.force.drag_norm_m_s2": "m/s^2",
    "orbit.force.srp_norm_m_s2": "m/s^2",
    "orbit.force.total_norm_m_s2": "m/s^2",
}
TRACE_UNITS.update(_STANDARD_FIELD_UNITS)
TRACE_UNITS.update({
    "propulsion.burn.active": "bool",
    "propulsion.thrust_n": "N",
    "propulsion.specific_impulse_s": "s",
    "propulsion.propellant.remaining_kg": "kg",
    "propulsion.propellant.used_step_kg": "kg",
    "propulsion.delta_v.step_m_s": "m/s",
    "propulsion.delta_v.cumulative_m_s": "m/s",
    "propulsion.mass.total_kg": "kg",
    "propulsion.burn.direction_x": "unit",
    "propulsion.burn.direction_y": "unit",
    "propulsion.burn.direction_z": "unit",
    "orbit.v_burn_adjusted_m_s_x": "m/s",
    "orbit.v_burn_adjusted_m_s_y": "m/s",
    "orbit.v_burn_adjusted_m_s_z": "m/s",
    "orbit.speed_adjusted_m_s": "m/s",
    "orbit.speed_delta_proxy_m_s": "m/s",
    "adcs.pointing_error_deg": "deg",
    "adcs.angular_rate_disturbance_rad_s": "rad/s",
    "adcs.disturbance_torque_proxy_n_m": "N*m",
    "label.burn_active": "bool",
    "label.attitude_control_available": "bool",
})


@dataclass(frozen=True)
class DatasetWriteResult:
    """Files produced by a dataset write."""

    output_root: Path
    files: dict[str, str] = field(default_factory=dict)
    manifest: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"output_root": str(self.output_root), "files": self.files, "manifest": self.manifest}


def _rows_to_dicts(rows: Iterable[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dataclass_to_dict(row)
        if not isinstance(item, Mapping):
            raise TypeError(f"trace row must be dataclass or mapping, got {type(row).__name__}")
        out.append(dict(item))
    return out


def _filter_rows(rows: Sequence[dict[str, Any]], fields: Sequence[str] | None) -> list[dict[str, Any]]:
    if not fields:
        return list(rows)
    wanted = list(fields)
    filtered: list[dict[str, Any]] = []
    for row in rows:
        filtered.append({key: row.get(key) for key in wanted if key in row})
    return filtered


def write_trace_csv(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Write trace rows to CSV."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return path
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(str(key))
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return path


def write_jsonl(path: str | Path, rows: Sequence[Mapping[str, Any]]) -> Path:
    """Write trace rows to JSON Lines."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(dict(row), ensure_ascii=False, sort_keys=False) + "\n")
    return path


def _trace_schema(rows: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    if not rows:
        return []
    keys: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in keys:
                keys.append(str(key))
    schema = []
    for key in keys:
        value = next((row.get(key) for row in rows if key in row and row.get(key) is not None), None)
        dtype = "bool" if isinstance(value, bool) else "number" if isinstance(value, (int, float)) else "string"
        schema.append({"name": key, "unit": TRACE_UNITS.get(key, "unknown"), "dtype": dtype})
    return schema


def _bsk_version() -> str:
    try:
        import importlib.metadata as metadata
        return metadata.version("bsk")
    except Exception:
        return "unavailable"


def build_dataset_manifest(
    *,
    compiled: CompiledTask,
    task_spec: Mapping[str, Any],
    files: Mapping[str, str],
    trace_rows: Sequence[Mapping[str, Any]],
    status: str,
    summary: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a DatasetManifest payload."""

    seed = compiled.simulation.get("seed")
    run = {
        "duration_s": compiled.simulation.get("duration_s"),
        "sample_s": compiled.simulation.get("sample_s"),
        "scenario_count": 1,
        "success_count": 1 if status == "complete" else 0,
        "failure_count": 0 if status == "complete" else 1,
    }
    task_payload = {
        "task_id": compiled.task_id,
        "task_type": compiled.task_type,
        "schema_version": compiled.schema_version,
        "spec_hash": spec_sha256(task_spec),
        "seed": seed if isinstance(seed, int) else None,
    }
    metadata = compiled.metadata if isinstance(compiled.metadata, Mapping) else {}
    capability_id = metadata.get("capability_id")
    if capability_id:
        task_payload["capability_id"] = capability_id

    manifest = {
        "manifest_version": DATASET_MANIFEST_VERSION,
        "dataset_id": compiled.task_id,
        "created_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "platform": {
            "platform_version": "0.1.0",
            "bsk_version": _bsk_version(),
            "python_version": platform.python_version(),
        },
        "task": task_payload,
        "run": run,
        "files": dict(files),
        "trace_schema": _trace_schema(trace_rows),
        "labels": compiled.labels,
        "quality": {
            "trace_rows": len(trace_rows),
            "summary_status": (summary or {}).get("status"),
            "spec_canonical_sha256": spec_sha256(task_spec),
        },
        "status": status,
    }
    if capability_id:
        manifest["capability"] = {
            "capability_id": capability_id,
            "trust_level": metadata.get("capability_trust_level"),
            "adapter": metadata.get("adapter"),
            "contract": metadata.get("capability_contract"),
            "source_binding": metadata.get("source_binding"),
        }
    if metadata.get("composition"):
        manifest["composition"] = metadata.get("composition")
    if metadata.get("repair_log"):
        manifest["repair_log"] = metadata.get("repair_log")
    if metadata.get("normalized_defaults"):
        manifest["normalized_defaults"] = metadata.get("normalized_defaults")
    if metadata.get("pruned_fields"):
        manifest["pruned_fields"] = metadata.get("pruned_fields")
    if metadata.get("taskspec_pruning"):
        manifest["taskspec_pruning"] = metadata.get("taskspec_pruning")
    if metadata.get("modifiers"):
        manifest["modifiers"] = metadata.get("modifiers")
    if metadata.get("applied_modifiers"):
        manifest["applied_modifiers"] = metadata.get("applied_modifiers")
    if metadata.get("hf_foundation"):
        manifest["hf_foundation"] = metadata.get("hf_foundation")
    if metadata.get("hf3_orbit_environment"):
        manifest["hf3_orbit_environment"] = metadata.get("hf3_orbit_environment")
    if metadata.get("orb1_orbit_fidelity"):
        manifest["orb1_orbit_fidelity"] = metadata.get("orb1_orbit_fidelity")
    if metadata.get("orb1_force_models"):
        manifest["orb1_force_models"] = metadata.get("orb1_force_models")
    if metadata.get("int1_orbit_adcs_integration"):
        manifest["int1_orbit_adcs_integration"] = metadata.get("int1_orbit_adcs_integration")
    if metadata.get("hf4_adcs_closed_loop"):
        manifest["hf4_adcs_closed_loop"] = metadata.get("hf4_adcs_closed_loop")
    if metadata.get("adcs1_fidelity"):
        manifest["adcs1_fidelity"] = metadata.get("adcs1_fidelity")

    if metadata.get("hf5_power_thermal_orbit"):
        manifest["hf5_power_thermal_orbit"] = metadata.get("hf5_power_thermal_orbit")
    if metadata.get("hf6_comm_payload_mission"):
        manifest["hf6_comm_payload_mission"] = metadata.get("hf6_comm_payload_mission")
    if metadata.get("hf7_propulsion_orbit_attitude"):
        manifest["hf7_propulsion_orbit_attitude"] = metadata.get("hf7_propulsion_orbit_attitude")
    if metadata.get("hf8_physical_validation"):
        manifest["hf8_physical_validation"] = metadata.get("hf8_physical_validation")
    if metadata.get("physical_validation"):
        manifest["physical_validation"] = metadata.get("physical_validation")
    if isinstance(summary, Mapping) and isinstance(summary.get("physical_validation"), Mapping):
        manifest["physical_validation"] = summary.get("physical_validation")
    return manifest


def write_labels(output_root: str | Path, compiled: CompiledTask) -> dict[str, str]:
    """Write simple run/fault labels for P1 datasets."""

    output_root = Path(output_root)
    labels_dir = output_root / "labels"
    labels_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, str] = {}

    run_label_path = labels_dir / "run_labels.csv"
    with run_label_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["task_id", "mode", "task_type", "backend"])
        writer.writeheader()
        writer.writerow({"task_id": compiled.task_id, "mode": compiled.mode, "task_type": compiled.task_type, "backend": compiled.backend})
    files["run_labels"] = str(run_label_path.relative_to(output_root))

    if compiled.faults:
        fault_path = labels_dir / "fault_labels.csv"
        fieldnames = [
            "event_id", "fault_id", "task_id", "case_id", "target", "target_type",
            "event_type", "fault_type", "degradation_type", "onset_time_s",
            "end_time_s", "duration_s", "magnitude", "label",
        ]
        case_id = None
        if isinstance(compiled.metadata, Mapping):
            case_id = compiled.metadata.get("case_id")
        case_id = case_id or "case_000"
        with fault_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for idx, fault in enumerate(compiled.faults):
                onset = fault.get("onset_time_s")
                duration = fault.get("duration_s")
                end_time_s = None
                if isinstance(onset, (int, float)) and isinstance(duration, (int, float)) and duration != -1:
                    end_time_s = onset + duration
                row = {
                    "event_id": fault.get("event_id") or fault.get("fault_id") or f"fault_{idx}",
                    "fault_id": fault.get("fault_id"),
                    "task_id": compiled.task_id,
                    "case_id": case_id,
                    "target": fault.get("target") or fault.get("target_id"),
                    "target_type": fault.get("target_type"),
                    "event_type": "fault",
                    "fault_type": fault.get("fault_type"),
                    "degradation_type": None,
                    "onset_time_s": onset,
                    "end_time_s": end_time_s,
                    "duration_s": duration,
                    "magnitude": fault.get("magnitude"),
                    "label": fault.get("label"),
                }
                writer.writerow(row)
        files["fault_labels"] = str(fault_path.relative_to(output_root))

    return files


def write_task_dataset(
    *,
    output_root: str | Path,
    compiled: CompiledTask,
    task_spec: Mapping[str, Any],
    summary: Mapping[str, Any] | Any,
    trace_rows: Iterable[Any],
    status: str = "complete",
    runtime_metadata: Mapping[str, Any] | None = None,
    validation_outcome: Mapping[str, Any] | None = None,
    run_id: str | None = None,
) -> DatasetWriteResult:
    """Write summary, trace, labels, task copy, compiled plan, and manifest."""

    output_root = Path(output_root)
    output_root.mkdir(parents=True, exist_ok=True)
    summary_dict = dataclass_to_dict(summary)
    if not isinstance(summary_dict, Mapping):
        summary_dict = {"value": summary_dict}
    rows = _rows_to_dicts(trace_rows)
    rows = _filter_rows(rows, compiled.outputs.get("record_fields"))

    files: dict[str, str] = {}
    task_spec_path = output_root / "task_spec.json"
    write_json(task_spec_path, task_spec)
    files["task_spec"] = str(task_spec_path.relative_to(output_root))

    compiled_path = output_root / "compiled_task.json"
    write_json(compiled_path, compiled.to_dict())
    files["compiled_task"] = str(compiled_path.relative_to(output_root))

    if compiled.outputs.get("include_summary", True):
        summary_path = output_root / "summary.json"
        write_json(summary_path, summary_dict)
        files["summary"] = str(summary_path.relative_to(output_root))

    if compiled.outputs.get("include_trace", True):
        fmt = compiled.outputs.get("trace_format", "csv")
        if fmt == "csv":
            trace_path = output_root / "trace.csv"
            write_trace_csv(trace_path, rows)
        elif fmt == "jsonl":
            trace_path = output_root / "trace.jsonl"
            write_jsonl(trace_path, rows)
        elif fmt == "parquet":
            # Optional path: use pandas if the caller has installed parquet support.
            try:
                import pandas as pd  # type: ignore
                trace_path = output_root / "trace.parquet"
                pd.DataFrame(rows).to_parquet(trace_path, index=False)
            except Exception as exc:  # pragma: no cover - optional dependency path
                raise RuntimeError(f"parquet trace output requires pandas + parquet engine: {exc}") from exc
        else:
            raise ValueError(f"unsupported trace_format: {fmt}")
        files["trace"] = str(trace_path.relative_to(output_root))

    telemetry_streams = compiled.outputs.get("telemetry_streams") or []
    multi_rate: dict[str, Any] | None = None
    if telemetry_streams:
        native_payload = (runtime_metadata or {}).get("native_multi_rate_telemetry") if isinstance(runtime_metadata, Mapping) else None
        if isinstance(native_payload, Mapping) and native_payload.get("streams"):
            multi_rate = write_native_multi_rate_telemetry(
                output_root=output_root,
                native_payload=native_payload,
                streams=telemetry_streams,
            )
        else:
            multi_rate = write_multi_rate_telemetry(
                output_root=output_root,
                rows=rows,
                base_sample_s=float(compiled.simulation.get("sample_s") or 0.0),
                streams=telemetry_streams,
            )
        files["multi_rate_telemetry_manifest"] = str(multi_rate["manifest_file"])
        for stream in multi_rate["streams"]:
            files[f"telemetry_stream:{stream['stream_id']}"] = str(stream["file"])

    fmea_cfg = compiled.outputs.get("fmea") or {}
    fault_environment = (
        runtime_metadata.get("fault_environment")
        if isinstance(runtime_metadata, Mapping) and isinstance(runtime_metadata.get("fault_environment"), Mapping)
        else None
    )
    traceability: dict[str, Any] | None = None
    has_events = any(
        isinstance(container, Mapping) and any(container.get(key) for key in ("faults", "degradations", "constraints"))
        for container in (task_spec.get("events"), task_spec.get("modifiers"))
    )
    if has_events:
        traceability = build_fault_traceability(
            task_spec=task_spec,
            dataset_root=output_root,
            fault_environment=fault_environment,
            multi_rate_manifest=multi_rate,
            validation_outcome=validation_outcome,
            run_id=str(run_id or compiled.task_id),
        )
        traceability_files = write_fault_traceability(
            output_root=output_root / "traceability",
            payload=traceability,
            fault_environment=fault_environment,
        )
        for key, rel in traceability_files.items():
            files[f"traceability:{key}"] = str((Path("traceability") / rel).as_posix())

    if isinstance(fmea_cfg, Mapping) and fmea_cfg.get("enabled"):
        fmea_manifest = write_fmea_table(
            output_root=output_root / "fmea",
            task_spec=task_spec,
            formats=fmea_cfg.get("formats") or ["csv", "json"],
            traceability=traceability,
        )
        files["fmea_manifest"] = str((Path("fmea") / fmea_manifest["manifest_file"]).as_posix())
        for fmt, rel in fmea_manifest["files"].items():
            files[f"fmea:{fmt}"] = str((Path("fmea") / rel).as_posix())

    if compiled.outputs.get("include_labels", True):
        files.update(write_labels(output_root, compiled))

    manifest = build_dataset_manifest(
        compiled=compiled,
        task_spec=task_spec,
        files=files,
        trace_rows=rows,
        status=status,
        summary=summary_dict,
    )
    if isinstance(runtime_metadata, Mapping):
        execution_traceability: dict[str, Any] = {}
        for key in ("unified_execution", "model_asset_execution"):
            value = runtime_metadata.get(key)
            if isinstance(value, Mapping):
                execution_traceability[key] = dataclass_to_dict(value)
        if execution_traceability:
            manifest["execution_traceability"] = execution_traceability

        runtime_evidence: dict[str, Any] = {}
        for key in (
            "runtime_manifest",
            "model_source_boundary",
            "runtime_status_semantics",
            "fault_environment",
            "native_multi_rate_telemetry",
        ):
            value = runtime_metadata.get(key)
            if value is not None:
                runtime_evidence[key] = dataclass_to_dict(value)
        if runtime_evidence:
            manifest["runtime_evidence"] = runtime_evidence
    if compiled.outputs.get("include_manifest", True):
        manifest_path = output_root / "manifest.json"
        write_json(manifest_path, manifest)
        files["manifest"] = str(manifest_path.relative_to(output_root))
        # Keep manifest self-consistent after adding itself to files.
        manifest["files"] = dict(files)
        write_json(manifest_path, manifest)

    return DatasetWriteResult(output_root=output_root, files=files, manifest=manifest)


__all__ = [
    "TRACE_UNITS",
    "DatasetWriteResult",
    "write_trace_csv",
    "write_jsonl",
    "build_dataset_manifest",
    "write_labels",
    "write_task_dataset",
]
