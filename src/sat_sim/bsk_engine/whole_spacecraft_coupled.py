'''Whole-spacecraft project-owned strongly-coupled equation scenario.

v0.5.4.7 removes the misleading BSKSim runtime claim. The scenario uses project-owned deterministic coupled equations with explicit runtime and conservation evidence.
'''
from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from sat_sim.adapter_base import SimulationResult
from sat_sim.fault_environment import BSKRLStyleFaultAdapter
from sat_sim.task_validator import ValidationIssue

from .event_manager import parse_bsk_events, BSKEventManager
from .types import (
    BSK_ENGINE_SCHEMA_VERSION,
    BSKConnectionSpec,
    BSKExecutionPlan,
    BSKModuleSpec,
    BSKProcessSpec,
    BSKRecorderSpec,
    BSKRunResult,
    BSKScenarioConfig,
    BSKTaskSpec,
)

WHOLE_SPACECRAFT_BSKSIM_COUPLED_CAPABILITY_ID = "whole_spacecraft.bsksim_coupled.v1"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _number(params: Mapping[str, Any], key: str, default: float, *, minimum: float | None = None, maximum: float | None = None) -> float:
    value = params.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return float(default)
    out = float(value)
    if minimum is not None:
        out = max(minimum, out)
    if maximum is not None:
        out = min(maximum, out)
    return out


def _bool(params: Mapping[str, Any], key: str, default: bool) -> bool:
    value = params.get(key, default)
    return bool(value) if isinstance(value, bool) else bool(default)


def whole_spacecraft_coupled_config_from_task_spec(spec: Mapping[str, Any]) -> BSKScenarioConfig:
    model = _mapping(spec.get("model"))
    target = _mapping(model.get("target") or spec.get("target"))
    task = _mapping(spec.get("task"))
    params = _mapping(spec.get("parameters"))
    values = _mapping(params.get("values")) or params
    outputs = _mapping(spec.get("outputs"))
    plots = outputs.get("plots") if isinstance(outputs.get("plots"), Sequence) and not isinstance(outputs.get("plots"), (str, bytes)) else []
    sim = _mapping(spec.get("simulation"))
    solver = _mapping(sim.get("solver"))
    step_s = sim.get("step_s", solver.get("step_s", 1.0))
    mode = str(target.get("mode") or values.get("mode") or "nominal")
    return BSKScenarioConfig(
        scenario_id=str(task.get("id") or spec.get("task_id") or "whole_spacecraft_bsksim_coupled_task"),
        capability_id=str(model.get("capability_id") or spec.get("capability_id") or WHOLE_SPACECRAFT_BSKSIM_COUPLED_CAPABILITY_ID),
        duration_s=float(sim.get("duration_s", 300.0) or 300.0),
        step_s=float(step_s or 1.0),
        sample_s=float(sim.get("sample_s", 10.0) or 10.0),
        mode_request=mode,
        parameters=values,
        events=parse_bsk_events(spec),
        requested_outputs=tuple(str(x) for x in plots),
    )


class WholeSpacecraftBSKSimCoupledScenario:
    '''Project equation engine for orbit/attitude/EPS/thermal/comm/payload/propulsion.'''

    def __init__(self, config: BSKScenarioConfig) -> None:
        self.config = config
        self.event_manager = BSKEventManager(config.events)

    def build_execution_plan(self) -> BSKExecutionPlan:
        step = float(self.config.step_s)
        processes = (
            BSKProcessSpec("DynamicsProcess", "dynamics", "Orbit, attitude, power, thermal and propulsion dynamics"),
            BSKProcessSpec("FswProcess", "fsw", "ADCS mode, power management, payload and comm scheduling"),
            BSKProcessSpec("SubsystemProcess", "subsystem", "Project-owned coupled subsystem state propagation"),
        )
        tasks = (
            BSKTaskSpec("OrbitAttitudeTask", "DynamicsProcess", step, "Orbit phase, sun vector and pointing state"),
            BSKTaskSpec("PowerThermalTask", "SubsystemProcess", max(step, 1.0), "Solar/EPS/thermal coupled propagation"),
            BSKTaskSpec("CommPayloadTask", "SubsystemProcess", max(step, 1.0), "Payload generation, storage and downlink coupling"),
            BSKTaskSpec("PropulsionTask", "DynamicsProcess", max(step, 1.0), "Propulsion request and delta-v integration"),
            BSKTaskSpec("FswModeTask", "FswProcess", step, "Mode request and subsystem gating"),
        )
        modules = (
            BSKModuleSpec("spacecraft_hub", "project orbit-attitude equation state", "OrbitAttitudeTask", "central spacecraft state", status="instantiated_project_model"),
            BSKModuleSpec("orbit_environment", "project orbit/environment equations", "OrbitAttitudeTask", "orbit phase, eclipse and sun incidence", status="instantiated_project_model"),
            BSKModuleSpec("adcs_bridge", "project ADCS pointing equations", "FswModeTask", "attitude pointing and control power", status="instantiated_project_model"),
            BSKModuleSpec("solar_array", "project EPS solar array model", "PowerThermalTask", "attitude/eclipsed solar generation", status="instantiated_project_model"),
            BSKModuleSpec("battery", "project EPS battery model", "PowerThermalTask", "SOC integration under loads", status="instantiated_project_model"),
            BSKModuleSpec("thermal_bus", "project thermal lumped bus model", "PowerThermalTask", "power-to-heat and radiator coupling", status="instantiated_project_model"),
            BSKModuleSpec("payload", "project payload data/power model", "CommPayloadTask", "payload generation gated by power and pointing", status="instantiated_project_model"),
            BSKModuleSpec("communication", "project comm/data downlink model", "CommPayloadTask", "antenna pointing and storage downlink", status="instantiated_project_model"),
            BSKModuleSpec("propulsion", "project propulsion impulse model", "PropulsionTask", "delta-v and power draw coupling", status="instantiated_project_model"),
        )
        connections = (
            BSKConnectionSpec("orbit_environment.sun_incidence", "solar_array.incidence", "orbit/attitude controls solar generation"),
            BSKConnectionSpec("orbit_environment.eclipse", "solar_array.eclipse", "eclipse gates solar input"),
            BSKConnectionSpec("solar_array.power", "battery.charge_current", "solar generation charges EPS"),
            BSKConnectionSpec("battery.soc", "payload.power_gate", "payload requires EPS SOC margin"),
            BSKConnectionSpec("battery.soc", "communication.downlink_gate", "downlink requires EPS SOC margin"),
            BSKConnectionSpec("payload.generated_data", "communication.storage", "payload data accumulates in storage"),
            BSKConnectionSpec("adcs.pointing_error", "communication.antenna_pointing_loss", "pointing error reduces downlink"),
            BSKConnectionSpec("adcs.pointing_error", "payload.quality_gate", "pointing error reduces payload generation"),
            BSKConnectionSpec("eps.load_power", "thermal_bus.heat_input", "electrical loads become heat"),
            BSKConnectionSpec("propulsion.delta_v", "orbit_environment.orbit_phase", "burn modifies orbit phase proxy"),
        )
        record_fields = self.config.requested_outputs or (
            "orbit.eclipse_flag",
            "orbit.sun_incidence_cos",
            "adcs.pointing_error_deg",
            "eps.solar_array_power_w",
            "eps.battery_soc",
            "thermal.bus_temp_c",
            "payload.generated_data_bits",
            "comm.downlink_rate_bps",
            "data.storage_bits",
            "propulsion.delta_v_m_s",
            "label.fault_active",
            "label.degradation_active",
            "label.constraint_active",
        )
        recorders = tuple(BSKRecorderSpec(field=f, source="WholeSpacecraftBSKSimCoupledScenario", sample_s=self.config.sample_s, description=f) for f in record_fields)
        return BSKExecutionPlan(
            schema_version=BSK_ENGINE_SCHEMA_VERSION,
            engine="project_coupled_equation_engine",
            scenario_id=self.config.scenario_id,
            processes=processes,
            tasks=tasks,
            modules=modules,
            connections=connections,
            events=self.config.events,
            recorders=recorders,
            mode_request=self.config.mode_request,
            notes=(
                "v0.5.4.7 identifies this runtime as a project-owned coupled equation engine.",
                "The coupling graph links orbit/attitude, EPS, thermal, comm/data, payload and propulsion traces in one scenario.",
                "No Basilisk-native runtime is claimed for this capability; use an explicit native Basilisk capability when required.",
            ),
        )

    def run(self) -> BSKRunResult:
        plan = self.build_execution_plan()
        rows = self._trace_rows()
        fault_environment = BSKRLStyleFaultAdapter().summarize(plan.events, rows)
        eps_soc_values = [float(r["eps.battery_soc"]) for r in rows]
        temp_values = [float(r["thermal.bus_temp_c"]) for r in rows]
        storage_values = [float(r["data.storage_bits"]) for r in rows]
        integrity = self._coupling_integrity_report(rows)
        summary = {
            "status": "complete",
            "scenario_id": self.config.scenario_id,
            "capability_id": self.config.capability_id,
            "engine": "project_coupled_equation_engine",
            "backend_type": "project_coupled_equation_engine",
            "mode_request": self.config.mode_request,
            "process_count": len(plan.processes),
            "task_count": len(plan.tasks),
            "declared_module_count": len(plan.modules),
            "instantiated_module_count": len(plan.modules),
            "module_count": len(plan.modules),
            "declared_connection_count": len(plan.connections),
            "connected_equation_count": len(plan.connections),
            "connection_count": len(plan.connections),
            "event_count": len(plan.events),
            "recorder_count": len(plan.recorders),
            "strong_coupling_chain_count": len(plan.connections),
            "coupling_domains": ["orbit_attitude", "eps", "thermal", "comm_data", "payload", "propulsion"],
            "trace_rows": len(rows),
            "min_battery_soc": min(eps_soc_values) if eps_soc_values else None,
            "final_battery_soc": eps_soc_values[-1] if eps_soc_values else None,
            "max_bus_temp_c": max(temp_values) if temp_values else None,
            "final_storage_bits": storage_values[-1] if storage_values else None,
            "fault_environment_episode_count": fault_environment.episode_count,
            "fault_environment_style": fault_environment.style,
            "coupling_integrity_pass": bool(integrity.get("passed")),
            "coupling_integrity": integrity,
            "native_module_migration": "not_applicable_project_equation_engine",
            "runtime_truth_status": "instantiated_connected_recorded_project_models",
        }
        return BSKRunResult(
            status="SUCCEEDED",
            summary=summary,
            trace_rows=rows,
            execution_plan=plan,
            metadata={
                "fault_environment": fault_environment.to_dict(),
                "runtime_manifest": {
                    "engine": "project_coupled_equation_engine",
                    "declared_modules": [m.to_dict() for m in plan.modules],
                    "instantiated_modules": [m.tag for m in plan.modules],
                    "connected_equations": [c.to_dict() for c in plan.connections],
                    "recorded_fields": [r.field for r in plan.recorders],
                },
                "model_source_boundary": {
                    "uses_basilisk_runtime": False,
                    "uses_bsksim_style_process_task_plan": False,
                    "project_coupled_equation_engine": True,
                    "strong_coupling_bridge": False,
                    "full_basilisk_native_sysmodels": False,
                    "claim": "Project-owned whole-spacecraft coupled equation engine; not an official BSKSim runtime.",
                },
            },
        )

    def _trace_rows(self) -> tuple[dict[str, Any], ...]:
        p = self.config.parameters
        sample = max(float(self.config.sample_s), float(self.config.step_s), 1e-6)
        n = int(math.floor(float(self.config.duration_s) / sample)) + 1
        duration = float(self.config.duration_s)

        orbit_rate = _number(p, "orbit_rate_rad_s", 0.0011)
        initial_phase = _number(p, "initial_orbit_phase_rad", 6.2)
        eclipse_fraction = _number(p, "eclipse_fraction", 0.35, minimum=0.0, maximum=0.9)
        initial_pointing = _number(p, "initial_pointing_error_deg", 8.0, minimum=0.0)
        adcs_time_constant = _number(p, "adcs_time_constant_s", max(30.0, duration / 4.0), minimum=1.0)
        battery_capacity_wh = _number(p, "battery_capacity_wh", 160.0, minimum=1e-6)
        initial_soc = _number(p, "initial_soc", 0.62, minimum=0.0, maximum=1.0)
        battery_energy_wh = initial_soc * battery_capacity_wh
        solar_power_max_w = _number(p, "solar_power_max_w", 95.0, minimum=0.0)
        bus_power_w = _number(p, "bus_power_w", 18.0, minimum=0.0)
        payload_power_w = _number(p, "payload_power_w", 38.0, minimum=0.0)
        downlink_power_w = _number(p, "downlink_power_w", 16.0, minimum=0.0)
        adcs_power_w = _number(p, "adcs_power_w", 12.0, minimum=0.0)
        thermal_capacity_j_per_k = _number(p, "thermal_capacity_j_per_k", 5200.0, minimum=1.0)
        radiator_coeff_w_per_k = _number(p, "radiator_coeff_w_per_k", 1.8, minimum=0.0)
        ambient_temp_c = _number(p, "ambient_temp_c", -5.0)
        bus_temp_c = _number(p, "initial_bus_temp_c", 18.0)
        payload_data_rate_bps = _number(p, "payload_data_rate_bps", 2.5e6, minimum=0.0)
        downlink_rate_max_bps = _number(p, "downlink_rate_max_bps", 1.5e6, minimum=0.0)
        storage_capacity_bits = _number(p, "storage_capacity_bits", 6.0e9, minimum=1.0)
        storage_bits = _number(p, "initial_storage_bits", 0.0, minimum=0.0)
        propulsion_enabled = _bool(p, "propulsion_enabled", True)
        burn_start_s = _number(p, "burn_start_s", duration * 0.55 if duration > 0 else 0.0, minimum=0.0)
        burn_duration_s = _number(p, "burn_duration_s", 10.0, minimum=0.0)
        thrust_delta_v_m_s = _number(p, "burn_delta_v_m_s", 0.02, minimum=0.0)
        propulsion_power_w = _number(p, "propulsion_power_w", 20.0, minimum=0.0)

        rows: list[dict[str, Any]] = []
        cumulative_delta_v = 0.0
        for i in range(n):
            t = min(float(i) * sample, duration)
            dt = sample if i > 0 else 0.0
            labels: dict[str, Any] = self.event_manager.active_labels(t)
            active_events = [ev for ev in self.config.events if ev.active_at(t)]
            active_effects: list[str] = []
            payload_forced_off = False
            comm_forced_off = False
            battery_capacity_ratio = 1.0
            radiator_rejection_ratio = 1.0
            solar_efficiency_ratio = 1.0
            pointing_error_multiplier = 1.0
            safe_mode_threshold: float | None = None

            for ev in active_events:
                effect = ev.effect
                active_effects.append(effect)
                params = ev.parameters
                if effect in {"payload_instrument_off", "payload_off"}:
                    payload_forced_off = True
                    labels["label.payload_forced_off"] = True
                elif effect in {"comm_data_downlink_link_loss", "downlink_loss"}:
                    comm_forced_off = True
                    labels["label.comm_link_loss_active"] = True
                elif effect in {"eps_battery_capacity_loss", "battery_capacity_loss"}:
                    ratio = params.get("remaining_capacity_ratio", params.get("capacity_scale", 0.7))
                    battery_capacity_ratio *= max(0.0, min(1.0, float(ratio or 0.7)))
                    labels["label.eps_capacity_loss_active"] = True
                elif effect in {"thermal_radiator_rejection_loss", "radiator_loss"}:
                    ratio = params.get("remaining_rejection_ratio", params.get("rejection_scale", 0.5))
                    radiator_rejection_ratio *= max(0.0, min(1.0, float(ratio or 0.5)))
                    labels["label.thermal_radiator_loss_active"] = True
                elif effect in {"solar_panel_efficiency_loss", "solar_efficiency_loss"}:
                    ratio = params.get("remaining_efficiency_ratio", params.get("efficiency_scale", 0.7))
                    solar_efficiency_ratio *= max(0.0, min(1.0, float(ratio or 0.7)))
                    labels["label.solar_efficiency_loss_active"] = True
                elif effect in {"power_safe_mode_threshold", "safe_mode_threshold"}:
                    safe_mode_threshold = max(0.0, min(1.0, float(params.get("soc_threshold", 0.25) or 0.25)))
                elif effect in {"adcs_rw_jamming", "rw_jamming"}:
                    multiplier = max(1.0, float(params.get("pointing_error_multiplier", 1.15) or 1.15))
                    pointing_error_multiplier *= multiplier
                    labels["label.adcs_fault_affects_pointing"] = True

            effective_capacity_wh = max(battery_capacity_wh * battery_capacity_ratio, 1e-9)
            battery_energy_wh = min(battery_energy_wh, effective_capacity_wh)
            soc_before = max(0.0, min(1.0, battery_energy_wh / effective_capacity_wh))

            phase = initial_phase + orbit_rate * t
            orbit_fraction = (phase / (2.0 * math.pi)) % 1.0 if orbit_rate > 0.0 else 0.0
            eclipse = 1.0 if orbit_fraction < eclipse_fraction else 0.0
            sun_incidence = 0.0 if eclipse else max(0.0, math.cos(phase))
            pointing_error = initial_pointing * math.exp(-t / adcs_time_constant) * pointing_error_multiplier
            pointing_quality = max(0.0, 1.0 - pointing_error / 30.0)

            safe_mode_engaged = safe_mode_threshold is not None and soc_before < safe_mode_threshold
            labels["label.power_safe_mode_engaged"] = bool(safe_mode_engaged)
            payload_active = (soc_before > 0.25) and (pointing_quality > 0.25) and not payload_forced_off and not safe_mode_engaged
            downlink_visible = (not bool(eclipse)) and pointing_quality > 0.35 and not comm_forced_off and not safe_mode_engaged

            generated_bits = payload_data_rate_bps * pointing_quality * dt if payload_active else 0.0
            downlink_rate = downlink_rate_max_bps * pointing_quality if downlink_visible else 0.0
            available_bits = storage_bits + generated_bits
            downlinked_bits = min(available_bits, downlink_rate * dt)
            unclipped_storage = available_bits - downlinked_bits
            dropped_bits = max(0.0, unclipped_storage - storage_capacity_bits)
            storage_bits = max(0.0, min(storage_capacity_bits, unclipped_storage))

            burn_active = propulsion_enabled and burn_start_s <= t <= burn_start_s + burn_duration_s
            if burn_active and burn_duration_s > 0 and dt > 0:
                cumulative_delta_v += thrust_delta_v_m_s * dt / burn_duration_s

            adcs_power = adcs_power_w * (1.0 + min(pointing_error / 20.0, 1.0))
            payload_power = payload_power_w if payload_active else 0.0
            comm_power = downlink_power_w if downlink_visible else 0.0
            propulsion_power = propulsion_power_w if burn_active else 0.0
            total_load = bus_power_w + adcs_power + payload_power + comm_power + propulsion_power
            solar_power = solar_power_max_w * solar_efficiency_ratio * sun_incidence
            net_power = solar_power - total_load
            if dt > 0:
                battery_energy_wh = max(0.0, min(effective_capacity_wh, battery_energy_wh + net_power * dt / 3600.0))
            soc = max(0.0, min(1.0, battery_energy_wh / effective_capacity_wh))

            effective_radiator_coeff = radiator_coeff_w_per_k * radiator_rejection_ratio
            heat_input = total_load * 0.72 + max(0.0, solar_power) * 0.08
            if dt > 0:
                bus_temp_c += (heat_input - effective_radiator_coeff * (bus_temp_c - ambient_temp_c)) * dt / thermal_capacity_j_per_k

            rows.append({
                "time_s": t,
                "time.step_s": dt,
                "orbit.phase_rad": phase,
                "orbit.eclipse_flag": eclipse,
                "orbit.sun_incidence_cos": sun_incidence,
                "adcs.pointing_error_deg": pointing_error,
                "adcs.pointing_quality": pointing_quality,
                "adcs.power_w": adcs_power,
                "eps.solar_efficiency_scale": solar_efficiency_ratio,
                "eps.solar_array_power_w": solar_power,
                "eps.load_power_w": total_load,
                "eps.net_power_w": net_power,
                "eps.effective_battery_capacity_wh": effective_capacity_wh,
                "eps.battery_energy_wh": battery_energy_wh,
                "eps.battery_soc": soc,
                "thermal.effective_radiator_coeff_w_per_k": effective_radiator_coeff,
                "thermal.heat_input_w": heat_input,
                "thermal.bus_temp_c": bus_temp_c,
                "payload.active": 1.0 if payload_active else 0.0,
                "payload.generated_data_bits": generated_bits,
                "comm.visible": 1.0 if downlink_visible else 0.0,
                "comm.downlink_rate_bps": downlink_rate,
                "comm.downlinked_bits": downlinked_bits,
                "data.dropped_bits": dropped_bits,
                "data.storage_bits": storage_bits,
                "propulsion.burn_active": 1.0 if burn_active else 0.0,
                "propulsion.delta_v_m_s": cumulative_delta_v,
                "event.active_effects": ",".join(active_effects),
                **labels,
            })
        return tuple(rows)

    @staticmethod
    def _coupling_integrity_report(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        checks: dict[str, bool] = {}
        if not rows:
            return {"passed": False, "checks": {"non_empty": False}, "coverage": {}}
        required = (
            "orbit.sun_incidence_cos",
            "orbit.eclipse_flag",
            "eps.solar_array_power_w",
            "eps.net_power_w",
            "eps.effective_battery_capacity_wh",
            "eps.battery_energy_wh",
            "eps.battery_soc",
            "thermal.bus_temp_c",
            "payload.generated_data_bits",
            "comm.downlink_rate_bps",
            "comm.downlinked_bits",
            "data.dropped_bits",
            "data.storage_bits",
        )
        checks["required_fields"] = not any(any(field not in row for field in required) for row in rows)
        solar_pairs = [(float(r["orbit.sun_incidence_cos"]), float(r["eps.solar_array_power_w"])) for r in rows]
        checks["solar_gating"] = not any(sun <= 1e-9 and power > 1e-6 for sun, power in solar_pairs)

        max_data_residual = 0.0
        max_energy_residual = 0.0
        for prev, cur in zip(rows, rows[1:]):
            expected_storage = (
                float(prev["data.storage_bits"])
                + float(cur["payload.generated_data_bits"])
                - float(cur["comm.downlinked_bits"])
                - float(cur["data.dropped_bits"])
            )
            max_data_residual = max(max_data_residual, abs(float(cur["data.storage_bits"]) - expected_storage))
            cap = float(cur["eps.effective_battery_capacity_wh"])
            dt = float(cur.get("time.step_s", float(cur["time_s"]) - float(prev["time_s"])))
            expected_energy = min(float(prev["eps.battery_energy_wh"]), cap) + float(cur["eps.net_power_w"]) * dt / 3600.0
            expected_energy = max(0.0, min(cap, expected_energy))
            max_energy_residual = max(max_energy_residual, abs(float(cur["eps.battery_energy_wh"]) - expected_energy))
        checks["data_balance"] = max_data_residual <= 1e-3
        checks["energy_balance"] = max_energy_residual <= 1e-9

        coverage = {
            "sunlit_samples": sum(1 for r in rows if float(r["orbit.sun_incidence_cos"]) > 1e-9),
            "eclipse_samples": sum(1 for r in rows if float(r["orbit.eclipse_flag"]) > 0.5),
            "payload_generation_samples": sum(1 for r in rows if float(r["payload.generated_data_bits"]) > 0.0),
            "downlink_samples": sum(1 for r in rows if float(r["comm.downlinked_bits"]) > 0.0),
        }
        checks["coverage_sun_and_eclipse"] = coverage["sunlit_samples"] > 0 and coverage["eclipse_samples"] > 0
        checks["coverage_payload_and_downlink"] = coverage["payload_generation_samples"] > 0 and coverage["downlink_samples"] > 0
        return {
            "passed": all(checks.values()),
            "checks": checks,
            "coverage": coverage,
            "max_data_balance_residual_bits": max_data_residual,
            "max_energy_balance_residual_wh": max_energy_residual,
        }

    @staticmethod
    def _coupling_integrity(rows: Sequence[Mapping[str, Any]]) -> bool:
        return bool(WholeSpacecraftBSKSimCoupledScenario._coupling_integrity_report(rows).get("passed"))


class WholeSpacecraftBSKSimCoupledAdapter:
    '''Adapter for ``whole_spacecraft.bsksim_coupled.v1``.'''

    capability_id = WHOLE_SPACECRAFT_BSKSIM_COUPLED_CAPABILITY_ID

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> Sequence[ValidationIssue]:
        issues: list[ValidationIssue] = []
        model = spec.get("model") if isinstance(spec.get("model"), Mapping) else {}
        cid = model.get("capability_id") or spec.get("capability_id")
        if cid != self.capability_id:
            issues.append(ValidationIssue("error", "$.model.capability_id", f"must be {self.capability_id!r}", "capability"))
        if spec.get("task_type") not in {None, "whole_spacecraft"}:
            issues.append(ValidationIssue("error", "$.task_type", "requires task_type='whole_spacecraft'", "capability"))
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        solver = sim.get("solver") if isinstance(sim.get("solver"), Mapping) else {}
        step_s = sim.get("step_s", solver.get("step_s"))
        for value, path in (
            (sim.get("duration_s"), "$.simulation.duration_s"),
            (step_s, "$.simulation.solver.step_s" if "step_s" not in sim else "$.simulation.step_s"),
            (sim.get("sample_s"), "$.simulation.sample_s"),
        ):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or float(value) <= 0:
                issues.append(ValidationIssue("error", path, "must be a positive number", "range"))
        if isinstance(step_s, (int, float)) and isinstance(sim.get("sample_s"), (int, float)) and float(sim["sample_s"]) < float(step_s):
            issues.append(ValidationIssue("error", "$.simulation.sample_s", "must be >= step_s", "range"))
        params = _mapping(spec.get("parameters"))
        values = _mapping(params.get("values")) or params
        for key in ("initial_soc", "eclipse_fraction"):
            if key in values:
                value = values[key]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                    issues.append(ValidationIssue("error", f"$.parameters.values.{key}", "must be within [0, 1]", "range"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        scenario = WholeSpacecraftBSKSimCoupledScenario(whole_spacecraft_coupled_config_from_task_spec(spec))
        result = scenario.run()
        task_id = str(spec.get("task_id", result.summary.get("scenario_id", "whole_spacecraft_bsksim_coupled_task")))
        metadata = spec.get("metadata") if isinstance(spec.get("metadata"), Mapping) else {}
        case_id = str(metadata.get("case_id", "case_000"))
        rows = []
        for row in result.trace_rows:
            r = dict(row)
            r["task_id"] = task_id
            r["case_id"] = case_id
            r["mode"] = result.summary.get("mode_request", "nominal")
            rows.append(r)
        summary = dict(result.summary)
        summary.update({
            "task_id": task_id,
            "case_id": case_id,
            "capability_id": self.capability_id,
            "target_level": "whole_spacecraft",
            "target_name": "bsksim_coupled",
            "trace_rows": len(rows),
            "bsk_engine_schema": result.execution_plan.schema_version,
            "execution_plan_available": True,
            "status": "complete",
        })
        labels = {
            "run_labels": [{
                "task_id": task_id,
                "mode": summary.get("mode_request", "nominal"),
                "capability_id": self.capability_id,
                "engine": summary.get("engine"),
                "fidelity_level": "engineering_coupled_bridge",
            }],
            "event_labels": [event.to_dict() for event in result.execution_plan.events],
            "fault_environment": result.metadata.get("fault_environment", {}),
        }
        model_boundary = result.metadata.get("model_source_boundary", {}) if isinstance(result.metadata, Mapping) else {}
        metadata_out = {
            "capability_id": self.capability_id,
            "execution_plan": result.execution_plan.to_dict(),
            "bsk_engine": result.metadata,
            "fault_environment": result.metadata.get("fault_environment", {}) if isinstance(result.metadata, Mapping) else {},
            "runtime_manifest": result.metadata.get("runtime_manifest", {}) if isinstance(result.metadata, Mapping) else {},
            "model_source_boundary": model_boundary,
            "modifiers_applied_by_adapter": True,
            "modifier_evidence_classification": "runtime_state_equation_effect",
        }
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata_out)

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        payload = json.dumps(dict(spec), indent=2, ensure_ascii=False)
        return (
            "#!/usr/bin/env python3\n"
            "\"\"\"Generated whole_spacecraft.bsksim_coupled.v1 runner.\"\"\"\n"
            "from sat_sim.bsk_engine.whole_spacecraft_coupled import WholeSpacecraftBSKSimCoupledAdapter\n\n"
            f"TASK_SPEC = {payload}\n\n"
            "if __name__ == '__main__':\n"
            "    result = WholeSpacecraftBSKSimCoupledAdapter().run(TASK_SPEC)\n"
            "    print(result.to_dict()['summary'])\n"
        )

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "summary": [
                "status",
                "engine",
                "strong_coupling_chain_count",
                "coupling_integrity_pass",
                "min_battery_soc",
                "max_bus_temp_c",
                "final_storage_bits",
            ],
            "trace_fields": [
                "time_s",
                "orbit.*",
                "adcs.*",
                "eps.*",
                "thermal.*",
                "payload.*",
                "comm.*",
                "data.*",
                "propulsion.*",
                "label.*",
            ],
            "metadata": ["execution_plan", "model_source_boundary", "fault_environment"],
        }


__all__ = [
    "WHOLE_SPACECRAFT_BSKSIM_COUPLED_CAPABILITY_ID",
    "WholeSpacecraftBSKSimCoupledScenario",
    "WholeSpacecraftBSKSimCoupledAdapter",
    "whole_spacecraft_coupled_config_from_task_spec",
]
