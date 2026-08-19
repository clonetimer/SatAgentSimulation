"""Agent-callable adapter for the existing Basilisk whole-spacecraft graph.

The capability name uses ``digital_twin`` for product discoverability, but the
adapter deliberately reports an engineering-prototype claim boundary.  The
runtime is the existing unified ``whole_spacecraft`` Basilisk graph; this file
is an adapter, not a second model implementation.
"""
from __future__ import annotations

from dataclasses import asdict, fields
import json
from typing import Any, Mapping, Sequence

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue
from sat_sim.whole_spacecraft_effects import (
    resolve_whole_spacecraft_effects,
    supported_whole_spacecraft_effect_catalog,
)
from whole_spacecraft.runner import run_whole_spacecraft_effect_case
from whole_spacecraft.task_cadence import validate_task_periods
from whole_spacecraft.schemas import (
    WholeSpacecraftConfig,
    WholeSpacecraftCouplingConfig,
    WholeSpacecraftRunConfig,
)


_CAPABILITY_ID = "whole_spacecraft.composite_digital_twin.v1"
_TARGET_NAME = "composite_digital_twin"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(params: Mapping[str, Any], key: str, default: float) -> float:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"parameters.{key} must be a number")
    return float(value)


def _integer(params: Mapping[str, Any], key: str, default: int) -> int:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"parameters.{key} must be an integer")
    return int(value)


def _boolean(params: Mapping[str, Any], key: str, default: bool) -> bool:
    value = params.get(key, default)
    if not isinstance(value, bool):
        raise ValueError(f"parameters.{key} must be a boolean")
    return value


def _number_tuple(params: Mapping[str, Any], key: str) -> tuple[float, ...] | None:
    value = params.get(key)
    if value is None:
        return None
    if not isinstance(value, (list, tuple)) or not value:
        raise ValueError(f"parameters.{key} must contain one or more numbers")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError(f"parameters.{key} must contain only numbers")
    return tuple(float(item) for item in value)


def _vector3(params: Mapping[str, Any], key: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    value = params.get(key, default)
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise ValueError(f"parameters.{key} must contain exactly three numbers")
    if any(isinstance(item, bool) or not isinstance(item, (int, float)) for item in value):
        raise ValueError(f"parameters.{key} must contain exactly three numbers")
    return tuple(float(item) for item in value)  # type: ignore[return-value]


def _build_coupling_config(params: Mapping[str, Any]) -> WholeSpacecraftCouplingConfig:
    payload = _mapping(params.get("coupling"))
    known = {item.name for item in fields(WholeSpacecraftCouplingConfig)}
    unknown = sorted(set(payload) - known)
    if unknown:
        raise ValueError(f"parameters.coupling contains unknown field(s): {', '.join(unknown)}")
    for key, value in payload.items():
        if not isinstance(value, bool):
            raise ValueError(f"parameters.coupling.{key} must be a boolean")
    return WholeSpacecraftCouplingConfig(**payload)


def _build_configs(spec: Mapping[str, Any]) -> tuple[WholeSpacecraftRunConfig, WholeSpacecraftConfig]:
    sim = _mapping(spec.get("simulation"))
    params = _mapping(spec.get("parameters"))
    orbit = _mapping(params.get("orbit_environment"))

    duration_s = _number(sim, "duration_s", 300.0)
    sample_s = _number(sim, "sample_s", 10.0)
    structure = WholeSpacecraftConfig(
        adcs_dyn_step_s=_number(params, "adcs_dyn_step_s", 0.2),
        adcs_fsw_step_s=_number(params, "adcs_fsw_step_s", 0.2),
        mission_initial_sigma_bn=_vector3(params, "mission_initial_sigma_bn", (0.0, 0.0, 0.0)),
        mission_initial_omega_bn_b_rad_s=_vector3(params, "mission_initial_omega_bn_b_rad_s", (0.0, 0.0, 0.0)),
        initial_orbit_radius_m=_number(params, "initial_orbit_radius_m", 7_000_000.0),
        initial_orbit_phase_deg=_number(params, "initial_orbit_phase_deg", 0.0),
        battery_capacity_wh=_number(params, "battery_capacity_wh", 160.0),
        initial_soc=_number(params, "initial_soc", 0.62),
        solar_power_w=_number(params, "solar_power_w", 95.0),
        payload_power_w=_number(params, "payload_power_w", 38.0),
        bus_power_w=_number(params, "bus_power_w", 18.0),
        instrument_baud_bps=_number(params, "instrument_baud_bps", 2.5e6),
        storage_capacity_bits=_number(params, "storage_capacity_bits", 6.0e9),
        storage_initial_bits=_number(params, "storage_initial_bits", 0.0),
        transmitter_baud_bps=_number(params, "transmitter_baud_bps", 1.5e6),
        native_downlink_bit_rate_request_bps=_number(params, "native_downlink_bit_rate_request_bps", 1.5e6),
        comm_power_reference_rate_bps=_number(params, "comm_power_reference_rate_bps", 1.5e6),
        native_downlink_packet_size_bits=_number(params, "native_downlink_packet_size_bits", 1000.0),
        native_downlink_max_retransmissions=_integer(params, "native_downlink_max_retransmissions", 1),
        native_downlink_cnr_linear=_number(params, "native_downlink_cnr_linear", 1.0e9),
        native_downlink_distance_m=_number(params, "native_downlink_distance_m", 500_000.0),
        native_downlink_bandwidth_hz=_number(params, "native_downlink_bandwidth_hz", 1.0e6),
        native_downlink_frequency_hz=_number(params, "native_downlink_frequency_hz", 2.2e9),
        thermal_step_s=_number(params, "thermal_step_s", 10.0),
        thermal_heat_power_w=_number(params, "thermal_heat_power_w", 30.0),
        thermal_use_network=_boolean(params, "thermal_use_network", True),
        propulsion_enabled=_boolean(params, "propulsion_enabled", True),
        propulsion_on_time_s=_number_tuple(params, "propulsion_on_time_s"),
        orb_env_step_s=_number(orbit, "step_s", 1.0),
        orb_env_sun_model=str(orbit.get("sun_model", "fixed")),
        orb_env_sun_vector_n=_vector3(orbit, "sun_vector_n", (1.0, 0.0, 0.0)),
        orb_env_magnetic_field_model=str(orbit.get("magnetic_field_model", "dipole")),
        orb_env_use_j2_gravity=_boolean(orbit, "use_j2_gravity", True),
        orb_env_spice_data_path=str(orbit["spice_data_path"]) if orbit.get("spice_data_path") else None,
        orb_env_wmm_data_path=str(orbit["wmm_data_path"]) if orbit.get("wmm_data_path") else None,
        orb_env_strict_resource_loading=_boolean(orbit, "strict_resource_loading", False),
        orb_env_spice_epoch_utc=str(orbit.get("spice_epoch_utc", "2025 JAN 01 00:00:00.000")),
        orb_env_enable_eclipse=_boolean(orbit, "enable_eclipse", True),
        coupling=_build_coupling_config(params),
        parameter_profile=str(params.get("parameter_profile", "demo")),
        parameter_registry_path=str(params["parameter_registry_path"]) if params.get("parameter_registry_path") else None,
        parameter_minimum_confidence=str(params.get("parameter_minimum_confidence", "demo")),
        strict_parameter_provenance=_boolean(params, "strict_parameter_provenance", True),
    )
    run = WholeSpacecraftRunConfig(
        duration_s=duration_s,
        sample_s=sample_s,
        access_window_s=_number(params, "access_window_s", 20.0),
        access_period_s=_number(params, "access_period_s", 60.0),
        max_pointing_error_deg=_number(params, "max_pointing_error_deg", 0.25),
        structure=structure,
    )
    return run, structure


class WholeSpacecraftCompositeDigitalTwinAdapter:
    """Expose the unified whole-spacecraft Basilisk graph and its owned effects."""

    capability_id = _CAPABILITY_ID

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        if spec.get("capability_id") != self.capability_id:
            issues.append(ValidationIssue("error", "$.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") != "whole_spacecraft":
            issues.append(ValidationIssue("error", "$.task_type", "requires task_type='whole_spacecraft'", "capability"))
        target = _mapping(spec.get("target"))
        if target.get("level") != "whole_spacecraft" or target.get("name") != _TARGET_NAME:
            issues.append(ValidationIssue("error", "$.target", "requires target.level='whole_spacecraft' and target.name='composite_digital_twin'", "capability"))
        mode = str(target.get("mode") or "nominal")
        if mode not in {"nominal", "fault", "degradation", "mixed"}:
            issues.append(ValidationIssue("error", "$.target.mode", "supports nominal/fault/degradation/mixed only", "capability"))

        effect_resolution = resolve_whole_spacecraft_effects(spec)
        for item in effect_resolution.issues:
            issues.append(ValidationIssue(item.severity, item.path, item.message, item.code))

        try:
            run, structure = _build_configs(spec)
            positive = {
                "simulation.duration_s": run.duration_s,
                "simulation.sample_s": run.sample_s,
                "parameters.adcs_dyn_step_s": structure.adcs_dyn_step_s,
                "parameters.adcs_fsw_step_s": structure.adcs_fsw_step_s,
                "parameters.thermal_step_s": structure.thermal_step_s,
                "parameters.battery_capacity_wh": structure.battery_capacity_wh,
                "parameters.storage_capacity_bits": structure.storage_capacity_bits,
                "parameters.initial_orbit_radius_m": structure.initial_orbit_radius_m,
                "parameters.access_period_s": run.access_period_s,
            }
            for path, value in positive.items():
                if value <= 0.0:
                    issues.append(ValidationIssue("error", f"$.{path}", "must be greater than zero", "range"))
            if structure.propulsion_on_time_s is not None and any(value < 0.0 for value in structure.propulsion_on_time_s):
                issues.append(ValidationIssue("error", "$.parameters.propulsion_on_time_s", "all values must be nonnegative", "range"))
            if not 0.0 <= structure.initial_soc <= 1.0:
                issues.append(ValidationIssue("error", "$.parameters.initial_soc", "must be within [0, 1]", "range"))
            if run.sample_s > run.duration_s:
                issues.append(ValidationIssue("warning", "$.simulation.sample_s", "sample interval exceeds duration; output will contain only boundary samples", "range"))
            if run.access_window_s < 0.0 or run.access_window_s > run.access_period_s:
                issues.append(ValidationIssue("error", "$.parameters.access_window_s", "must be within [0, access_period_s]", "range"))
            period_grid = validate_task_periods(
                dynamics_step_s=structure.adcs_dyn_step_s,
                fsw_step_s=structure.adcs_fsw_step_s,
                orbit_environment_step_s=structure.orb_env_step_s,
                thermal_step_s=structure.thermal_step_s,
                recorder_step_s=run.sample_s,
            )
            for item in period_grid.get("issues", ()):
                issues.append(ValidationIssue(
                    "error",
                    f"$.simulation.task_periods.{item.get('task')}",
                    str(item.get("reason_code")),
                    "task_cadence",
                ))
        except Exception as exc:
            issues.append(ValidationIssue("error", "$.parameters", str(exc), "capability"))
        return tuple(issues)

    @staticmethod
    def _active_effects_at(effects: Sequence[Mapping[str, Any]], time_s: float) -> list[Mapping[str, Any]]:
        active: list[Mapping[str, Any]] = []
        for effect in effects:
            if effect.get("kind") == "degradation":
                active.append(effect)
                continue
            onset = float(effect.get("onset_time_s", 0.0))
            duration = float(effect.get("duration_s", -1.0))
            if time_s < onset:
                continue
            if duration == -1.0 or time_s <= onset + duration + 1e-12:
                active.append(effect)
        return active

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        errors = [item for item in self.validate(spec, capability) if item.severity == "error"]
        if errors:
            raise ValueError("; ".join(f"{item.path}: {item.message}" for item in errors))
        config, _ = _build_configs(spec)
        resolution = resolve_whole_spacecraft_effects(spec)
        summary, rows = run_whole_spacecraft_effect_case(
            config,
            fault_specs=resolution.fault_specs,
            degradation=resolution.degradation,
        )
        task_id = str(spec.get("task_id") or "whole_spacecraft_composite_task")
        metadata_in = _mapping(spec.get("metadata"))
        case_id = str(metadata_in.get("case_id") or "case_000")
        mode = resolution.effective_mode
        trace_rows: list[dict[str, Any]] = []
        active_sample_count = 0
        for row in rows:
            payload = asdict(row)
            active = self._active_effects_at(resolution.effects, float(row.time_s))
            if active:
                active_sample_count += 1
            payload.update({
                "task_id": task_id,
                "case_id": case_id,
                "mode": mode,
                "label.modifier_active": bool(active),
                "label.fault_active": any(item.get("kind") == "fault" for item in active),
                "label.degradation_active": any(item.get("kind") == "degradation" for item in active),
                "label.health_state": "degraded" if active else "nominal",
                "effect.active_count": len(active),
                "effect.active_ids": ",".join(str(item.get("effect_id")) for item in active),
            })
            trace_rows.append(payload)
        summary_payload = asdict(summary)
        fault_execution_ok = (
            not resolution.fault_specs
            or (summary.runtime_fault_event_status == "PASS" and summary.runtime_unsupported_count == 0)
        )
        summary_payload.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "target_level": "whole_spacecraft",
            "target_name": _TARGET_NAME,
            "mode": mode,
            "trace_rows": len(trace_rows),
            "mission_success_probability_claimed": False,
            "claim_level": "engineering_prototype_unified_basilisk_graph",
            "can_claim_high_fidelity": False,
            "effect_execution_owner": "composite_whole_spacecraft_adapter",
            "effect_execution_status": "PASS" if fault_execution_ok else "FAIL",
            "requested_effect_count": len(resolution.effects),
            "fault_effect_count": sum(1 for item in resolution.effects if item.get("kind") == "fault"),
            "degradation_effect_count": sum(1 for item in resolution.effects if item.get("kind") == "degradation"),
            "active_effect_sample_count": active_sample_count,
            "applied_effects": list(resolution.effects),
            "degradation_execution": (
                "build_time_subsystem_configuration" if resolution.degradation is not None else "not_applicable"
            ),
            "fault_execution": (
                "runtime_basilisk_event_subsystem_component_route" if resolution.fault_specs else "not_applicable"
            ),
        })
        metadata = {
            "capability_id": self.capability_id,
            "runtime_owner": "whole_spacecraft.runner.run_whole_spacecraft_effect_case",
            "unified_basilisk_graph": True,
            "modifiers_applied_by_adapter": True,
            "effect_catalog": supported_whole_spacecraft_effect_catalog(),
            "applied_effects": list(resolution.effects),
            "subsystems": list(summary.included_subsystems),
            "coupling_evidence": {
                "coupling_count": summary.coupling_count,
                "active_coupling_count": summary.active_coupling_count,
                "native_coupling_count": summary.native_coupling_count,
                "proxy_coupling_count": summary.proxy_coupling_count,
                "disabled_coupling_count": summary.disabled_coupling_count,
            },
            "conservation_evidence": {
                "energy_status": summary.energy_conservation_status,
                "energy_residual_j": summary.energy_balance_residual_j,
                "energy_tolerance_j": summary.energy_balance_tolerance_j,
                "data_status": summary.data_conservation_status,
                "data_residual_bits": summary.data_balance_residual_bits,
                "data_tolerance_bits": summary.data_balance_tolerance_bits,
            },
            "proxy_runtime_evidence": {
                "status": summary.proxy_coupling_runtime_status,
                "eps_to_thermal_heat_energy_j": summary.eps_to_thermal_heat_energy_j,
                "heater_electrical_energy_j": summary.heater_electrical_energy_j,
                "heater_thermal_energy_j": summary.heater_thermal_energy_j,
                "rf_environment_wiring_count": summary.rf_environment_wiring_count,
            },
            "claim_guardrail": (
                "This is an engineering-prototype composite spacecraft simulation. "
                "The mission_success_score is a deterministic gate fraction, not a calibrated probability; "
                "do not claim flight-grade, certification-grade, or flight-data-correlated digital-twin fidelity."
            ),
        }
        labels = {
            "run_labels": [{
                "task_id": task_id,
                "case_id": case_id,
                "mode": mode,
                "capability_id": self.capability_id,
                "fidelity_level": "engineering_prototype",
                "fault_effect_count": summary_payload["fault_effect_count"],
                "degradation_effect_count": summary_payload["degradation_effect_count"],
            }],
            "applied_effects": list(resolution.effects),
        }
        return SimulationResult(summary=summary_payload, trace_rows=tuple(trace_rows), labels=labels, metadata=metadata)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False, sort_keys=False)
        return f'''#!/usr/bin/env python3
"""Generated {self.capability_id} script."""
import json
from pathlib import Path

from sat_sim.adapters.whole_spacecraft_composite_digital_twin import WholeSpacecraftCompositeDigitalTwinAdapter
from sat_sim.dataset_writer import write_task_dataset
from sat_sim.task_compiler import compile_task_spec

TASK_SPEC = json.loads({payload!r})


def main() -> int:
    adapter = WholeSpacecraftCompositeDigitalTwinAdapter()
    issues = adapter.validate(TASK_SPEC)
    errors = [item for item in issues if item.severity == "error"]
    if errors:
        raise SystemExit("; ".join(f"{{item.path}}: {{item.message}}" for item in errors))
    result = adapter.run(TASK_SPEC)
    compiled = compile_task_spec(TASK_SPEC, validate=True)
    output_root = Path(TASK_SPEC.get("outputs", {{}}).get("output_root", TASK_SPEC.get("task_id", "whole_spacecraft_composite_output")))
    dataset = write_task_dataset(
        output_root=output_root,
        compiled=compiled,
        task_spec=TASK_SPEC,
        summary=result.summary,
        trace_rows=result.trace_rows,
        status="complete",
    )
    print(json.dumps({{"ok": True, "summary": result.summary, "dataset": dataset.to_dict()}}, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        if isinstance(capability, Mapping) and isinstance(capability.get("outputs"), Mapping):
            return dict(capability["outputs"])
        return {"trace": [], "summary": []}


__all__ = ["WholeSpacecraftCompositeDigitalTwinAdapter"]
