"""ADCS BSKSim-style migration bridge.

This module brings the ADCS capability into the project-owned BSKSim-style
scenario/dynamics/FSW/event/recorder structure while preserving the validated
ADCS fidelity propagation as the physics bridge. It is a staged migration:
module boundaries, modeRequest, event routing and recorders are explicit; full
replacement of every proxy with Basilisk-native FSW modules is planned later.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sat_sim.adapter_base import SimulationResult
from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter
from sat_sim.fault_environment import BSKRLStyleFaultAdapter

from .event_manager import parse_bsk_events
from .recorder_registry import recorders_from_spec
from .types import (
    BSK_ENGINE_SCHEMA_VERSION,
    BSKConnectionSpec,
    BSKExecutionPlan,
    BSKModuleSpec,
    BSKProcessSpec,
    BSKRecorderSpec,
    BSKScenarioConfig,
    BSKTaskSpec,
)

ADCS_BSKSIM_CAPABILITY_ID = "subsystem.adcs_bsksim.v1"


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _simulation_float(spec: Mapping[str, Any], key: str, default: float) -> float:
    sim = _mapping(spec.get("simulation"))
    if key == "step_s" and key not in sim:
        value = _mapping(sim.get("solver")).get("step_s", default)
    else:
        value = sim.get(key, default)
    try:
        return float(value)
    except Exception:
        return float(default)


def adcs_config_from_task_spec(spec: Mapping[str, Any]) -> BSKScenarioConfig:
    model = _mapping(spec.get("model"))
    target = _mapping(model.get("target"))
    task = _mapping(spec.get("task"))
    params = _mapping(spec.get("parameters"))
    values = _mapping(params.get("values")) or _mapping(spec.get("parameters"))
    outputs = _mapping(spec.get("outputs"))
    plots = outputs.get("plots") if isinstance(outputs.get("plots"), Sequence) and not isinstance(outputs.get("plots"), (str, bytes)) else []
    mode = str(target.get("mode") or values.get("target_mode") or "nadir")
    return BSKScenarioConfig(
        scenario_id=str(task.get("id") or spec.get("task_id") or "adcs_bsksim_task"),
        capability_id=str(model.get("capability_id") or spec.get("capability_id") or ADCS_BSKSIM_CAPABILITY_ID),
        duration_s=_simulation_float(spec, "duration_s", 60.0),
        step_s=_simulation_float(spec, "step_s", 0.25),
        sample_s=_simulation_float(spec, "sample_s", 2.0),
        mode_request=mode,
        parameters=values,
        events=parse_bsk_events(spec),
        requested_outputs=tuple(str(x) for x in plots),
    )


class ADCSBSKSimScenario:
    """BSKSim-style ADCS scenario with explicit module and recorder contracts."""

    def __init__(self, config: BSKScenarioConfig) -> None:
        self.config = config

    def build_execution_plan(self) -> BSKExecutionPlan:
        processes = (
            BSKProcessSpec("DynamicsProcess", "dynamics", "ADCS spacecraft and actuator dynamics process"),
            BSKProcessSpec("FswProcess", "fsw", "ADCS guidance, navigation, control and actuator allocation process"),
        )
        tasks = (
            BSKTaskSpec("DynamicsTask", "DynamicsProcess", self.config.step_s, "Spacecraft, sensor and actuator dynamics"),
            BSKTaskSpec("FswTask", "FswProcess", self.config.step_s, "ADCS flight-software chain"),
        )
        modules = (
            BSKModuleSpec("spacecraft", "Basilisk.simulation.spacecraft.Spacecraft", "DynamicsTask", "spacecraft hub", status="declared_native_module"),
            BSKModuleSpec("simple_nav", "Basilisk.simulation.simpleNav", "DynamicsTask", "truth/navigation message bridge", status="declared_native_module"),
            BSKModuleSpec("gyro_sensor", "ADCS gyro measurement model", "DynamicsTask", "rate sensor", status="project_proxy_with_bsk_recorder"),
            BSKModuleSpec("star_tracker", "ADCS star tracker measurement model", "DynamicsTask", "attitude sensor", status="project_proxy_with_bsk_recorder"),
            BSKModuleSpec("sun_sensor", "ADCS sun sensor measurement model", "DynamicsTask", "coarse sun sensor", status="project_proxy_with_bsk_recorder"),
            BSKModuleSpec("magnetometer", "ADCS magnetometer measurement model", "DynamicsTask", "magnetic field sensor", status="project_proxy_with_bsk_recorder"),
            BSKModuleSpec("reaction_wheels", "Basilisk.simulation.reactionWheelStateEffector", "DynamicsTask", "reaction wheel cluster", status="declared_native_or_bridge_module"),
            BSKModuleSpec("magnetorquers", "ADCS magnetorquer actuator bridge", "DynamicsTask", "magnetic torque actuator", status="project_proxy_with_bsk_recorder"),
            BSKModuleSpec("mode_request", "BSKSim-style modeRequest", "FswTask", "mode manager", status="project_control_surface"),
            BSKModuleSpec("guidance", "Basilisk.fswAlgorithms.inertial3D / hillPoint / sunSafe", "FswTask", "guidance reference", status="declared_native_or_bridge_module"),
            BSKModuleSpec("attitude_error", "Basilisk.fswAlgorithms.attTrackingError", "FswTask", "tracking error", status="declared_native_or_bridge_module"),
            BSKModuleSpec("mrp_feedback", "Basilisk.fswAlgorithms.mrpFeedback", "FswTask", "attitude controller", status="declared_native_or_bridge_module"),
            BSKModuleSpec("rw_torque_mapper", "Basilisk.fswAlgorithms.rwMotorTorque", "FswTask", "reaction-wheel torque mapping", status="declared_native_or_bridge_module"),
            BSKModuleSpec("rw_allocation", "weighted pseudoinverse allocation", "FswTask", "three-/four-wheel allocation", status="project_control_surface"),
        )
        connections = (
            BSKConnectionSpec("spacecraft.scStateOutMsg", "simple_nav.scStateInMsg", "spacecraft state to navigation"),
            BSKConnectionSpec("simple_nav.attOutMsg", "attitude_error.attNavInMsg", "navigation attitude to tracking-error module"),
            BSKConnectionSpec("guidance.attRefOutMsg", "attitude_error.attRefInMsg", "guidance reference to tracking-error module"),
            BSKConnectionSpec("attitude_error.attGuidOutMsg", "mrp_feedback.guidInMsg", "tracking error to controller"),
            BSKConnectionSpec("mrp_feedback.cmdTorqueOutMsg", "rw_torque_mapper.vehControlInMsg", "body torque command to wheel mapper"),
            BSKConnectionSpec("rw_torque_mapper.rwMotorTorqueOutMsg", "reaction_wheels.rwMotorCmdInMsg", "wheel motor commands to wheel dynamics"),
            BSKConnectionSpec("magnetometer.magFieldOut", "magnetorquers.magFieldIn", "magnetic field to magnetorquer bridge"),
        )
        requested = {"outputs": {"plots": list(self.config.requested_outputs)}}
        recorders = list(recorders_from_spec(requested, sample_s=self.config.sample_s))
        required = (
            BSKRecorderSpec("adcs.attitude.pointing_error_deg", "fsw.attitude_error", self.config.sample_s, "deg", "姿态指向误差"),
            BSKRecorderSpec("adcs.rw.speed_rad_s_*", "dynamics.reaction_wheels", self.config.sample_s, "rad/s", "反作用轮转速"),
            BSKRecorderSpec("adcs.control.applied_torque_nm_*", "fsw.mrp_feedback", self.config.sample_s, "N·m", "控制力矩"),
            BSKRecorderSpec("adcs.event.active_effects", "event_manager", self.config.sample_s, "", "ADCS事件活动状态"),
        )
        existing = {r.field for r in recorders}
        for rec in required:
            if rec.field not in existing:
                recorders.append(rec)
        return BSKExecutionPlan(
            schema_version=BSK_ENGINE_SCHEMA_VERSION,
            engine="project_bsksim_style_adcs",
            scenario_id=self.config.scenario_id,
            processes=processes,
            tasks=tasks,
            modules=modules,
            connections=connections,
            events=tuple(self.config.events),
            recorders=tuple(recorders),
            mode_request=self.config.mode_request,
            notes=(
                "v0.5.4.1 migrates ADCS into the project-owned BSKSim-style scenario contract.",
                "v0.5.4.2 adds BSK-RL-style fault episode evidence without adding Gym/RL runtime dependencies.",
                "Validated ADCS fidelity propagation remains the physics bridge until every Basilisk-native FSW module is fully substituted.",
            ),
        )

    def run(self, spec: Mapping[str, Any]) -> SimulationResult:
        plan = self.build_execution_plan()
        bridged_spec = dict(spec)
        # The validated ADCS fidelity bridge consumes the legacy modifiers.* event
        # contract. BSKSim-style scenarios use events.* as the canonical contract,
        # so mirror events into modifiers without deleting existing modifiers.
        events_obj = _mapping(bridged_spec.get("events"))
        if events_obj:
            modifiers = _mapping(bridged_spec.get("modifiers"))
            for category in ("faults", "degradations", "constraints"):
                existing = list(modifiers.get(category, []) or [])
                for item in events_obj.get(category, []) or []:
                    if isinstance(item, Mapping):
                        existing.append(dict(item))
                if existing:
                    modifiers[category] = existing
            bridged_spec["modifiers"] = modifiers
        bridged_spec["capability_id"] = "subsystem.adcs_fidelity.v1"
        model = _mapping(bridged_spec.get("model"))
        model["capability_id"] = "subsystem.adcs_fidelity.v1"
        bridged_spec["model"] = model
        result = AdcsFidelityAdapter().run(bridged_spec)
        fault_environment = BSKRLStyleFaultAdapter().summarize(plan.events, tuple(result.trace_rows))
        summary = dict(result.summary)
        summary.update({
            "capability_id": ADCS_BSKSIM_CAPABILITY_ID,
            "engine": "project_bsksim_style_adcs",
            "bsk_engine_schema": plan.schema_version,
            "bsk_process_count": len(plan.processes),
            "bsk_task_count": len(plan.tasks),
            "bsk_module_count": len(plan.modules),
            "bsk_connection_count": len(plan.connections),
            "bsk_event_count": len(plan.events),
            "bsk_recorder_count": len(plan.recorders),
            "fault_environment_episode_count": fault_environment.episode_count,
            "fault_environment_style": fault_environment.style,
            "mode_request": self.config.mode_request,
            "migration_stage": "adcs_bsksim_style_bridge",
            "native_module_migration": "partial_adcs_contract_migration_with_validated_fidelity_bridge",
        })
        rows = []
        for row in result.trace_rows:
            r = dict(row)
            r["bsk.engine"] = "project_bsksim_style_adcs"
            r["bsk.mode_request"] = self.config.mode_request
            rows.append(r)
        labels = dict(result.labels)
        labels["bsk_event_labels"] = [e.to_dict() for e in plan.events]
        labels["fault_environment"] = fault_environment.to_dict()
        labels.setdefault("run_labels", []).append({
            "capability_id": ADCS_BSKSIM_CAPABILITY_ID,
            "engine": "project_bsksim_style_adcs",
            "mode": self.config.mode_request,
        })
        metadata = dict(result.metadata)
        metadata.update({
            "execution_plan": plan.to_dict(),
            "fault_environment": fault_environment.to_dict(),
            "modifiers_applied_by_adapter": True,
            "model_source_boundary": {
                "bsk_style_process_task_split": True,
                "adcs_module_contract_migrated": True,
                "physics_bridge": "sat_sim.adapters.subsystem_adcs_fidelity.AdcsFidelityAdapter",
                "claim": "ADCS BSKSim-style migration bridge; not full official BSKSim template integration",
            },
        })
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata)


__all__ = ["ADCS_BSKSIM_CAPABILITY_ID", "ADCSBSKSimScenario", "adcs_config_from_task_spec"]
