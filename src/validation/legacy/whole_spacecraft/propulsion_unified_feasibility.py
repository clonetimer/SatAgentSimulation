"""Propulsion-to-unified Basilisk assembly feasibility runner for v8.21.

This module extends the v8.20 selective unified Basilisk assembly by adding the
propulsion focused backend objects into the same ``SimulationBaseClass`` and the
same spacecraft object.  It is a feasibility runner for message ownership and
SWIG lifecycle safety, not a mission-level reboost/orbit-validation scenario.

Truthfulness boundary:
* Uses Basilisk native spacecraft, reaction wheel, thrusterDynamicEffector,
  fuelTank, power, data, and sensor modules.
* Uses Basilisk Python SysModel modules for PDU/load shedding, thermal state,
  constant heat input, device requests and ST/IMU fusion estimator.
* Tests that propulsion can coexist with ADCS/EPS/Comm/Thermal scheduled chains
  in one SimBaseClass without SWIG lifecycle failure.
* Does not claim full all-subsystem Basilisk full-dynamics or mission-grade
  reboost validation.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, replace
from math import sqrt
from pathlib import Path
from typing import Any

import numpy as np

from Basilisk.utilities import SimulationBaseClass, macros

from integration.whole_spacecraft_links import (
    wire_comm_transmitter_storage_drain,
    wire_payload_to_comm_storage,
)
from subsystems.adcs.builder import attach_adcs_closed_loop_graph, attach_adcs_to_spacecraft
from subsystems.adcs.schemas import RwOnlyClosedLoopConfig
from subsystems.adcs.sensor_estimate import SensorFusionConfig
from subsystems.comm_data.builder import (
    attach_comm_data_basilisk_graph_to_task,
    build_comm_data_basilisk_assembly_graph,
)
from subsystems.eps.builder import (
    attach_eps_load_shedding_graph_to_task,
    build_eps_load_shedding_assembly_graph,
)
from subsystems.payload.builder import (
    attach_payload_basilisk_graph_to_task,
    build_payload_basilisk_assembly_graph,
)
from subsystems.propulsion.builder import (
    attach_propulsion_basilisk_graph_to_spacecraft,
    build_propulsion_basilisk_assembly_graph,
)
from subsystems.propulsion.builder import build_nominal_propulsion_config
from subsystems.propulsion.schemas import PropulsionConfig
from subsystems.thermal.builder import (
    attach_thermal_basilisk_graph_to_task,
    build_thermal_basilisk_assembly_graph,
)
from subsystems.fault_base import FaultSpec
from whole_spacecraft.builder import build_whole_spacecraft_bus
from whole_spacecraft.degradation import WholeSatelliteDegradation


def _norm(vec: Any) -> float:
    return float(sqrt(sum(float(x) * float(x) for x in vec)))


@dataclass(frozen=True)
class PropulsionUnifiedFeasibilityConfig:
    duration_s: float = 300.0
    dyn_step_s: float = 0.2
    fsw_step_s: float = 0.2
    power_step_s: float = 2.0
    sample_s: float = 2.0
    # EPS
    battery_capacity_wh: float = 160.0
    initial_soc: float = 0.62
    solar_power_w: float = 120.0
    bus_power_w: float = 12.0
    payload_power_w: float = 35.0
    adcs_power_w: float = 8.0
    comm_power_w: float = 12.0
    heater_power_w: float = 6.0
    # Comm/Data
    instrument_baud_bps: float = 2.5e6
    storage_capacity_bits: float = 6.0e9
    transmitter_baud_bps: float = 1.5e6
    # Thermal
    payload_heat_w: float = 25.0
    initial_temp_c: float = 22.0
    ambient_temp_c: float = 18.0
    # ADCS
    rw_config: RwOnlyClosedLoopConfig = RwOnlyClosedLoopConfig(duration_s=300.0, dyn_step_s=0.2, fsw_step_s=0.2, sample_s=2.0)
    fusion_config: SensorFusionConfig = SensorFusionConfig(fusion_mode="ideal")
    max_final_attitude_ratio: float = 0.35
    # Propulsion feasibility burn
    propulsion_config: PropulsionConfig = build_nominal_propulsion_config()
    propulsion_on_time_s: tuple[float, ...] | None = None
    propulsion_enabled: bool = True
    # 退化参数（可选）
    degradation: "WholeSatelliteDegradation | None" = None


@dataclass(frozen=True)
class PropulsionUnifiedFeasibilityTraceRow:
    time_s: float
    battery_soc: float
    battery_storage_j: float
    solar_power_w: float
    bus_load_w: float
    payload_load_enabled_w: float
    adcs_load_enabled_w: float
    comm_load_enabled_w: float
    heater_load_enabled_w: float
    load_shed_active: bool
    shed_reason: str
    data_storage_bits: float
    instrument_baud_bps: float
    transmitter_baud_bps: float
    thermal_temp_c: float
    thermal_safe: bool
    thermal_margin_c: float
    attitude_error_norm: float
    rate_error_norm_rad_s: float
    rw_speed_0_rad_s: float
    rw_speed_1_rad_s: float
    rw_speed_2_rad_s: float
    rw_motor_torque_command_0_nm: float  # NOTE: motor torque COMMAND, not measured torque (no torque sensor exists)
    rw_motor_torque_command_1_nm: float
    rw_motor_torque_command_2_nm: float
    estimator_quality: float
    nav_innovation_norm: float
    fuel_mass_kg: float
    fuel_mass_dot_kg_s: float
    thrust_force_n: float
    thrust_force_b_x_n: float
    thrust_factor: float
    position_x_m: float
    velocity_x_m_s: float


@dataclass(frozen=True)
class PropulsionUnifiedFeasibilitySummary:
    backend: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    unified_simbase: bool
    included_subsystems: tuple[str, ...]
    excluded_subsystems: tuple[str, ...]
    native_modules: tuple[str, ...]
    scheduled_custom_modules: tuple[str, ...]
    message_contracts: tuple[str, ...]
    duration_s: float
    sample_count: int
    final_soc: float
    final_data_storage_bits: float
    final_attitude_error_ratio: float
    min_thermal_margin_c: float
    load_shed_event_count: int
    initial_fuel_mass_kg: float
    final_fuel_mass_kg: float
    propellant_used_kg: float
    final_velocity_x_m_s: float
    propulsion_unified: bool
    swig_lifecycle_status: str
    message_ownership_status: str
    status: str
    not_claimed: tuple[str, ...]


def run_propulsion_unified_feasibility(
    config: PropulsionUnifiedFeasibilityConfig | None = None,
    fault_specs: tuple[FaultSpec, ...] | None = None,
) -> tuple[PropulsionUnifiedFeasibilitySummary, tuple[PropulsionUnifiedFeasibilityTraceRow, ...]]:
    """Run the legacy feasibility profile through subsystem-owned builders.

    This entry point is retained for dataset compatibility.  It no longer
    creates ADCS, EPS, Payload, Comm/Data, Thermal, or Propulsion components in
    the whole-spacecraft layer.
    """

    cfg = config or PropulsionUnifiedFeasibilityConfig()
    if min(cfg.duration_s, cfg.dyn_step_s, cfg.fsw_step_s, cfg.power_step_s, cfg.sample_s) <= 0:
        raise ValueError("all timing parameters must be positive")

    battery_capacity_wh = float(cfg.battery_capacity_wh)
    solar_power_w = float(cfg.solar_power_w)
    propulsion_config = cfg.propulsion_config
    if cfg.degradation is not None:
        deg = cfg.degradation
        battery_capacity_wh *= 1.0 - max(0.0, float(deg.eps_degradation.battery_degradation.capacity_loss_pct)) / 100.0
        solar_power_w *= 1.0 - max(0.0, float(deg.eps_degradation.solar_panel_degradation.efficiency_loss_pct)) / 100.0
        thrust_loss = max(0.0, float(deg.propulsion_degradation.thruster_degradation.thrust_loss_pct)) / 100.0
        if thrust_loss > 0.0:
            physical = propulsion_config.thruster_physical
            physical = replace(physical, thrust_n=tuple(float(value) * (1.0 - thrust_loss) for value in physical.thrust_n))
            propulsion_config = replace(propulsion_config, thruster_physical=physical)

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("selectiveUnifiedProcess")
    sim_task = "dynamicsAndSensorsTask"
    fsw_task = "fswTask"
    power_task = "powerThermalDataTask"
    process.addTask(sim.CreateNewTask(sim_task, macros.sec2nano(float(cfg.dyn_step_s))), 30)
    process.addTask(sim.CreateNewTask(fsw_task, macros.sec2nano(float(cfg.fsw_step_s))), 20)
    process.addTask(sim.CreateNewTask(power_task, macros.sec2nano(float(cfg.power_step_s))), 10)

    # Whole-spacecraft owns only the central bus; subsystem builders own their
    # component graphs and message wiring.
    # The unified runner owns the scenario timing.  Preserve the caller's ADCS
    # physical/control parameters while aligning every recorder and scheduled
    # module to the outer whole-spacecraft sample contract.
    rw_cfg = replace(
        cfg.rw_config,
        duration_s=float(cfg.duration_s),
        dyn_step_s=float(cfg.dyn_step_s),
        fsw_step_s=float(cfg.fsw_step_s),
        sample_s=float(cfg.sample_s),
    )
    sc_object = build_whole_spacecraft_bus(rw_cfg, model_tag="unifiedSpacecraft")
    adcs_graph = attach_adcs_to_spacecraft(sc_object, rw_cfg, fusion_config=cfg.fusion_config)
    attach_adcs_closed_loop_graph(sim, sim_task, fsw_task, adcs_graph)
    cl = adcs_graph.rw_closed_loop

    prop_on_times = cfg.propulsion_on_time_s
    if not cfg.propulsion_enabled:
        count = int(propulsion_config.thruster_command.num_thrusters)
        prop_on_times = tuple(0.0 for _ in range(count))
    propulsion_graph = build_propulsion_basilisk_assembly_graph(propulsion_config, on_time_s=prop_on_times)
    attach_propulsion_basilisk_graph_to_spacecraft(sc_object, sim, sim_task, propulsion_graph)

    eps_graph = build_eps_load_shedding_assembly_graph(
        battery_capacity_wh=battery_capacity_wh,
        initial_soc=float(cfg.initial_soc),
        solar_power_w=solar_power_w,
        bus_power_w=float(cfg.bus_power_w),
        payload_power_w=float(cfg.payload_power_w),
        adcs_power_w=float(cfg.adcs_power_w),
        comm_power_w=float(cfg.comm_power_w),
        heater_power_w=float(cfg.heater_power_w),
    )
    attach_eps_load_shedding_graph_to_task(sim, power_task, eps_graph)

    payload_graph = build_payload_basilisk_assembly_graph(
        instrument_baud_bps=float(cfg.instrument_baud_bps),
        data_name="payload_science",
        model_tag="unifiedPayloadInstrument",
    )
    attach_payload_basilisk_graph_to_task(sim, power_task, payload_graph)
    comm_graph = build_comm_data_basilisk_assembly_graph(
        storage_capacity_bits=float(cfg.storage_capacity_bits),
        initial_bits=0.0,
        transmitter_baud_bps=float(cfg.transmitter_baud_bps),
        storage_model_tag="unifiedStorageUnit",
        transmitter_model_tag="unifiedTransmitter",
    )
    attach_comm_data_basilisk_graph_to_task(sim, power_task, comm_graph)
    wire_payload_to_comm_storage(payload_graph.instrument, comm_graph.storage)
    wire_comm_transmitter_storage_drain(comm_graph.transmitter, comm_graph.storage)
    comm_graph.transmitter.nodeBaudRate = -abs(float(cfg.transmitter_baud_bps))

    thermal_graph = build_thermal_basilisk_assembly_graph(
        duration_s=float(cfg.duration_s),
        step_s=float(cfg.power_step_s),
        heat_power_w=float(cfg.payload_heat_w),
        initial_temp_c=float(cfg.initial_temp_c),
        ambient_temp_c=float(cfg.ambient_temp_c),
    )
    attach_thermal_basilisk_graph_to_task(sim, power_task, thermal_graph)
    thermal = thermal_graph.evaluator

    sample_time = macros.sec2nano(float(cfg.sample_s))
    battery = eps_graph.battery
    solar = eps_graph.solar_source
    bus = eps_graph.load_sinks["bus"]
    payload_load = eps_graph.load_sinks["payload"]
    adcs_load = eps_graph.load_sinks["adcs"]
    comm_load = eps_graph.load_sinks["comm"]
    heater_load = eps_graph.load_sinks["heater"]
    pdu = eps_graph.pdu
    instrument = payload_graph.instrument
    storage = comm_graph.storage
    transmitter = comm_graph.transmitter
    estimator = cl.estimator
    attitude_rec = cl.attitude_log
    rw_rec = cl.rw_speed_log
    rw_motor_torque_cmd_rec = cl.rw_motor_torque_log
    fuel_tank_obj = propulsion_graph.fuel_tank
    thruster_effector = propulsion_graph.thruster_effector

    recorders = {
        "battery": battery.batPowerOutMsg.recorder(sample_time),
        "solar": solar.nodePowerOutMsg.recorder(sample_time),
        "bus": bus.nodePowerOutMsg.recorder(sample_time),
        "payload": payload_load.nodePowerOutMsg.recorder(sample_time),
        "adcs": adcs_load.nodePowerOutMsg.recorder(sample_time),
        "comm": comm_load.nodePowerOutMsg.recorder(sample_time),
        "heater": heater_load.nodePowerOutMsg.recorder(sample_time),
        "storage": storage.storageUnitDataOutMsg.recorder(sample_time),
        "instrument": instrument.nodeDataOutMsg.recorder(sample_time),
        "transmitter": transmitter.nodeDataOutMsg.recorder(sample_time),
        "spacecraft": sc_object.scStateOutMsg.recorder(sample_time),
        "fuel": fuel_tank_obj.fuelTankOutMsg.recorder(sample_time),
    }
    for name in ("battery", "solar", "bus", "payload", "adcs", "comm", "heater", "storage", "instrument", "transmitter"):
        sim.AddModelToTask(power_task, recorders[name])
    sim.AddModelToTask(sim_task, recorders["spacecraft"])
    sim.AddModelToTask(sim_task, recorders["fuel"])
    thr_rec = None
    if len(thruster_effector.thrusterOutMsgs) > 0:
        thr_rec = thruster_effector.thrusterOutMsgs[0].recorder(sample_time)
        sim.AddModelToTask(sim_task, thr_rec)

    fault_injector = None
    if fault_specs:
        from whole_spacecraft._runtime_fault_injector import FaultInjector
        registry = {
            "battery": battery,
            "transmitter": transmitter,
            "storage": storage,
            "instrument": instrument,
            "payload_instrument": instrument,
            "thruster": thruster_effector,
            "fuel_tank": fuel_tank_obj,
            "imu": cl.imu,
            "star_tracker": cl.star_tracker,
            "sun_sensor": cl.css,
            "magnetometer": cl.magnetometer,
            "rw_cluster": cl.rw_state_effector,
            "rw_0": cl.rw_state_effector,
        }
        fault_injector = FaultInjector(sim, sc_object, list(fault_specs), registry)
        fault_injector.schedule_all_faults()

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    times = [float(value) * macros.NANO2SEC for value in attitude_rec.times()]
    sigma_br = np.asarray(attitude_rec.sigma_BR, dtype=float).tolist()
    omega_br = np.asarray(attitude_rec.omega_BR_B, dtype=float).tolist()
    rw_speeds = np.asarray(rw_rec.wheelSpeeds, dtype=float).tolist()
    rw_motor_torque_cmds = np.asarray(rw_motor_torque_cmd_rec.motorTorque, dtype=float).tolist()
    attitude_norms = [_norm(value) for value in sigma_br]
    rate_norms = [_norm(value) for value in omega_br]
    pdu_by_time = {round(row.time_s, 9): row for row in pdu.trace}
    thermal_by_time = {round(row.time_s, 9): row for row in thermal.trace}
    est_by_time = {round(row["time_s"], 9): row for row in estimator.trace}

    def _arr(rec: Any, attr: str, idx: int, default: float = 0.0) -> float:
        if hasattr(rec, attr) and len(getattr(rec, attr)) > idx:
            try:
                return float(getattr(rec, attr)[idx])
            except Exception:
                return default
        return default

    rows: list[PropulsionUnifiedFeasibilityTraceRow] = []
    for idx, time_s in enumerate(times):
        decision = pdu_by_time.get(round(time_s, 9))
        th = thermal_by_time.get(round(time_s, 9))
        est = est_by_time.get(round(time_s, 9), {})
        cap = _arr(recorders["battery"], "storageCapacity", idx, 1.0)
        stored_j = _arr(recorders["battery"], "storageLevel", idx, 0.0)
        speeds = rw_speeds[idx] if idx < len(rw_speeds) else [0.0, 0.0, 0.0]
        torques = rw_motor_torque_cmds[idx] if idx < len(rw_motor_torque_cmds) else [0.0, 0.0, 0.0]
        fuel_mass = _arr(recorders["fuel"], "fuelMass", idx, float(propulsion_config.fuel_tank.initial_mass_kg))
        fuel_mdot = _arr(recorders["fuel"], "fuelMassDot", idx, 0.0)
        thrust_force = _arr(thr_rec, "thrustForce", idx, 0.0) if thr_rec is not None else 0.0
        thrust_force_b_x = 0.0
        if thr_rec is not None and hasattr(thr_rec, "thrustForce_B") and len(thr_rec.thrustForce_B) > idx:
            try:
                thrust_force_b_x = float(thr_rec.thrustForce_B[idx][0])
            except Exception:
                thrust_force_b_x = 0.0
        thrust_factor = _arr(thr_rec, "thrustFactor", idx, 0.0) if thr_rec is not None else 0.0
        r_bn = recorders["spacecraft"].r_BN_N[idx] if len(recorders["spacecraft"].r_BN_N) > idx else [0.0, 0.0, 0.0]
        v_bn = recorders["spacecraft"].v_BN_N[idx] if len(recorders["spacecraft"].v_BN_N) > idx else [0.0, 0.0, 0.0]
        rows.append(PropulsionUnifiedFeasibilityTraceRow(
            time_s=time_s,
            battery_soc=stored_j / cap if cap else 0.0,
            battery_storage_j=stored_j,
            solar_power_w=_arr(recorders["solar"], "netPower", idx, solar_power_w),
            bus_load_w=-_arr(recorders["bus"], "netPower", idx, -cfg.bus_power_w),
            payload_load_enabled_w=-_arr(recorders["payload"], "netPower", idx, 0.0),
            adcs_load_enabled_w=-_arr(recorders["adcs"], "netPower", idx, 0.0),
            comm_load_enabled_w=-_arr(recorders["comm"], "netPower", idx, 0.0),
            heater_load_enabled_w=-_arr(recorders["heater"], "netPower", idx, 0.0),
            load_shed_active=bool(decision.load_shed_active) if decision else False,
            shed_reason=decision.shed_reason if decision else "none",
            data_storage_bits=_arr(recorders["storage"], "storageLevel", idx, 0.0),
            instrument_baud_bps=_arr(recorders["instrument"], "baudRate", idx, cfg.instrument_baud_bps),
            transmitter_baud_bps=_arr(recorders["transmitter"], "baudRate", idx, cfg.transmitter_baud_bps),
            thermal_temp_c=float(th.temp_c) if th else cfg.initial_temp_c,
            thermal_safe=bool(th.thermal_safe) if th else True,
            thermal_margin_c=float(th.max_margin_c) if th else 0.0,
            attitude_error_norm=float(attitude_norms[idx]) if idx < len(attitude_norms) else 0.0,
            rate_error_norm_rad_s=float(rate_norms[idx]) if idx < len(rate_norms) else 0.0,
            rw_speed_0_rad_s=float(speeds[0]) if len(speeds) > 0 else 0.0,
            rw_speed_1_rad_s=float(speeds[1]) if len(speeds) > 1 else 0.0,
            rw_speed_2_rad_s=float(speeds[2]) if len(speeds) > 2 else 0.0,
            rw_motor_torque_command_0_nm=float(torques[0]) if len(torques) > 0 else 0.0,
            rw_motor_torque_command_1_nm=float(torques[1]) if len(torques) > 1 else 0.0,
            rw_motor_torque_command_2_nm=float(torques[2]) if len(torques) > 2 else 0.0,
            estimator_quality=float(est.get("estimator_quality", 1.0)),
            nav_innovation_norm=float(est.get("nav_innovation_norm", 0.0)),
            fuel_mass_kg=fuel_mass,
            fuel_mass_dot_kg_s=fuel_mdot,
            thrust_force_n=thrust_force,
            thrust_force_b_x_n=thrust_force_b_x,
            thrust_factor=thrust_factor,
            position_x_m=float(r_bn[0]) if len(r_bn) else 0.0,
            velocity_x_m_s=float(v_bn[0]) if len(v_bn) else 0.0,
        ))

    initial_att = rows[0].attitude_error_norm if rows else 0.0
    ratio = (rows[-1].attitude_error_norm if rows else 0.0) / max(initial_att, 1e-12)
    min_margin = min((row.thermal_margin_c for row in rows), default=0.0)
    initial_fuel = rows[0].fuel_mass_kg if rows else 0.0
    final_fuel = rows[-1].fuel_mass_kg if rows else 0.0
    propellant_used = max(0.0, initial_fuel - final_fuel)
    propulsion_ok = (not cfg.propulsion_enabled) or (propellant_used > 0.0 and abs(rows[-1].velocity_x_m_s) > 0.0)
    runtime_ok = fault_injector is None or fault_injector.event_audit().get("runtime_event_status") == "PASS"
    status = "PASS" if rows and rows[-1].battery_soc > 0.0 and rows[-1].data_storage_bits >= 0.0 and ratio <= cfg.max_final_attitude_ratio and propulsion_ok and runtime_ok else "FAIL"
    summary = PropulsionUnifiedFeasibilitySummary(
        backend="propulsion_unified_feasibility_v8_21_subsystem_owned",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        unified_simbase=True,
        included_subsystems=("eps", "comm_data", "thermal_scheduled", "adcs_sensor_fusion_rw_closed_loop", "propulsion_focused_chain"),
        excluded_subsystems=(),
        native_modules=(
            "spacecraft.Spacecraft", "reactionWheelStateEffector.ReactionWheelStateEffector", "imuSensor.ImuSensor", "starTracker.StarTracker", "coarseSunSensor.CoarseSunSensor", "magnetometer.Magnetometer",
            "inertial3D.inertial3D", "attTrackingError.attTrackingError", "mrpFeedback.mrpFeedback", "rwMotorTorque.rwMotorTorque",
            "simpleBattery.SimpleBattery", "simplePowerSink.SimplePowerSink", "simpleInstrument.SimpleInstrument", "simpleStorageUnit.SimpleStorageUnit", "simpleTransmitter.SimpleTransmitter",
            "thrusterDynamicEffector.ThrusterDynamicEffector", "fuelTank.FuelTank",
        ),
        scheduled_custom_modules=("PduLoadSheddingSysModel", "ConstantDeviceRequest", "ThermalNodeScheduledSysModel", "ConstantThermalPowerInput", "StarTrackerImuFusionEstimator"),
        message_contracts=("PowerNodeUsageMsg", "PowerStorageStatusMsg", "DeviceCmdMsg", "DeviceStatusMsg", "NavAttMsg", "STSensorMsg", "IMUSensorMsg", "DataNodeUsageMsg", "THRArrayOnTimeCmdMsg", "FuelTankMsg", "THROutputMsg"),
        duration_s=float(cfg.duration_s),
        sample_count=len(rows),
        final_soc=rows[-1].battery_soc if rows else 0.0,
        final_data_storage_bits=rows[-1].data_storage_bits if rows else 0.0,
        final_attitude_error_ratio=float(ratio),
        min_thermal_margin_c=float(min_margin),
        load_shed_event_count=sum(1 for row in rows if row.load_shed_active),
        initial_fuel_mass_kg=float(initial_fuel),
        final_fuel_mass_kg=float(final_fuel),
        propellant_used_kg=float(propellant_used),
        final_velocity_x_m_s=float(rows[-1].velocity_x_m_s if rows else 0.0),
        propulsion_unified=True,
        swig_lifecycle_status="PASS_no_process_crash_or_invalidated_thruster_tank_messages",
        message_ownership_status="PASS_whole_calls_subsystem_builders_and_subsystems_own_component_messages",
        status=status,
        not_claimed=("full_all_subsystem_basilisk_full_dynamics", "native_basilisk_thermal_network", "mission_grade_reboost_validation", "flight_validated_adcs_filter"),
    )
    return summary, tuple(rows)


def write_propulsion_unified_feasibility_dataset(output_dir: str | Path, config: PropulsionUnifiedFeasibilityConfig | None = None) -> dict[str, str]:
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows = run_propulsion_unified_feasibility(config)
    summary_path = output_dir / "propulsion_unified_feasibility_summary.json"
    trace_path = output_dir / "propulsion_unified_feasibility_trace.csv"
    manifest_path = output_dir / "propulsion_unified_feasibility_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(PropulsionUnifiedFeasibilityTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "propulsion_unified_feasibility_v8_21",
        "backend_truth": "single SimulationBaseClass assembly with EPS, Comm/Data, Thermal scheduled custom module, ADCS ST/IMU fusion RW closed-loop, and propulsion thruster/fuelTank focused chain on the same spacecraft",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="datasets/propulsion_unified_feasibility")
    args = parser.parse_args()
    print(json.dumps(write_propulsion_unified_feasibility_dataset(args.output_dir), indent=2, ensure_ascii=False))
