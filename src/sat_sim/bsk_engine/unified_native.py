"""Unified Basilisk runtime for ADCS and whole-spacecraft coupling.

The runtime mixes official Basilisk modules with project-owned ``SysModel``
modules in one Process/Task.  It intentionally excludes external co-simulation
solvers and curve-only proxies.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Any, Mapping

import numpy as np

from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue
from sat_sim.fault_environment import BSKRLStyleFaultAdapter
from integration.resource_feedback import (
    AdcsControlPowerBridge,
    DataActivityPowerBridge,
    DownlinkActivityPowerBridge,
    InputReadStatus,
    StatusPowerBridge,
    ThrusterActivityPowerBridge,
)

from .event_manager import parse_bsk_events
from .types import BSKEventSpec

ADCS_UNIFIED_CAPABILITY_ID = "subsystem.adcs_unified_native.v1"
WHOLE_UNIFIED_CAPABILITY_ID = "whole_spacecraft.unified_native.v1"

ADCS_NATIVE_EVENT_EFFECTS = {
    "rw_jamming", "adcs_rw_jamming", "rw_motor_failure", "adcs_rw_motor_failure",
    "gyro_bias_step", "gyro_noise_increase", "rw_friction_degradation", "adcs_rw_torque_authority_loss",
    "reaction_wheel_speed_limit", "adcs_reaction_wheel_speed_limit",
    "adcs_star_tracker_signal_loss", "adcs_star_tracker_bias_drift", "adcs_star_tracker_accuracy_loss",
    "adcs_star_tracker_fov_obstruction", "adcs_star_tracker_stuck_at_last", "adcs_star_tracker_blinding",
    "adcs_star_tracker_dropout", "adcs_star_tracker_misalignment",
    "adcs_sun_sensor_signal_loss", "adcs_sun_sensor_bias_drift", "adcs_sun_sensor_noise_increase",
    "adcs_sun_sensor_saturation", "adcs_sun_sensor_eclipse_blindness", "adcs_sun_sensor_false_eclipse",
    "adcs_sun_sensor_cell_failure", "adcs_sun_sensor_contamination",
    "adcs_imu_signal_loss", "adcs_imu_gyro_bias_drift", "adcs_imu_accel_bias_drift",
    "adcs_imu_gyro_noise_increase", "adcs_imu_accel_noise_increase", "adcs_imu_stuck_at_zero",
    "adcs_imu_axis_dropout", "adcs_imu_noise_burst", "adcs_rw_speed_sensor_fault",
    "adcs_sensor_failure", "adcs_control_loop_failure", "adcs_actuator_failure",
}
WHOLE_NATIVE_EVENT_EFFECTS = ADCS_NATIVE_EVENT_EFFECTS | {
    "payload_instrument_off", "comm_data_downlink_link_loss",
    "eps_battery_capacity_loss", "thermal_radiator_rejection_loss",
    "solar_panel_efficiency_loss", "power_safe_mode_threshold",
    "propulsion_thruster_ignition_failure", "propulsion_burn_impulse_loss",
}

# Keep validation and script export independent from an installed Basilisk
# runtime.  The implementation classes are imported lazily inside
# ``UnifiedNativeRuntime.run`` after the runtime gate has been crossed.
RW_COMMAND_EVENT_EFFECTS = {
    "rw_jamming", "adcs_rw_jamming", "rw_motor_failure", "adcs_rw_motor_failure",
    "rw_friction_degradation", "adcs_rw_torque_authority_loss", "reaction_wheel_speed_limit",
    "adcs_reaction_wheel_speed_limit",
    "adcs_actuator_failure", "adcs_control_loop_failure",
}


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _parameter_values(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return user parameters from canonical/direct or flattened runtime form."""
    parameters = _mapping(spec.get("parameters"))
    nested = _mapping(parameters.get("values"))
    return nested if nested else parameters


def _value(spec: Mapping[str, Any], key: str, default: Any) -> Any:
    return _parameter_values(spec).get(key, default)


def _sim_number(spec: Mapping[str, Any], key: str, default: float) -> float:
    simulation = _mapping(spec.get("simulation"))
    if key == "step_s":
        raw = simulation.get(key, _mapping(simulation.get("solver")).get(key, default))
    else:
        raw = simulation.get(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return float(default)


def _pointing_error_deg(sigma: Any) -> float:
    norm = math.sqrt(sum(float(x) ** 2 for x in list(sigma)[:3]))
    return 4.0 * math.degrees(math.atan(norm))


def _norm3(values: Any) -> float:
    return math.sqrt(sum(float(x) ** 2 for x in list(values)[:3]))


def _reaction_wheel_axes(configuration: str) -> tuple[tuple[float, float, float], ...]:
    """Return the selected, non-duplicated actuator architecture."""
    from .rw_geometry import standard_rw_axes
    return standard_rw_axes(configuration)



def _bridge_health_payload(bridge: Any) -> dict[str, Any]:
    """Summarise message health and directional activity-to-power evidence.

    A bridge is not considered evidenced merely because it produced finite,
    non-negative values.  The summary distinguishes successful message reads,
    initialization-only NOT_READY samples, runtime read errors, actual stimulus
    and whether the output direction is physically consistent with the input.
    """
    trace = list(getattr(bridge, "trace", ()))
    errors = [row for row in trace if getattr(row, "input_status", None) == InputReadStatus.ERROR]
    not_ready = [row for row in trace if getattr(row, "input_status", None) == InputReadStatus.NOT_READY]
    valid = [row for row in trace if getattr(row, "input_status", None) == InputReadStatus.VALID]
    powers = [float(getattr(row, "electrical_power_w", 0.0)) for row in valid]
    activities = [float(getattr(row, "activity", 0.0)) for row in valid]
    enabled_values = [bool(getattr(row, "enabled", False)) for row in valid]
    power_span = (max(powers) - min(powers)) if powers else 0.0
    activity_span = (max(activities) - min(activities)) if activities else 0.0
    tolerance_w = 1.0e-9
    directional_violations: list[dict[str, Any]] = []
    for row in valid:
        enabled = bool(getattr(row, "enabled", False))
        activity = max(0.0, float(getattr(row, "activity", 0.0)))
        power = max(0.0, float(getattr(row, "electrical_power_w", 0.0)))
        # Disabled equipment must never create an electrical load.  Enabled
        # activity above numerical noise must create a positive load.
        if not enabled and power > tolerance_w:
            directional_violations.append({"time_ns": int(getattr(row, "time_ns", 0)), "reason": "disabled_with_positive_power", "power_w": power})
        elif enabled and activity > 1.0e-9 and power <= tolerance_w:
            directional_violations.append({"time_ns": int(getattr(row, "time_ns", 0)), "reason": "active_with_zero_power", "activity": activity})
    positive_activity_count = sum(1 for value in activities if value > 1.0e-9)
    positive_power_count = sum(1 for value in powers if value > tolerance_w)
    stimulus_observed = bool(
        positive_activity_count
        or power_span > tolerance_w
        or activity_span > 1.0e-9
        or len(set(enabled_values)) > 1
    )
    return {
        "status": "FAIL" if errors or directional_violations else ("PASS" if valid else "NOT_READY"),
        "sample_count": len(trace),
        "valid_sample_count": len(valid),
        "not_ready_sample_count": len(not_ready),
        "error_count": len(errors),
        "errors": [str(getattr(row, "error", "message read error")) for row in errors[:10]],
        "power_min_w": min(powers) if powers else 0.0,
        "power_max_w": max(powers) if powers else 0.0,
        "power_span_w": power_span,
        "activity_min": min(activities) if activities else 0.0,
        "activity_max": max(activities) if activities else 0.0,
        "activity_span": activity_span,
        "positive_activity_sample_count": positive_activity_count,
        "positive_power_sample_count": positive_power_count,
        "enabled_transition_observed": len(set(enabled_values)) > 1,
        "stimulus_observed": stimulus_observed,
        "directional_response_pass": not directional_violations,
        "directional_violation_count": len(directional_violations),
        "directional_violations": directional_violations[:10],
    }


def _required_coupling_evidence(required: tuple[str, ...], bridge_health: Mapping[str, Mapping[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not required:
        return {
            "status": "NOT_REQUIRED",
            "requirements": [],
            "claim_scope": "no_explicit_causal_requirement_declared_for_this_run",
            "paired_run_required_for_causal_pass": True,
        }
    mapping = {
        "eps_pdu_to_payload_activity": "payload",
        "eps_pdu_to_comm_activity": "comm",
        "eps_pdu_to_thermal_heaters": "heater",
        "payload_activity_to_eps_thermal": "payload",
        "adcs_control_effort_to_eps_thermal": "adcs",
        "propulsion_activity_to_eps_thermal": "propulsion",
    }
    items: list[dict[str, Any]] = []
    for coupling_id in required:
        bridge_name = mapping.get(coupling_id)
        if bridge_name is None:
            # Non-resource causal edges are evidenced directly in telemetry.
            if coupling_id == "adcs_pointing_to_payload_gate":
                observed = any(int(row.get("payload.active", 0)) == 0 and float(row.get("adcs.pointing_error_deg", 0.0)) > 0.0 for row in rows)
            elif coupling_id == "adcs_pointing_to_comm_gate":
                observed = any(int(row.get("comm.active", 0)) == 0 and int(row.get("comm.command_permitted", 0)) == 0 for row in rows)
            elif coupling_id == "orbit_sun_attitude_eclipse_to_eps_solar_power":
                observed = (max((float(row.get("eps.solar_array_power_w", 0.0)) for row in rows), default=0.0) - min((float(row.get("eps.solar_array_power_w", 0.0)) for row in rows), default=0.0)) > 1.0e-6
            elif coupling_id == "propulsion_effector_to_spacecraft":
                observed = any(float(row.get("propulsion.total_thrust_n", 0.0)) > 0.0 for row in rows)
            elif coupling_id == "ground_access_to_comm_downlink":
                observed = any(
                    int(row.get("comm.geometric_access", 0)) == 0 and int(row.get("comm.active", 0)) == 0
                    for row in rows
                ) or any(
                    int(row.get("comm.geometric_access", 0)) == 1 and float(row.get("comm.downlink_bps", 0.0)) > 0.0
                    for row in rows
                )
            elif coupling_id == "native_rf_quality_to_comm_downlink":
                observed = any(
                    float(row.get("comm.gated_cnr", 0.0)) > 0.0 and float(row.get("comm.downlink_bps", 0.0)) > 0.0
                    for row in rows
                ) or any(
                    float(row.get("comm.gated_cnr", 0.0)) <= 0.0 and float(row.get("comm.downlink_bps", 0.0)) == 0.0
                    for row in rows
                )
            elif coupling_id == "thermal_safety_to_payload_comm_gate":
                observed = any(
                    int(row.get("thermal.safe", 1)) == 0
                    and int(row.get("payload.active", 1)) == 0
                    and int(row.get("comm.active", 1)) == 0
                    for row in rows
                )
            else:
                observed = False
            items.append({"coupling_id": coupling_id, "status": "OBSERVED_SINGLE_RUN" if observed else "NOT_EXERCISED", "evidence_source": "telemetry"})
            continue
        health = dict(bridge_health.get(bridge_name, {}))
        if health.get("status") == "FAIL" or not health.get("directional_response_pass", True):
            status = "FAIL"
        elif coupling_id in {"eps_pdu_to_payload_activity", "eps_pdu_to_comm_activity", "eps_pdu_to_thermal_heaters"}:
            status = "OBSERVED_SINGLE_RUN" if health.get("enabled_transition_observed") else "NOT_EXERCISED"
        else:
            status = "OBSERVED_SINGLE_RUN" if health.get("stimulus_observed") else "NOT_EXERCISED"
        items.append({"coupling_id": coupling_id, "status": status, "evidence_source": f"bridge:{bridge_name}", "bridge_health": health})
    overall = (
        "FAIL"
        if any(item["status"] == "FAIL" for item in items)
        else ("SINGLE_RUN_OBSERVED" if items and all(item["status"] == "OBSERVED_SINGLE_RUN" for item in items) else "INCONCLUSIVE")
    )
    return {
        "status": overall,
        "requirements": items,
        "claim_scope": "single_run_observation_not_paired_causal_proof",
        "paired_run_required_for_causal_pass": True,
    }



def _overlay_exact_thermal_trace(rows: list[dict[str, Any]], thermal_network: Any, *, tolerance_s: float) -> None:
    """Replace integer DeviceStatus temperature recordings with exact node trace values."""
    trace = list(getattr(thermal_network, "trace", ()))
    if not rows or not trace:
        return
    by_node: dict[str, list[Any]] = {}
    for item in trace:
        by_node.setdefault(str(getattr(item, "node_name", "")), []).append(item)
    for items in by_node.values():
        items.sort(key=lambda item: float(getattr(item, "time_s", 0.0)))
    for row in rows:
        time_s = float(row.get("time_s", 0.0))
        for node_name, items in by_node.items():
            nearest = min(items, key=lambda item: abs(float(getattr(item, "time_s", 0.0)) - time_s))
            if abs(float(getattr(nearest, "time_s", 0.0)) - time_s) > tolerance_s:
                continue
            temp_k = float(getattr(nearest, "temp_k", 0.0))
            row[f"thermal.{node_name}_temp_k"] = temp_k
            row[f"thermal.{node_name}_temp_c"] = temp_k - 273.15
            row[f"thermal.{node_name}_safe"] = int(bool(getattr(nearest, "thermal_safe", False)))
            row[f"thermal.{node_name}_heat_input_w"] = float(getattr(nearest, "heat_input_w", 0.0))
            row[f"thermal.{node_name}_radiator_rejected_w"] = float(
                getattr(nearest, "radiator_rejected_w", 0.0)
            )
            row[f"thermal.{node_name}_radiator_on"] = int(
                bool(getattr(nearest, "radiator_on", False))
            )
        if "thermal.payload_temp_k" in row:
            row["thermal.payload_temp_k"] = float(row.get("thermal.payload_temp_k", row["thermal.payload_temp_k"]))
            row["thermal.payload_temp_c"] = float(row["thermal.payload_temp_k"]) - 273.15


def _active_event_ratio(
    events: tuple[BSKEventSpec, ...],
    time_s: float,
    *,
    effect: str,
    parameter_names: tuple[str, ...],
    default: float = 1.0,
    active_default: float | None = None,
) -> float:
    """Return the most restrictive active event ratio without inventing values."""
    ratio = float(default)
    for event in events:
        if event.effect != effect or not event.active_at(time_s):
            continue
        value = None
        for name in parameter_names:
            if name in event.parameters:
                value = event.parameters.get(name)
                break
        if value is None and "loss_fraction" in event.parameters:
            value = 1.0 - float(event.parameters.get("loss_fraction", 0.0))
        if value is None and "loss_pct" in event.parameters:
            value = 1.0 - float(event.parameters.get("loss_pct", 0.0)) / 100.0
        if value is None and active_default is not None:
            value = active_default
        try:
            ratio = min(ratio, max(0.0, min(float(value), 1.0)))
        except (TypeError, ValueError):
            continue
    return ratio


def _build_unified_rows(
    recorder_map: Mapping[str, Any],
    cfg: "UnifiedRuntimeConfig",
    sample_interval_s: float,
    *,
    start_index: int = 0,
) -> list[dict[str, Any]]:
    from Basilisk.utilities import macros  # type: ignore
    rows: list[dict[str, Any]] = []
    times = recorder_map["state"].times()
    if start_index < 0 or start_index > len(times):
        raise ValueError("start_index must be within the recorder range")
    for index in range(start_index, len(times)):
        nanos = times[index]
        state = recorder_map["state"]
        guide = recorder_map["guid"]
        rw_rec = recorder_map["rw"]
        position = state.r_BN_N[index]
        row: dict[str, Any] = {
            "time_s": float(nanos) * macros.NANO2SEC,
            "orbit.radius_m": _norm3(position),
            "orbit.phase_rad": math.atan2(float(position[1]), float(position[0])),
            "orbit.eclipse_factor": float(recorder_map["eclipse"].shadowFactor[index]),
            "adcs.pointing_error_deg": _pointing_error_deg(guide.sigma_BR[index]),
            "adcs.body_rate_rad_s_x": float(recorder_map["imu"].AngVelPlatform[index][0]),
            "adcs.body_rate_rad_s_y": float(recorder_map["imu"].AngVelPlatform[index][1]),
            "adcs.body_rate_rad_s_z": float(recorder_map["imu"].AngVelPlatform[index][2]),
            "adcs.sensor.raw_gyro_rad_s_x": float(recorder_map["imu_raw"].AngVelPlatform[index][0]),
            "adcs.sensor.raw_gyro_rad_s_y": float(recorder_map["imu_raw"].AngVelPlatform[index][1]),
            "adcs.sensor.raw_gyro_rad_s_z": float(recorder_map["imu_raw"].AngVelPlatform[index][2]),
            "adcs.sensor.raw_accel_m_s2_x": float(recorder_map["imu_raw"].AccelPlatform[index][0]),
            "adcs.sensor.raw_accel_m_s2_y": float(recorder_map["imu_raw"].AccelPlatform[index][1]),
            "adcs.sensor.raw_accel_m_s2_z": float(recorder_map["imu_raw"].AccelPlatform[index][2]),
            "adcs.sensor.gyro_measured_rad_s_x": float(recorder_map["imu"].AngVelPlatform[index][0]),
            "adcs.sensor.gyro_measured_rad_s_y": float(recorder_map["imu"].AngVelPlatform[index][1]),
            "adcs.sensor.gyro_measured_rad_s_z": float(recorder_map["imu"].AngVelPlatform[index][2]),
            "adcs.sensor.gyro_bias_rad_s_x": float(recorder_map["imu_bias"].AngVelPlatform[index][0]),
            "adcs.sensor.gyro_bias_rad_s_y": float(recorder_map["imu_bias"].AngVelPlatform[index][1]),
            "adcs.sensor.gyro_bias_rad_s_z": float(recorder_map["imu_bias"].AngVelPlatform[index][2]),
            "adcs.sensor.gyro_noise_rad_s_x": float(recorder_map["imu_noise"].AngVelPlatform[index][0]),
            "adcs.sensor.gyro_noise_rad_s_y": float(recorder_map["imu_noise"].AngVelPlatform[index][1]),
            "adcs.sensor.gyro_noise_rad_s_z": float(recorder_map["imu_noise"].AngVelPlatform[index][2]),
            "adcs.sensor.accel_measured_m_s2_x": float(recorder_map["imu"].AccelPlatform[index][0]),
            "adcs.sensor.accel_measured_m_s2_y": float(recorder_map["imu"].AccelPlatform[index][1]),
            "adcs.sensor.accel_measured_m_s2_z": float(recorder_map["imu"].AccelPlatform[index][2]),
            "adcs.sensor.accel_bias_m_s2_x": float(recorder_map["imu_bias"].AccelPlatform[index][0]),
            "adcs.sensor.accel_bias_m_s2_y": float(recorder_map["imu_bias"].AccelPlatform[index][1]),
            "adcs.sensor.accel_bias_m_s2_z": float(recorder_map["imu_bias"].AccelPlatform[index][2]),
            "adcs.sensor.accel_noise_m_s2_x": float(recorder_map["imu_noise"].AccelPlatform[index][0]),
            "adcs.sensor.accel_noise_m_s2_y": float(recorder_map["imu_noise"].AccelPlatform[index][1]),
            "adcs.sensor.accel_noise_m_s2_z": float(recorder_map["imu_noise"].AccelPlatform[index][2]),
            "adcs.sensor.gyro_bias_norm_rad_s": _norm3(recorder_map["imu_bias"].AngVelPlatform[index]),
            "adcs.sensor.gyro_noise_norm_rad_s": _norm3(recorder_map["imu_noise"].AngVelPlatform[index]),
            "adcs.rw.speed_rad_s_0": float(rw_rec.wheelSpeeds[index][0]),
            "adcs.rw.speed_rad_s_1": float(rw_rec.wheelSpeeds[index][1]),
            "adcs.rw.speed_rad_s_2": float(rw_rec.wheelSpeeds[index][2]),
            "adcs.rw.measured_speed_rad_s_0": float(recorder_map["rw_measured"].wheelSpeeds[index][0]),
            "adcs.rw.measured_speed_rad_s_1": float(recorder_map["rw_measured"].wheelSpeeds[index][1]),
            "adcs.rw.measured_speed_rad_s_2": float(recorder_map["rw_measured"].wheelSpeeds[index][2]),
            "adcs.rw.command_torque_nm_0": float(recorder_map["rw_command_raw"].motorTorque[index][0]),
            "adcs.rw.command_torque_nm_1": float(recorder_map["rw_command_raw"].motorTorque[index][1]),
            "adcs.rw.command_torque_nm_2": float(recorder_map["rw_command_raw"].motorTorque[index][2]),
            "adcs.rw.motor_current_a_0": max(-float(cfg.values.get("rw_motor_current_limit_a", 10.0)), min(float(cfg.values.get("rw_motor_current_limit_a", 10.0)), float(recorder_map["rw_command_raw"].motorTorque[index][0]) / max(float(cfg.values.get("rw_motor_torque_constant_nm_per_a", 0.02)), 1.0e-12))),
            "adcs.rw.motor_current_a_1": max(-float(cfg.values.get("rw_motor_current_limit_a", 10.0)), min(float(cfg.values.get("rw_motor_current_limit_a", 10.0)), float(recorder_map["rw_command_raw"].motorTorque[index][1]) / max(float(cfg.values.get("rw_motor_torque_constant_nm_per_a", 0.02)), 1.0e-12))),
            "adcs.rw.motor_current_a_2": max(-float(cfg.values.get("rw_motor_current_limit_a", 10.0)), min(float(cfg.values.get("rw_motor_current_limit_a", 10.0)), float(recorder_map["rw_command_raw"].motorTorque[index][2]) / max(float(cfg.values.get("rw_motor_torque_constant_nm_per_a", 0.02)), 1.0e-12))),
            "adcs.control.applied_torque_nm_0": float(recorder_map["rw_command_applied"].motorTorque[index][0]),
            "adcs.control.applied_torque_nm_1": float(recorder_map["rw_command_applied"].motorTorque[index][1]),
            "adcs.control.applied_torque_nm_2": float(recorder_map["rw_command_applied"].motorTorque[index][2]),
            "adcs.rw.effective_drag_nms_0": float(recorder_map["rw_effective_drag"].motorTorque[index][0]),
            "adcs.rw.effective_drag_nms_1": float(recorder_map["rw_effective_drag"].motorTorque[index][1]),
            "adcs.rw.effective_drag_nms_2": float(recorder_map["rw_effective_drag"].motorTorque[index][2]),
            "adcs.rw.effective_max_torque_nm_0": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][0]),
            "adcs.rw.effective_max_torque_nm_1": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][1]),
            "adcs.rw.effective_max_torque_nm_2": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][2]),
            "adcs.rw.effective_torque_ratio_0": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][0]) / max(abs(float(cfg.values.get("rw_max_torque_nm_0", cfg.values.get("rw_max_torque_nm", 0.2)))), 1.0e-12),
            "adcs.rw.effective_torque_ratio_1": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][1]) / max(abs(float(cfg.values.get("rw_max_torque_nm_1", cfg.values.get("rw_max_torque_nm", 0.2)))), 1.0e-12),
            "adcs.rw.effective_torque_ratio_2": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][2]) / max(abs(float(cfg.values.get("rw_max_torque_nm_2", cfg.values.get("rw_max_torque_nm", 0.2)))), 1.0e-12),
            "adcs.rw.effective_max_speed_rad_s_0": float(recorder_map["rw_effective_speed_limit"].wheelSpeeds[index][0]),
            "adcs.rw.effective_max_speed_rad_s_1": float(recorder_map["rw_effective_speed_limit"].wheelSpeeds[index][1]),
            "adcs.rw.effective_max_speed_rad_s_2": float(recorder_map["rw_effective_speed_limit"].wheelSpeeds[index][2]),
            "adcs.star_tracker.q0": float(recorder_map["star"].qInrtl2Case[index][0]),
            "adcs.star_tracker.q1": float(recorder_map["star"].qInrtl2Case[index][1]),
            "adcs.star_tracker.q2": float(recorder_map["star"].qInrtl2Case[index][2]),
            "adcs.star_tracker.q3": float(recorder_map["star"].qInrtl2Case[index][3]),
            "adcs.star_tracker.raw_q0": float(recorder_map["star_raw"].qInrtl2Case[index][0]),
            "adcs.star_tracker.raw_q1": float(recorder_map["star_raw"].qInrtl2Case[index][1]),
            "adcs.star_tracker.raw_q2": float(recorder_map["star_raw"].qInrtl2Case[index][2]),
            "adcs.star_tracker.raw_q3": float(recorder_map["star_raw"].qInrtl2Case[index][3]),
            "adcs.star_tracker.valid": int(recorder_map["star_valid"].deviceStatus[index]),
            "adcs.magnetometer.tesla_x": float(recorder_map["mag"].tam_S[index][0]),
            "adcs.magnetometer.tesla_y": float(recorder_map["mag"].tam_S[index][1]),
            "adcs.magnetometer.tesla_z": float(recorder_map["mag"].tam_S[index][2]),
            "adcs.sun_sensor.cosine_output": float(recorder_map["sun_sensor"].OutputData[index]),
            "adcs.sun_sensor.direction_b_x": float(recorder_map["sun_vector"].tam_S[index][0]),
            "adcs.sun_sensor.direction_b_y": float(recorder_map["sun_vector"].tam_S[index][1]),
            "adcs.sun_sensor.direction_b_z": float(recorder_map["sun_vector"].tam_S[index][2]),
            "adcs.sun_sensor.raw_direction_b_x": float(recorder_map["sun_vector_raw"].tam_S[index][0]),
            "adcs.sun_sensor.raw_direction_b_y": float(recorder_map["sun_vector_raw"].tam_S[index][1]),
            "adcs.sun_sensor.raw_direction_b_z": float(recorder_map["sun_vector_raw"].tam_S[index][2]),
            "adcs.sun_sensor.valid": int(recorder_map["sun_valid"].deviceStatus[index]),
            "adcs.earth_sensor.direction_b_x": float(recorder_map["earth_sensor"].tam_S[index][0]),
            "adcs.earth_sensor.direction_b_y": float(recorder_map["earth_sensor"].tam_S[index][1]),
            "adcs.earth_sensor.direction_b_z": float(recorder_map["earth_sensor"].tam_S[index][2]),
            "adcs.fusion.source_mode": int(recorder_map["fusion_source_mode"].deviceStatus[index]),
        }
        wheel_count = len(_reaction_wheel_axes(str(cfg.values.get("rw_configuration", "orthogonal_3"))))
        torque_constant = max(float(cfg.values.get("rw_motor_torque_constant_nm_per_a", 0.02)), 1.0e-12)
        current_limit = float(cfg.values.get("rw_motor_current_limit_a", 10.0))
        for wheel_index in range(3, wheel_count):
            nominal_torque = max(abs(float(cfg.values.get(
                f"rw_max_torque_nm_{wheel_index}", cfg.values.get("rw_max_torque_nm", 0.2)
            ))), 1.0e-12)
            raw_torque = float(recorder_map["rw_command_raw"].motorTorque[index][wheel_index])
            row.update({
                f"adcs.rw.speed_rad_s_{wheel_index}": float(rw_rec.wheelSpeeds[index][wheel_index]),
                f"adcs.rw.measured_speed_rad_s_{wheel_index}": float(recorder_map["rw_measured"].wheelSpeeds[index][wheel_index]),
                f"adcs.rw.command_torque_nm_{wheel_index}": raw_torque,
                f"adcs.rw.motor_current_a_{wheel_index}": max(-current_limit, min(current_limit, raw_torque / torque_constant)),
                f"adcs.control.applied_torque_nm_{wheel_index}": float(recorder_map["rw_command_applied"].motorTorque[index][wheel_index]),
                f"adcs.rw.effective_drag_nms_{wheel_index}": float(recorder_map["rw_effective_drag"].motorTorque[index][wheel_index]),
                f"adcs.rw.effective_max_torque_nm_{wheel_index}": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][wheel_index]),
                f"adcs.rw.effective_torque_ratio_{wheel_index}": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][wheel_index]) / nominal_torque,
                f"adcs.rw.effective_max_speed_rad_s_{wheel_index}": float(recorder_map["rw_effective_speed_limit"].wheelSpeeds[index][wheel_index]),
            })
        row["label.fault_active"] = bool(recorder_map["event_fault"].deviceStatus[index])
        row["label.degradation_active"] = bool(recorder_map["event_degradation"].deviceStatus[index])
        row["label.constraint_active"] = bool(recorder_map["event_constraint"].deviceStatus[index])
        active_effects = [event.effect for event in cfg.events if event.active_at(row["time_s"])]
        row["event.active_effects"] = ",".join(active_effects)
        row["adcs.event.active_effects"] = row["event.active_effects"]
        if not cfg.adcs_only:
            battery = recorder_map["battery"]
            storage = recorder_map["storage"]
            capacity = max(float(battery.storageCapacity[index]), 1.0e-12)
            payload_active = int(recorder_map["payload_status"].deviceStatus[index])
            downlink_active = int(recorder_map["downlink_status"].deviceStatus[index])
            row.update({
                "eps.solar_array_power_w": float(recorder_map["panel"].netPower[index]),
                "eps.battery_energy_j": float(battery.storageLevel[index]),
                "eps.battery_capacity_j": capacity,
                "eps.effective_battery_capacity_wh": capacity / 3600.0,
                "eps.battery_capacity_ratio": capacity / max(float(cfg.values.get("battery_capacity_wh", 160.0)) * 3600.0, 1.0e-12),
                "eps.solar_array_efficiency_ratio": _active_event_ratio(
                    cfg.events, row["time_s"], effect="solar_panel_efficiency_loss",
                    parameter_names=("remaining_efficiency_ratio", "remaining_ratio"),
                    active_default=0.5,
                ),
                "thermal.radiator_rejection_ratio": _active_event_ratio(
                    cfg.events, row["time_s"], effect="thermal_radiator_rejection_loss",
                    parameter_names=("remaining_rejection_ratio", "remaining_ratio"),
                    active_default=0.5,
                ),
                "eps.battery_energy_wh": float(battery.storageLevel[index]) / 3600.0,
                "eps.battery_soc": float(battery.storageLevel[index]) / capacity,
                "eps.net_power_w": float(battery.currentNetPower[index]),
                "eps.loads.adcs_power_w": max(0.0, -float(recorder_map["adcs_power"].netPower[index])),
                "eps.loads.payload_power_w": max(0.0, -float(recorder_map["payload_power"].netPower[index])),
                "eps.loads.comm_power_w": max(0.0, -float(recorder_map["downlink_power"].netPower[index])),
                "eps.loads.heater_power_w": max(0.0, -float(recorder_map["heater_power"].netPower[index])),
                "eps.pdu.payload_enabled": int(recorder_map["pdu_payload_status"].deviceStatus[index]),
                "eps.pdu.adcs_enabled": int(recorder_map["pdu_adcs_status"].deviceStatus[index]),
                "eps.pdu.comm_enabled": int(recorder_map["pdu_comm_status"].deviceStatus[index]),
                "eps.pdu.heater_enabled": int(recorder_map["pdu_heater_status"].deviceStatus[index]),
                "payload.active": payload_active,
                "payload.generated_bps": max(float(recorder_map["instrument"].baudRate[index]), 0.0),
                "payload.generated_data_bits": max(float(recorder_map["instrument"].baudRate[index]), 0.0) * sample_interval_s,
                "comm.command_permitted": downlink_active,
                "comm.geometric_access": int(recorder_map["ground_access"].hasAccess[index]),
                "comm.native_cnr": max(float(recorder_map["native_link_budget"].CNR1[index]), float(recorder_map["native_link_budget"].CNR2[index])),
                "comm.gated_cnr": max(float(recorder_map["gated_link_budget"].CNR1[index]), float(recorder_map["gated_link_budget"].CNR2[index])),
                "comm.active": int(recorder_map["downlink_handling"].linkActive[index]),
                "comm.downlink_bps": max(float(recorder_map["downlink_handling"].deliveredDataRate[index]), 0.0),
                "comm.downlink_rate_bps": max(float(recorder_map["downlink_handling"].deliveredDataRate[index]), 0.0),
                "comm.attempted_rate_bps": max(float(recorder_map["downlink_handling"].attemptedDataRate[index]), 0.0),
                "comm.dropped_rate_bps": max(float(recorder_map["downlink_handling"].droppedDataRate[index]), 0.0),
                "comm.ber": max(float(recorder_map["downlink_handling"].ber[index]), 0.0),
                "comm.per": max(float(recorder_map["downlink_handling"].per[index]), 0.0),
                "comm.downlinked_bits": max(float(recorder_map["downlink_handling"].deliveredDataRate[index]), 0.0) * sample_interval_s,
                "label.power_safe_mode_engaged": bool(recorder_map["safe_mode_status"].deviceStatus[index]),
                "data.storage_bits": float(storage.storageLevel[index]),
                "data.storage_capacity_bits": float(storage.storageCapacity[index]),
                "data.net_baud_bps": float(storage.currentNetBaud[index]),
                "thermal.safe": int(recorder_map["thermal_status"].deviceStatus[index]),
                "thermal.payload_temp_k": float(recorder_map["thermal_temp_payload"].deviceStatus[index]),
                "thermal.payload_temp_c": float(recorder_map["thermal_temp_payload"].deviceStatus[index]) - 273.15,
            })
            for thermal_node_name in ("battery", "electronics", "payload", "adcs", "structure", "solar_panel", "comm", "propulsion"):
                temp_key = f"thermal_temp_{thermal_node_name}"
                safe_key = f"thermal_safe_{thermal_node_name}"
                if temp_key in recorder_map:
                    row[f"thermal.{thermal_node_name}_temp_k"] = float(recorder_map[temp_key].deviceStatus[index])
                    row[f"thermal.{thermal_node_name}_safe"] = int(recorder_map[safe_key].deviceStatus[index])
            if "propulsion_fuel" in recorder_map:
                fuel_rec = recorder_map["propulsion_fuel"]
                thrust_forces = []
                thrust_factors = []
                for key in sorted(name for name in recorder_map if name.startswith("propulsion_thruster_")):
                    thruster_rec = recorder_map[key]
                    thrust_forces.append(float(thruster_rec.thrustForce[index]))
                    thrust_factors.append(float(thruster_rec.thrustFactor[index]))
                row.update({
                    "propulsion.enabled": 1,
                    "propulsion.burn_active": int(recorder_map["propulsion_burn_status"].deviceStatus[index]),
                    "propulsion.burn_permitted": int(recorder_map["propulsion_permission"].deviceStatus[index]),
                    "propulsion.fuel_mass_kg": float(fuel_rec.fuelMass[index]),
                    "propulsion.fuel_mass_dot_kg_s": float(fuel_rec.fuelMassDot[index]),
                    "propulsion.total_thrust_n": sum(thrust_forces),
                    "propulsion.max_thrust_factor": max(thrust_factors, default=0.0),
                    "propulsion.electrical_power_w": max(0.0, -float(recorder_map["propulsion_power"].netPower[index])),
                    "propulsion.thrust_force_n_0": thrust_forces[0] if thrust_forces else 0.0,
                    "propulsion.thrust_force_n_1": thrust_forces[1] if len(thrust_forces) > 1 else 0.0,
                })
            else:
                row["propulsion.enabled"] = 0
        rows.append(row)

    return rows


@dataclass(frozen=True)
class UnifiedRuntimeConfig:
    capability_id: str
    duration_s: float
    step_s: float
    sample_s: float
    adcs_only: bool
    values: dict[str, Any]
    events: tuple[BSKEventSpec, ...]
    required_couplings: tuple[str, ...] = ()
    telemetry_streams: tuple[dict[str, Any], ...] = ()
    execution_stops_s: tuple[float, ...] = ()
    execution_controller: Any = None
    # Only opt-in persistent interactive sessions may extend beyond setup duration.
    interactive_unbounded: bool = False
    # Only the trusted local native Vizard exporter may set this. Not a TaskSpec
    # parameter or HTTP API path. Disabled in persistent/interactive sessions.
    vizard_save_file: str | None = None


class UnifiedNativeRuntime:
    """Build and execute the native mixed-origin Basilisk model graph."""

    OFFICIAL_MODULES = (
        "spacecraft", "reaction_wheels", "eclipse", "star_tracker", "imu", "coarse_sun_sensor",
        "magnetic_field", "magnetometer", "inertial_guidance", "attitude_error",
        "mrp_feedback", "rw_motor_torque",
    )
    WHOLE_OFFICIAL_MODULES = (
        "solar_panel", "bus_power", "adcs_power", "payload_power", "downlink_power",
        "battery", "payload_instrument", "transmitter", "storage", "payload_thermal",
    )
    PROJECT_MODULES = (
        "project_native_event_status", "project_imu_fault_injector",
        "project_adcs_sensor_fusion", "project_rw_command_fault_manager",
        "project_star_tracker_fault_injector", "project_sun_vector_fault_injector",
        "project_power_data_mode_gate", "project_whole_parameter_event_controller",
    )

    def __init__(
        self, config: UnifiedRuntimeConfig, *,
        _test_only_fresh_native_graph_probe: Any = None,
    ) -> None:
        self.config = config
        # Isolated test process only. This hook never appears in TaskSpec,
        # the persistent runtime, HTTP handlers or a recoverable checkpoint.
        if _test_only_fresh_native_graph_probe is not None:
            if (
                not callable(_test_only_fresh_native_graph_probe)
                or config.execution_controller is not None
                or config.interactive_unbounded
            ):
                raise ValueError("R9D_NATIVE_TEST_PROBE_NOT_PERMITTED")
        self._test_only_fresh_native_graph_probe = _test_only_fresh_native_graph_probe

    def run(self) -> SimulationResult:
        from Basilisk.architecture import messaging  # type: ignore
        from .project_native_modules import (
            AdcsSensorFusion,
            EarthHorizonSensor,
            ImuFaultInjector,
            NativeEventStatus,
            NativeRfDownlinkGate,
            PowerDataModeGate,
            PropulsionBurnController,
            ReactionWheelCommandFaultManager,
            ReactionWheelSpeedFaultInjector,
            StarTrackerFaultInjector,
            SunDirectionSensor,
            SunVectorFaultInjector,
            WholeSpacecraftParameterEventController,
        )
        from Basilisk.fswAlgorithms import attTrackingError, inertial3D, mrpFeedback, rwMotorTorque  # type: ignore
        from Basilisk.simulation import (  # type: ignore
            coarseSunSensor,
            eclipse,
            imuSensor,
            magneticFieldCenteredDipole,
            magnetometer,
            reactionWheelStateEffector,
            simpleBattery,
            simpleInstrument,
            simplePowerSink,
            simpleSolarPanel,
            simpleStorageUnit,
            spacecraft,
            starTracker,
        )
        from Basilisk.utilities import (  # type: ignore
            RigidBodyKinematics, SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody, simIncludeRW,
        )

        cfg = self.config
        if cfg.vizard_save_file is not None and (cfg.execution_controller is not None or cfg.interactive_unbounded):
            raise ValueError("VIZARD_NATIVE_RECORDING_FINITE_ONLY")
        values = cfg.values
        sim = SimulationBaseClass.SimBaseClass()
        process_name = "UnifiedNativeProcess"
        task_name = "UnifiedNativeTask"
        process = sim.CreateNewProcess(process_name)
        native_task = sim.CreateNewTask(task_name, macros.sec2nano(cfg.step_s))
        process.addTask(native_task)
        instantiated: list[dict[str, str]] = []
        connections: list[str] = []
        record_sources: list[str] = []

        def add(model: Any, tag: str, source: str, priority: int) -> Any:
            model.ModelTag = tag
            sim.AddModelToTask(task_name, model, None, priority)
            instantiated.append({"tag": tag, "source": source})
            return model

        def connected(source: str, target: str) -> None:
            connections.append(f"{source}->{target}")

        event_status = NativeEventStatus(cfg.events)
        add(event_status, "project_native_event_status", "basilisk_project_native", 110)

        sc = spacecraft.Spacecraft()
        sc.hub.mHub = float(values.get("spacecraft_mass_kg", 100.0))
        # Configurable diagonal rigid-body inertia in the body B frame.
        # Preserve the historical nominal values when omitted; mass is a
        # separate independent input and never silently rescales inertia.
        inertia = [float(values.get(f"spacecraft_inertia_{axis}_kg_m2", default))
                   for axis, default in (("xx", 100.0), ("yy", 80.0), ("zz", 60.0))]
        if any(not math.isfinite(moment) or not 0. < moment <= 10000. for moment in inertia):
            raise ValueError("NATIVE_SPACECRAFT_PRINCIPAL_INERTIA_INVALID")
        xx, yy, zz = inertia
        if xx >= yy + zz or yy >= xx + zz or zz >= xx + yy:
            raise ValueError("NATIVE_SPACECRAFT_PRINCIPAL_INERTIA_TRIANGLE_VIOLATION")
        sc.hub.IHubPntBc_B = [[xx, 0.0, 0.0], [0.0, yy, 0.0], [0.0, 0.0, zz]]
        gravity_factory = simIncludeGravBody.gravBodyFactory()
        earth = gravity_factory.createEarth()
        earth.isCentralBody = True
        gravity_factory.addBodiesTo(sc)
        elements = orbitalMotion.ClassicElements()
        elements.a = float(values.get("orbit_radius_m", 7_000_000.0))
        elements.e = float(values.get("eccentricity", 0.001))
        elements.i = math.radians(float(values.get("inclination_deg", 35.0)))
        elements.Omega = 0.0
        elements.omega = 0.0
        elements.f = float(values.get("initial_true_anomaly_rad", 0.0))
        position, velocity = orbitalMotion.elem2rv(earth.mu, elements)
        sc.hub.r_CN_NInit = position
        sc.hub.v_CN_NInit = velocity
        initial_error_deg = float(values.get("initial_pointing_error_deg", 8.0))
        sc.hub.sigma_BNInit = [math.tan(math.radians(initial_error_deg) / 4.0), 0.0, 0.0]
        sc.hub.omega_BN_BInit = [0.01, -0.004, 0.002]
        add(sc, "spacecraft", "basilisk_official_native", 100)

        rw = reactionWheelStateEffector.ReactionWheelStateEffector()
        rw_factory = simIncludeRW.rwFactory()
        rw_axes = _reaction_wheel_axes(str(values.get("rw_configuration", "orthogonal_3")))
        # Native physical installation geometry: do not rewrite the official
        # axis/point pair into a synthetic moment or a telemetry-only setting.
        from .rw_geometry import resolve_rw_geometry
        rw_axes, rw_positions = resolve_rw_geometry(values, rw_axes)
        # R8-C: pass validated initial speeds and torque limits into the
        # *actual* Basilisk reaction-wheel factory (not merely into the
        # downstream fault manager or synthetic telemetry).
        # The downstream native command clamp uses the same value below.
        rw_hardware_u_max = float(values.get("rw_max_torque_nm", 0.2))
        if not math.isfinite(rw_hardware_u_max) or not 0.0 < rw_hardware_u_max <= 0.2:
            raise ValueError("NATIVE_RW_HARDWARE_TORQUE_LIMIT_INVALID")
        if len(rw_axes) != 4 and values.get("rw_max_torque_nm_3") is not None:
            raise ValueError("NATIVE_RW4_TORQUE_LIMIT_REQUIRES_PYRAMID_4")
        rw_hardware_u_max_by_wheel: list[float] = []
        for wheel_index in range(len(rw_axes)):
            raw = values.get(f"rw_max_torque_nm_{wheel_index}", rw_hardware_u_max)
            if type(raw) not in (int, float):
                raise ValueError("NATIVE_RW_PER_WHEEL_TORQUE_LIMIT_INVALID")
            limit = float(raw)
            if not math.isfinite(limit) or not 0.0 < limit <= 0.2:
                raise ValueError("NATIVE_RW_PER_WHEEL_TORQUE_LIMIT_INVALID")
            rw_hardware_u_max_by_wheel.append(limit)
        rw_friction_mode = str(values.get("rw_friction_mode", "disabled"))
        rw_coulomb = float(values.get("rw_coulomb_friction_nm", 0.0))
        rw_viscous = float(values.get("rw_viscous_friction_nms", 0.0))
        rw_mount_radius = float(values.get("rw_mount_radius_m", 0.0))
        if rw_friction_mode not in {"disabled", "coulomb_viscous", "per_wheel"}:
            raise ValueError("NATIVE_RW_FRICTION_MODE_INVALID")
        if any(not math.isfinite(x) for x in (rw_coulomb, rw_viscous, rw_mount_radius)):
            raise ValueError("NATIVE_RW_FRICTION_OR_MOUNT_NOT_FINITE")
        if not (0 <= rw_coulomb <= .01 and 0 <= rw_viscous <= .01 and 0 <= rw_mount_radius <= .25):
            raise ValueError("NATIVE_RW_FRICTION_OR_MOUNT_OUT_OF_RANGE")
        if rw_friction_mode == "disabled" and (rw_coulomb > 0 or rw_viscous > 0):
            raise ValueError("NATIVE_RW_FRICTION_DISABLED_REQUIRES_ZERO")
        if rw_friction_mode == "coulomb_viscous" and rw_coulomb == rw_viscous == 0:
            raise ValueError("NATIVE_RW_FRICTION_ENABLED_REQUIRES_NONZERO")
        # Validate every physically instantiated wheel before constructing any
        # Basilisk hardware: three-wheel configurations must never silently
        # accept a nonexistent RW4 parameter (even if its value is zero).
        if len(rw_axes) != 4 and any(
            values.get(f"rw_{kind}_friction_{suffix}_3") is not None
            for kind, suffix in (("coulomb", "nm"), ("viscous", "nms"))
        ):
            raise ValueError("NATIVE_RW4_FRICTION_REQUIRES_PYRAMID_4")
        per_wheel_coefficients: list[tuple[float, float]] = []
        for wheel_index in range(len(rw_axes)):
            coefficients: list[float] = []
            for kind, suffix in (("coulomb", "nm"), ("viscous", "nms")):
                raw = values.get(f"rw_{kind}_friction_{suffix}_{wheel_index}", 0.0)
                try:
                    value = float(raw)
                except (TypeError, ValueError, OverflowError) as exc:
                    raise ValueError("NATIVE_RW_PER_WHEEL_FRICTION_INVALID") from exc
                if not math.isfinite(value) or not 0. <= value <= 0.01:
                    raise ValueError("NATIVE_RW_PER_WHEEL_FRICTION_INVALID")
                coefficients.append(value)
            per_wheel_coefficients.append((coefficients[0], coefficients[1]))
        if rw_friction_mode != "per_wheel" and any(
            c > 0. or v > 0. for c, v in per_wheel_coefficients
        ):
            raise ValueError("NATIVE_RW_PER_WHEEL_FRICTION_MODE_MISMATCH")
        if rw_friction_mode == "per_wheel":
            if rw_coulomb > 0. or rw_viscous > 0.:
                raise ValueError("NATIVE_RW_PER_WHEEL_FRICTION_FORBIDS_SHARED")
            if not any(c > 0. or v > 0. for c, v in per_wheel_coefficients):
                raise ValueError("NATIVE_RW_PER_WHEEL_FRICTION_REQUIRES_NONZERO")
        # Preserve the original frictionless nominal hardware and identical
        # legacy group-wide friction when the new mode is not requested.
        rw_actual_masses_kg: list[float] = []
        for index, axis in enumerate(rw_axes):
            if rw_friction_mode == "per_wheel":
                wheel_coulomb, wheel_viscous = per_wheel_coefficients[index]
                # Basilisk supports a frictionless wheel amongst other wheels
                # with friction; disabling it removes unexpected motor drag.
                use_wheel_friction = bool(wheel_coulomb or wheel_viscous)
            else:
                wheel_coulomb, wheel_viscous = rw_coulomb, rw_viscous
                use_wheel_friction = rw_friction_mode == "coulomb_viscous"
            rw_friction_kwargs = ({"useRWfriction": True, "fCoulomb": wheel_coulomb,
                                   "fStatic": 0.0, "betaStatic": -1.0,
                                   "cViscous": wheel_viscous}
                                  if use_wheel_friction else {"useRWfriction": False})
            # The simIncludeRW.rwFactory API uses RPM for Omega, whereas
            # physical telemetry and the operator schema use rad/s.
            # Retain the pre-R8-C baseline physical 100/200/300/400 RPM
            # when a speed was not explicitly specified.
            default_rpm = 100.0 * (index + 1)
            omega_init_rad_s = float(values.get(
                f"rw_initial_speed_rad_s_{index}", default_rpm * (2.0 * math.pi / 60.0),
            ))
            if not math.isfinite(omega_init_rad_s) or abs(omega_init_rad_s) > 500.0:
                raise ValueError("NATIVE_RW_INITIAL_SPEED_INVALID")
            omega_factory_rpm = omega_init_rad_s * (60.0 / (2.0 * math.pi))
            # HR16 allows exactly 50, 75, or 100 N*m*s. Other values cause
            # the upstream rwFactory to terminate the process; reject them
            # here before entering Basilisk. Configures physical Js=H/Omega_max.
            momentum_key = f"rw_max_momentum_nms_{index}"
            momentum_value = values.get(momentum_key, 50)
            if type(momentum_value) not in (int, float) or not math.isfinite(momentum_value) or momentum_value not in (50, 75, 100):
                raise ValueError("NATIVE_RW_HR16_MOMENTUM_UNSUPPORTED")
            official_rw_device = rw_factory.create(
                "Honeywell_HR16", list(axis), maxMomentum=float(momentum_value),
                Omega=omega_factory_rpm, u_max=rw_hardware_u_max_by_wheel[index],
                label=f"RW{index + 1}",
                rWB_B=list(rw_positions[index]),
                **rw_friction_kwargs,
            )
            # For BalancedWheels in pinned Basilisk 2.11, rotor mass is
            # assumed included in hub.mHub/IHub; it is NOT re-counted by the
            # effector when moving rWB_B. Use official wheel.mass for the
            # incremental rigid-body mass relocation below.
            rw_actual_masses_kg.append(float(official_rw_device.mass))
        if (values.get("rw_geometry_mode") == "per_wheel"
                and any(any(abs(v) > 0. for v in loc) for loc in rw_positions)):
            from .rw_geometry import relocated_rw_hub_mass_properties
            new_hub_com_b, new_hub_inertia_b = relocated_rw_hub_mass_properties(
                total_mass_kg=float(sc.hub.mHub),
                nominal_inertia_b=[[inertia[0], 0., 0.],
                                   [0., inertia[1], 0.],
                                   [0., 0., inertia[2]]],
                rw_masses_kg=rw_actual_masses_kg,
                rw_positions_b_m=rw_positions,
            )
            sc.hub.r_BcB_B = list(new_hub_com_b)
            sc.hub.IHubPntBc_B = [list(row) for row in new_hub_inertia_b]
        # RW4 parameters have no physical owner in a three-wheel graph;
        # reject them instead of silently dropping a requested hardware edit.
        if len(rw_axes) != 4 and values.get("rw_max_momentum_nms_3") is not None:
            raise ValueError("NATIVE_RW4_MOMENTUM_REQUIRES_PYRAMID_4")
        rw_factory.addToSpacecraft("reaction_wheels", rw, sc)
        add(rw, "reaction_wheels", "basilisk_official_native", 99)
        rw_config = rw_factory.getConfigMessage()
        connected("reaction_wheels", "spacecraft")

        sun_payload = messaging.SpicePlanetStateMsgPayload()
        sun_payload.PositionVector = [149_597_870_700.0, 0.0, 0.0]
        sun_payload.VelocityVector = [0.0, 0.0, 0.0]
        sun_payload.PlanetName = "sun"
        sun_msg = messaging.SpicePlanetStateMsg().write(sun_payload)
        earth_payload = messaging.SpicePlanetStateMsgPayload()
        earth_payload.PositionVector = [0.0, 0.0, 0.0]
        earth_payload.VelocityVector = [0.0, 0.0, 0.0]
        earth_payload.PlanetName = "earth"
        earth_msg = messaging.SpicePlanetStateMsg().write(earth_payload)

        eclipse_model = eclipse.Eclipse()
        eclipse_model.sunInMsg.subscribeTo(sun_msg)
        eclipse_model.addPlanetToModel(earth_msg)
        eclipse_model.addSpacecraftToModel(sc.scStateOutMsg)
        add(eclipse_model, "eclipse", "basilisk_official_native", 95)
        connected("spacecraft.scStateOutMsg", "eclipse")

        # Guard the shared random seed before the native star tracker, IMU
        # and project event injectors consume it. The SWIG RNGSeed is unsigned;
        # direct finite TaskSpecs must never allow negative/wrapped seeds.
        raw_sim_seed = values.get("simulation_seed", 0)
        if type(raw_sim_seed) is not int or not 0 <= raw_sim_seed <= 2147483647:
            raise ValueError("NATIVE_SIMULATION_SEED_INVALID")
        # Real Basilisk 3-2-1 body-to-case installation. Default (0,0,0)
        # remains identity, so existing whole-spacecraft runs are unchanged.
        tracker_euler_321 = [
            math.radians(float(values.get("star_tracker_mount_yaw_deg", 0.0))),
            math.radians(float(values.get("star_tracker_mount_pitch_deg", 0.0))),
            math.radians(float(values.get("star_tracker_mount_roll_deg", 0.0))),
        ]
        tracker = starTracker.StarTracker()
        tracker.dcm_CB = np.asarray(RigidBodyKinematics.euler3212C(tracker_euler_321)).tolist()
        star_noise_std_arcsec = float(values.get("star_tracker_noise_std_arcsec", 0.0))
        if not math.isfinite(star_noise_std_arcsec) or not 0 <= star_noise_std_arcsec <= 3600:
            raise ValueError("NATIVE_STAR_TRACKER_NOISE_STD_OUT_OF_RANGE")
        if star_noise_std_arcsec > 0:
            # Official PMatrix is the *sqrt covariance*, not a variance.
            # Explicit A=0 prevents earlier Basilisk versions from silently
            # interpreting the operator's white-noise sigma as a random walk.
            star_sigma_rad = math.radians(star_noise_std_arcsec / 3600.0)
            tracker.PMatrix = (np.eye(3) * star_sigma_rad).tolist()
            tracker.setAMatrix(np.zeros((3, 3)).tolist())
            tracker.RNGSeed = int(values.get("simulation_seed", 0))
        tracker.scStateInMsg.subscribeTo(sc.scStateOutMsg)
        add(tracker, "star_tracker", "basilisk_official_native", 90)
        connected("spacecraft.scStateOutMsg", "star_tracker")

        # Official IMU method accepts yaw, pitch, roll in radians. Sensor
        # location is a 3-D body-frame vector, not an Euler parameter.
        imu_mount_321 = [
            math.radians(float(values.get("imu_mount_yaw_deg", 0.0))),
            math.radians(float(values.get("imu_mount_pitch_deg", 0.0))),
            math.radians(float(values.get("imu_mount_roll_deg", 0.0))),
        ]
        imu = imuSensor.ImuSensor()
        imu.setBodyToPlatformDCM(*imu_mount_321)
        imu.sensorPos_B = [
            float(values.get("imu_position_b_x_m", 0.0)),
            float(values.get("imu_position_b_y_m", 0.0)),
            float(values.get("imu_position_b_z_m", 0.0)),
        ]
        # These are the official native sensor biases and saturation limits,
        # NOT the separate event-triggered project IMU fault injector.
        gyro_bias = [float(values.get(f"imu_gyro_bias_rad_s_{axis}", 0.0))
                     for axis in "xyz"]
        accel_bias = [float(values.get(f"imu_accel_bias_m_s2_{axis}", 0.0))
                      for axis in "xyz"]
        if not all(math.isfinite(v) for v in (*gyro_bias, *accel_bias)):
            raise ValueError("NATIVE_IMU_BIAS_NOT_FINITE")
        if any(abs(v) > 0.1 for v in gyro_bias) or any(abs(v) > 1.0 for v in accel_bias):
            raise ValueError("NATIVE_IMU_BIAS_OUT_OF_RANGE")
        imu.senRotBias = gyro_bias
        imu.senTransBias = accel_bias
        # Noise on the actual official Basilisk ImuSensor, distinct from
        # event-conditioned project ImuFaultInjector's base_noise_std_rad_s.
        # P is a square-root covariance (sigma), NOT variance (sigma^2).
        # Explicit A=0 is required with pinned Basilisk 2.11: its gyro
        # default is a random walk, not the operator-selected WHITE noise.
        native_gyro_sigma = values.get("imu_gyro_white_noise_std_rad_s", 0.)
        native_accel_sigma = values.get("imu_accel_white_noise_std_m_s2", 0.)
        if any(type(v) not in (int, float) for v in (native_gyro_sigma, native_accel_sigma)):
            raise ValueError("NATIVE_IMU_WHITE_NOISE_INVALID")
        native_gyro_sigma, native_accel_sigma = float(native_gyro_sigma), float(native_accel_sigma)
        if (not math.isfinite(native_gyro_sigma) or not 0. <= native_gyro_sigma <= .1
                or not math.isfinite(native_accel_sigma) or not 0. <= native_accel_sigma <= 1.):
            raise ValueError("NATIVE_IMU_WHITE_NOISE_INVALID")
        if native_gyro_sigma > 0. or native_accel_sigma > 0.:
            # No added noise or RNG state when both controls remain zero;
            # old scenarios retain their original native call graph.
            imu.RNGSeed = int(values.get("simulation_seed", 0))
            zero = np.zeros((3, 3)).tolist()
            if native_gyro_sigma > 0.:
                imu.PMatrixGyro = (np.eye(3) * native_gyro_sigma).tolist()
                imu.setAMatrixGyro(zero)
            if native_accel_sigma > 0.:
                imu.PMatrixAccel = (np.eye(3) * native_accel_sigma).tolist()
                imu.setAMatrixAccel(zero)
        gyro_max = float(values.get("imu_gyro_max_rad_s", 0.0))
        accel_max = float(values.get("imu_accel_max_m_s2", 0.0))
        if not all(math.isfinite(v) for v in (gyro_max, accel_max)) or not (
            0 <= gyro_max <= 1000 and 0 <= accel_max <= 1000
        ):
            raise ValueError("NATIVE_IMU_SATURATION_OUT_OF_RANGE")
        # Zero means 'do not overwrite the upstream module default', so the
        # old nominal graph retains exactly its existing saturation behavior.
        if gyro_max > 0:
            imu.senRotMax = gyro_max
        if accel_max > 0:
            imu.senTransMax = accel_max
        imu.scStateInMsg.subscribeTo(sc.scStateOutMsg)
        add(imu, "imu", "basilisk_official_native", 90)
        connected("spacecraft.scStateOutMsg", "imu")

        sun_sensor = coarseSunSensor.CoarseSunSensor()
        from .sun_sensor_geometry import resolve_sun_sensor_boresight
        sun_sensor.nHat_B = list(resolve_sun_sensor_boresight(values))
        fov_deg = values.get("sun_sensor_fov_deg", 80.0)
        if type(fov_deg) not in (int, float) or not math.isfinite(fov_deg) or not 0. < fov_deg <= 180.:
            raise ValueError("NATIVE_SUN_SENSOR_FOV_INVALID")
        sun_sensor.fov = math.radians(float(fov_deg))
        sun_sensor.stateInMsg.subscribeTo(sc.scStateOutMsg)
        sun_sensor.sunInMsg.subscribeTo(sun_msg)
        sun_sensor.sunEclipseInMsg.subscribeTo(eclipse_model.eclipseOutMsgs[0])
        add(sun_sensor, "coarse_sun_sensor", "basilisk_official_native", 89)
        connected("spacecraft.scStateOutMsg", "coarse_sun_sensor")
        connected("eclipse.eclipseOutMsgs[0]", "coarse_sun_sensor")

        earth_sensor = EarthHorizonSensor()
        earth_sensor.stateInMsg.subscribeTo(sc.scStateOutMsg)
        add(earth_sensor, "project_earth_horizon_sensor", "basilisk_project_native", 89)
        connected("spacecraft.scStateOutMsg", "project_earth_horizon_sensor")

        sun_direction = SunDirectionSensor()
        sun_direction.stateInMsg.subscribeTo(sc.scStateOutMsg)
        sun_direction.sunInMsg.subscribeTo(sun_msg)
        add(sun_direction, "project_sun_direction_sensor", "basilisk_project_native", 89)
        connected("spacecraft.scStateOutMsg", "project_sun_direction_sensor")
        connected("sun_ephemeris", "project_sun_direction_sensor")

        magnetic = magneticFieldCenteredDipole.MagneticFieldCenteredDipole()
        magnetic.planetRadius = 6_371_200.0
        magnetic.g10 = -30_926e-9
        magnetic.g11 = -2_318e-9
        magnetic.h11 = 5_817e-9
        magnetic.addSpacecraftToModel(sc.scStateOutMsg)
        add(magnetic, "magnetic_field", "basilisk_official_native", 90)
        connected("spacecraft.scStateOutMsg", "magnetic_field")

        magnetometer_model = magnetometer.Magnetometer()
        # This is a physical Basilisk magnetometer, not a magnetic-field
        # telemetry rotation. Validate direct finite TaskSpec values before
        # forwarding to upstream SWIG to avoid quiet NaN propagation.
        angles = []
        for name, low, high in (
            ("magnetometer_mount_yaw_deg", -180., 180.),
            ("magnetometer_mount_pitch_deg", -89., 89.),
            ("magnetometer_mount_roll_deg", -180., 180.),
        ):
            raw = values.get(name, 0.)
            if type(raw) not in (int, float) or not math.isfinite(raw) or not low <= raw <= high:
                raise ValueError("NATIVE_MAGNETOMETER_MOUNT_INVALID")
            angles.append(math.radians(float(raw)))
        mag_bias = []
        for axis in "xyz":
            raw = values.get(f"magnetometer_bias_tesla_{axis}", 0.)
            if type(raw) not in (int, float) or not math.isfinite(raw) or abs(raw) > 1e-4:
                raise ValueError("NATIVE_MAGNETOMETER_BIAS_INVALID")
            mag_bias.append(float(raw))
        mag_scale = values.get("magnetometer_scale_factor", 1.)
        if type(mag_scale) not in (int, float) or not math.isfinite(mag_scale) or not 0. < mag_scale <= 2.:
            raise ValueError("NATIVE_MAGNETOMETER_SCALE_INVALID")
        magnetometer_model.setBodyToSensorDCM(*angles)
        magnetometer_model.senBias = mag_bias
        magnetometer_model.scaleFactor = float(mag_scale)
        magnetometer_model.stateInMsg.subscribeTo(sc.scStateOutMsg)
        magnetometer_model.magInMsg.subscribeTo(magnetic.envOutMsgs[0])
        add(magnetometer_model, "magnetometer", "basilisk_official_native", 85)
        connected("magnetic_field.envOutMsgs[0]", "magnetometer")

        imu_fault = ImuFaultInjector(
            cfg.events,
            seed=int(values.get("simulation_seed", values.get("random_seed", 0))),
            base_noise_std_rad_s=float(values.get("gyro_base_noise_std_rad_s", 1.0e-5)),
        )
        imu_fault.imuInMsg.subscribeTo(imu.sensorOutMsg)
        add(imu_fault, "project_imu_fault_injector", "basilisk_project_native", 84)
        connected("imu.sensorOutMsg", "project_imu_fault_injector")

        star_fault = StarTrackerFaultInjector(cfg.events, seed=int(values.get("simulation_seed", 0)))
        star_fault.starInMsg.subscribeTo(tracker.sensorOutMsg)
        add(star_fault, "project_star_tracker_fault_injector", "basilisk_project_native", 84)
        connected("star_tracker.sensorOutMsg", "project_star_tracker_fault_injector")

        sun_fault = SunVectorFaultInjector(cfg.events, seed=int(values.get("simulation_seed", 0)))
        sun_fault.vectorInMsg.subscribeTo(sun_direction.bodyOutMsg)
        add(sun_fault, "project_sun_vector_fault_injector", "basilisk_project_native", 84)
        connected("project_sun_direction_sensor.bodyOutMsg", "project_sun_vector_fault_injector")

        fusion = AdcsSensorFusion()
        fusion.starInMsg.subscribeTo(star_fault.starOutMsg)
        fusion.imuInMsg.subscribeTo(imu_fault.imuOutMsg)
        fusion.magInMsg.subscribeTo(magnetometer_model.tamDataOutMsg)
        fusion.sunBodyInMsg.subscribeTo(sun_fault.vectorOutMsg)
        fusion.sunReferenceInMsg.subscribeTo(sun_direction.referenceOutMsg)
        fusion.earthBodyInMsg.subscribeTo(earth_sensor.directionOutMsg)
        fusion.earthReferenceInMsg.subscribeTo(earth_sensor.referenceOutMsg)
        fusion.starValidInMsg.subscribeTo(star_fault.validOutMsg)
        fusion.sunValidInMsg.subscribeTo(sun_fault.validOutMsg)
        add(fusion, "project_adcs_sensor_fusion", "basilisk_project_native", 80)
        connected("project_star_tracker_fault_injector.starOutMsg", "project_adcs_sensor_fusion")
        connected("project_imu_fault_injector.imuOutMsg", "project_adcs_sensor_fusion")
        connected("magnetometer.tamDataOutMsg", "project_adcs_sensor_fusion")
        connected("project_sun_vector_fault_injector.vectorOutMsg", "project_adcs_sensor_fusion")
        connected("project_earth_horizon_sensor.directionOutMsg", "project_adcs_sensor_fusion")

        rw_speed_fault = ReactionWheelSpeedFaultInjector(cfg.events, wheel_count=len(rw_axes))
        rw_speed_fault.speedInMsg.subscribeTo(rw.rwSpeedOutMsg)
        add(rw_speed_fault, "project_rw_speed_fault_injector", "basilisk_project_native", 79)
        connected("reaction_wheels.rwSpeedOutMsg", "project_rw_speed_fault_injector")

        reference = inertial3D.inertial3D()
        reference.sigma_R0N = [0.0, 0.0, 0.0]
        add(reference, "inertial_guidance", "basilisk_official_native", 75)
        tracking = attTrackingError.attTrackingError()
        tracking.attRefInMsg.subscribeTo(reference.attRefOutMsg)
        tracking.attNavInMsg.subscribeTo(fusion.attOutMsg)
        add(tracking, "attitude_error", "basilisk_official_native", 70)
        connected("project_adcs_sensor_fusion.attOutMsg", "attitude_error")

        controller = mrpFeedback.mrpFeedback()
        controller.K = float(values.get("controller_k", 3.5))
        controller.P = float(values.get("controller_p", 30.0))
        controller.Ki = -1.0
        controller.integralLimit = -1.0
        controller.guidInMsg.subscribeTo(tracking.attGuidOutMsg)
        vehicle = messaging.VehicleConfigMsgPayload()
        vehicle.ISCPntB_B = [100.0, 0.0, 0.0, 0.0, 80.0, 0.0, 0.0, 0.0, 60.0]
        vehicle.massSC = sc.hub.mHub
        vehicle_msg = messaging.VehicleConfigMsg().write(vehicle)
        controller.vehConfigInMsg.subscribeTo(vehicle_msg)
        controller.rwParamsInMsg.subscribeTo(rw_config)
        controller.rwSpeedsInMsg.subscribeTo(rw_speed_fault.speedOutMsg)
        add(controller, "mrp_feedback", "basilisk_official_native", 65)
        connected("attitude_error.attGuidOutMsg", "mrp_feedback")
        connected("project_rw_speed_fault_injector.speedOutMsg", "mrp_feedback")

        motor = rwMotorTorque.rwMotorTorque()
        motor.controlAxes_B = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
        motor.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
        motor.rwParamsInMsg.subscribeTo(rw_config)
        add(motor, "rw_motor_torque", "basilisk_official_native", 60)
        connected("mrp_feedback.cmdTorqueOutMsg", "rw_motor_torque")

        rw_fault = ReactionWheelCommandFaultManager(
            cfg.events, wheel_count=len(rw_axes), max_torque_nm=rw_hardware_u_max,
            wheel_max_torques_nm=rw_hardware_u_max_by_wheel,
        )
        rw_fault.commandInMsg.subscribeTo(motor.rwMotorTorqueOutMsg)
        rw_fault.speedInMsg.subscribeTo(rw.rwSpeedOutMsg)
        rw.rwMotorCmdInMsg.subscribeTo(rw_fault.commandOutMsg)
        add(rw_fault, "project_rw_command_fault_manager", "basilisk_project_native", 59)
        connected("rw_motor_torque.rwMotorTorqueOutMsg", "project_rw_command_fault_manager")
        connected("reaction_wheels.rwSpeedOutMsg", "project_rw_command_fault_manager")
        connected("project_rw_command_fault_manager.commandOutMsg", "reaction_wheels")

        recorder_map: dict[str, Any] = {}
        whole_modules: dict[str, Any] = {}
        if not cfg.adcs_only:
            panel = simpleSolarPanel.SimpleSolarPanel()
            # A fixed body-frame normal on the *official* Basilisk panel.
            # Not an articulated/tracking panel. Check again at the runtime
            # boundary: finite TaskSpecs do not pass the continuous Pydantic form.
            panel_normal_b = [float(values.get(f"solar_normal_b_{axis}", default))
                              for axis, default in zip("xyz", (1.0, 0.0, 0.0))]
            normal_sq = sum(x * x for x in panel_normal_b)
            if (any(not math.isfinite(x) or abs(x) > 1.0 for x in panel_normal_b)
                    or not 0.999 <= normal_sq <= 1.001):
                raise ValueError("NATIVE_SOLAR_PANEL_NORMAL_B_NOT_UNIT")
            panel.setPanelParameters(panel_normal_b, float(values.get("solar_panel_area_m2", 2.5)), float(values.get("solar_efficiency", 0.28)))
            panel.sunInMsg.subscribeTo(sun_msg)
            panel.stateInMsg.subscribeTo(sc.scStateOutMsg)
            panel.sunEclipseInMsg.subscribeTo(eclipse_model.eclipseOutMsgs[0])
            add(panel, "solar_panel", "basilisk_official_native", 55)
            # Tracking is project-owned kinematics executed in the SAME
            # Basilisk task, prior to the official panel update at priority 55.
            # The official panel still calculates the actual power and eclipse.
            tracking_enabled = values.get("solar_tracking_enabled", False)
            tracking_rate = float(values.get("solar_tracking_max_slew_rad_s", 0.))
            if not isinstance(tracking_enabled, bool) or not math.isfinite(tracking_rate) or not 0. <= tracking_rate <= 1.:
                raise ValueError("NATIVE_SOLAR_TRACKING_CONFIG_INVALID")
            if tracking_enabled != (tracking_rate > 0.):
                raise ValueError("NATIVE_SOLAR_TRACKING_RATE_ENABLE_MISMATCH")
            if tracking_enabled:
                from .solar_tracking import BasiliskScheduledSolarNormalTracker
                panel_tracker = BasiliskScheduledSolarNormalTracker(
                    panel, max_slew_rate_rad_s=tracking_rate,
                )
                panel_tracker.scStateInMsg.subscribeTo(sc.scStateOutMsg)
                panel_tracker.sunInMsg.subscribeTo(sun_msg)
                add(panel_tracker, "project_solar_normal_tracker", "basilisk_project_native", 57)
                connected("spacecraft.scStateOutMsg", "project_solar_normal_tracker")
                connected("sun_ephemeris", "project_solar_normal_tracker")
                connected("project_solar_normal_tracker.normal_B", "solar_panel.nHat_B")
            connected("eclipse.eclipseOutMsgs[0]", "solar_panel")

            power_nodes = []
            # Only the always-on bus remains static.  ADCS, payload, communication
            # and propulsion draw are supplied by message-driven activity bridges.
            sink = simplePowerSink.SimplePowerSink()
            sink.nodePowerOut = -float(values.get("bus_power_w", 18.0))
            add(sink, "bus_power", "basilisk_official_native", 55)
            power_nodes.append(sink)
            whole_modules["bus_power"] = sink

            battery = simpleBattery.SimpleBattery()
            battery.storageCapacity = float(values.get("battery_capacity_wh", 160.0)) * 3600.0
            battery.storedCharge_Init = float(values.get("initial_soc", 0.62)) * battery.storageCapacity
            battery.addPowerNodeToModel(panel.nodePowerOutMsg)
            for node in power_nodes:
                battery.addPowerNodeToModel(node.nodePowerOutMsg)
            add(battery, "battery", "basilisk_official_native", 50)
            connected("solar_panel.nodePowerOutMsg", "battery")
            for node in power_nodes:
                connected(f"{node.ModelTag}.nodePowerOutMsg", "battery")

            from components.pdu.builder import ConstantDeviceRequest, PduLoadSheddingConfig, PduLoadSheddingSysModel
            pdu_requests = {
                name: ConstantDeviceRequest(f"unified{name.title()}PowerRequest", True)
                for name in ("payload", "adcs", "comm", "heater")
            }
            pdu = PduLoadSheddingSysModel(PduLoadSheddingConfig(
                payload_min_soc=float(values.get("payload_min_soc", 0.55)),
                comm_min_soc=float(values.get("comm_min_soc", 0.50)),
                heater_min_soc=float(values.get("heater_min_soc", 0.30)),
                adcs_min_soc=float(values.get("adcs_min_soc", 0.20)),
                recovery_soc=float(values.get("pdu_recovery_soc", 0.65)),
                fallback_initial_soc=float(values.get("initial_soc", 0.62)),
            ))
            pdu.batteryStatusInMsg.subscribeTo(battery.batPowerOutMsg)
            pdu.payloadRequestInMsg.subscribeTo(pdu_requests["payload"].deviceCmdOutMsg)
            pdu.adcsRequestInMsg.subscribeTo(pdu_requests["adcs"].deviceCmdOutMsg)
            pdu.commRequestInMsg.subscribeTo(pdu_requests["comm"].deviceCmdOutMsg)
            pdu.heaterRequestInMsg.subscribeTo(pdu_requests["heater"].deviceCmdOutMsg)
            for request_name, request_model in pdu_requests.items():
                add(request_model, f"project_{request_name}_power_request", "basilisk_project_native", 53)
            add(pdu, "project_eps_pdu", "basilisk_project_native", 49)
            connected("battery.batPowerOutMsg", "project_eps_pdu")

            gate = PowerDataModeGate(
                min_soc=float(values.get("min_operational_soc", 0.20)),
                max_pointing_error_deg=float(values.get("payload_max_pointing_error_deg", 20.0)),
                comm_max_pointing_error_deg=float(values.get("comm_max_pointing_error_deg", values.get("payload_max_pointing_error_deg", 20.0))),
                events=cfg.events,
            )
            gate.batteryInMsg.subscribeTo(battery.batPowerOutMsg)
            gate.guidInMsg.subscribeTo(tracking.attGuidOutMsg)
            add(gate, "project_power_data_mode_gate", "basilisk_project_native", 45)
            gate.subscribe_power_permits(
                payload_status_msg=pdu.payloadStatusOutMsg,
                comm_status_msg=pdu.commStatusOutMsg,
            )
            whole_modules["gate"] = gate
            whole_modules["pdu"] = pdu
            whole_modules["pdu_requests"] = pdu_requests
            connected("battery.batPowerOutMsg", "project_power_data_mode_gate")
            connected("attitude_error.attGuidOutMsg", "project_power_data_mode_gate")
            connected("project_eps_pdu.payloadStatusOutMsg", "project_power_data_mode_gate")
            connected("project_eps_pdu.commStatusOutMsg", "project_power_data_mode_gate")

            instrument = simpleInstrument.SimpleInstrument()
            instrument.nodeBaudRate = float(values.get("payload_data_rate_bps", 2_500_000.0))
            instrument.nodeDataName = "payload"
            instrument.nodeStatusInMsg.subscribeTo(gate.payloadCmdOutMsg)
            add(instrument, "payload_instrument", "basilisk_official_native", 40)
            connected("project_power_data_mode_gate.payloadCmdOutMsg", "payload_instrument")

            # Native DownlinkHandling is the only production downlink model.
            # Do not register an unbound SimpleTransmitter compatibility object:
            # Basilisk transmitters require at least one storage input, and an
            # unbound instance publishes uninitialised messages and can corrupt
            # the native task at execution time.
            storage = simpleStorageUnit.SimpleStorageUnit()
            storage.storageCapacity = float(values.get("storage_capacity_bits", 6_000_000_000.0))
            storage.addDataNodeToModel(instrument.nodeDataOutMsg)

            from subsystems.comm_data.builder import (
                build_comm_data_native_odh_downlink,
                build_whole_spacecraft_native_rf_access_chain,
            )
            rf_access = build_whole_spacecraft_native_rf_access_chain(
                sc_state_msg=sc.scStateOutMsg,
                frequency_hz=float(values.get("downlink_frequency_hz", 2.2e9)),
                bandwidth_hz=float(values.get("downlink_bandwidth_hz", 1.0e6)),
                spacecraft_orientation_b=tuple(float(x) for x in values.get("spacecraft_antenna_orientation_b", (0.0, -0.41421356237309503, 0.0))),
                pointing_loss_enabled=bool(values.get("link_pointing_loss_enabled", True)),
                frequency_loss_enabled=bool(values.get("link_frequency_loss_enabled", True)),
                atmospheric_attenuation_enabled=bool(values.get("link_atmospheric_attenuation_enabled", False)),
                sun_msg=sun_msg,
                planet_msgs=(earth_msg,),
                eclipse_msg=eclipse_model.eclipseOutMsgs[0],
            )
            for model, tag, priority in (
                (rf_access.ground_location, "ground_location", 58),
                (rf_access.spacecraft_antenna, "spacecraft_antenna", 57),
                (rf_access.ground_antenna, "ground_antenna", 57),
                (rf_access.link_budget, "native_link_budget", 56),
            ):
                add(model, tag, "basilisk_official_native", priority)
            connected("spacecraft.scStateOutMsg", "ground_location")
            connected("spacecraft.scStateOutMsg", "spacecraft_antenna")
            connected("ground_location.currentGroundStateOutMsg", "ground_antenna")
            connected("spacecraft_antenna.antennaOutMsg", "native_link_budget")
            connected("ground_antenna.antennaOutMsg", "native_link_budget")

            rf_gate = NativeRfDownlinkGate(
                max_pointing_error_deg=float(values.get("comm_max_pointing_error_deg", values.get("payload_max_pointing_error_deg", 20.0)))
            )
            rf_gate.accessInMsg.subscribeTo(rf_access.access_msg)
            rf_gate.linkBudgetInMsg.subscribeTo(rf_access.link_budget_msg)
            rf_gate.guidInMsg.subscribeTo(tracking.attGuidOutMsg)
            rf_gate.commandPermitInMsg.subscribeTo(gate.downlinkStatusOutMsg)
            # Thermal subscription is completed after the network is built below.
            native_downlink, _owned_link_msg = build_comm_data_native_odh_downlink(
                storage=storage,
                data_name="payload",
                bit_rate_request_bps=float(values.get("downlink_rate_bps", 1_500_000.0)),
                packet_size_bits=float(values.get("downlink_packet_size_bits", 256.0)),
                max_retransmissions=int(values.get("downlink_max_retransmissions", 10)),
                link_active=False,
                cnr_linear=0.0,
                bandwidth_hz=float(values.get("downlink_bandwidth_hz", 1.0e6)),
                frequency_hz=float(values.get("downlink_frequency_hz", 2.2e9)),
                link_budget_msg=rf_gate.linkBudgetOutMsg,
            )
            # Complete every storage data-node subscription before registering
            # the storage model with the Basilisk task.  Mutating a native
            # subscriber vector after task registration can leave the C++
            # module with an invalid input topology.
            add(rf_gate, "project_native_rf_downlink_gate", "basilisk_project_native", 44)
            add(native_downlink, "native_downlink_handling", "basilisk_official_native", 38)
            add(storage, "storage", "basilisk_official_native", 35)
            connected("payload_instrument.nodeDataOutMsg", "storage")
            connected("ground_location.accessOutMsgs[0]", "project_native_rf_downlink_gate")
            connected("native_link_budget.linkBudgetOutPayload", "project_native_rf_downlink_gate")
            connected("project_power_data_mode_gate.downlinkStatusOutMsg", "project_native_rf_downlink_gate")
            connected("project_native_rf_downlink_gate.linkBudgetOutMsg", "native_downlink_handling")
            connected("native_downlink_handling.nodeDataOutMsg", "storage")

            adcs_power_bridge = AdcsControlPowerBridge(
                "project_adcs_control_power_bridge",
                motor_torque_msg=motor.rwMotorTorqueOutMsg,
                wheel_speed_msg=rw.rwSpeedOutMsg,
                base_power_w=float(values.get("adcs_power_w", 12.0)),
                drive_efficiency=float(values.get("adcs_drive_efficiency", 0.75)),
                max_power_w=float(values.get("adcs_max_power_w", 120.0)),
                enable_status_msg=pdu.adcsStatusOutMsg,
            )
            payload_power_bridge = DataActivityPowerBridge(
                "project_payload_activity_power_bridge",
                nominal_rate_bps=max(1.0, float(values.get("payload_data_rate_bps", 2_500_000.0))),
                active_power_w=float(values.get("payload_power_w", 38.0)),
                data_msg=instrument.nodeDataOutMsg,
                enable_status_msg=pdu.payloadStatusOutMsg,
            )
            comm_power_bridge = DownlinkActivityPowerBridge(
                "project_comm_activity_power_bridge",
                nominal_rate_bps=max(1.0, float(values.get("downlink_rate_bps", 1_500_000.0))),
                active_power_w=float(values.get("downlink_power_w", 16.0)),
                downlink_msg=native_downlink.downlinkOutMsg,
                enable_status_msg=pdu.commStatusOutMsg,
            )
            heater_power_bridge = StatusPowerBridge(
                "project_heater_status_power_bridge",
                active_power_w=float(values.get("heater_power_w", 15.0)),
                enable_status_msg=pdu.heaterStatusOutMsg,
            )
            for bridge in (adcs_power_bridge, payload_power_bridge, comm_power_bridge, heater_power_bridge):
                add(bridge, bridge.ModelTag, "engineering_proxy", 54)
                battery.addPowerNodeToModel(bridge.powerOutMsg)
                connected(f"{bridge.ModelTag}.powerOutMsg", "battery")
            whole_modules.update({
                "adcs_power_bridge": adcs_power_bridge,
                "payload_power_bridge": payload_power_bridge,
                "comm_power_bridge": comm_power_bridge,
                "heater_power_bridge": heater_power_bridge,
                "rf_access": rf_access,
                "rf_gate": rf_gate,
                "native_downlink": native_downlink,
            })

            propulsion_enabled = bool(
                values.get("propulsion_enabled", values.get("enable_propulsion", False))
            ) or any(
                event.effect in {
                    "propulsion_thruster_ignition_failure",
                    "propulsion_burn_impulse_loss",
                }
                for event in cfg.events
            )
            if propulsion_enabled:
                from subsystems.propulsion.builder import (
                    build_nominal_propulsion_config,
                    build_propulsion_basilisk_assembly_graph,
                )

                propulsion_cfg = build_nominal_propulsion_config()
                thruster_count = int(propulsion_cfg.thruster_command.num_thrusters)
                thrust_n = max(float(values.get("propulsion_thrust_n", 1.0)), 0.0)
                isp_s = max(float(values.get("propulsion_isp_s", 200.0)), 1.0e-6)
                burn_on_time_s = max(float(values.get("propulsion_burn_on_time_s", 0.5)), 0.0)
                burn_start_s = max(float(values.get("propulsion_burn_start_s", 30.0)), 0.0)
                initial_propellant_kg = max(float(values.get("propulsion_initial_propellant_kg", 1.0)), 0.0)
                tank_capacity_kg = max(float(values.get("propulsion_tank_capacity_kg", 2.0)), initial_propellant_kg)
                min_burn_soc = max(0.0, min(float(values.get("propulsion_min_burn_soc", 0.30)), 1.0))
                nominal_directions = propulsion_cfg.thruster_physical.directions_b
                nominal_positions = propulsion_cfg.thruster_physical.lever_arms_b_m
                thruster_directions = []
                thruster_positions = []
                for thr_index in range(thruster_count):
                    direction = tuple(float(values.get(
                        f"propulsion_thruster_{thr_index}_direction_b_{axis}",
                        nominal_directions[thr_index][axis_index],
                    )) for axis_index, axis in enumerate("xyz"))
                    position = tuple(float(values.get(
                        f"propulsion_thruster_{thr_index}_location_b_{axis}_m",
                        nominal_positions[thr_index][axis_index],
                    )) for axis_index, axis in enumerate("xyz"))
                    norm_sq = sum(x * x for x in direction)
                    if not all(math.isfinite(x) for x in (*direction, *position)):
                        raise ValueError("NATIVE_PROPULSION_GEOMETRY_NOT_FINITE")
                    if not 0.999 <= norm_sq <= 1.001 or any(abs(x) > 2 for x in position):
                        raise ValueError("NATIVE_PROPULSION_GEOMETRY_OUT_OF_RANGE")
                    thruster_directions.append(direction)
                    thruster_positions.append(position)
                min_pulse_s = float(values.get("propulsion_min_pulse_s", .05))
                if not math.isfinite(min_pulse_s) or not 0 < min_pulse_s <= 10:
                    raise ValueError("NATIVE_PROPULSION_MIN_PULSE_OUT_OF_RANGE")
                propulsion_cfg = replace(
                    propulsion_cfg,
                    thruster_command=replace(
                        propulsion_cfg.thruster_command,
                        nominal_on_time_s=burn_on_time_s,
                        active_ids=tuple(range(thruster_count)),
                        min_pulse_s=min_pulse_s,
                    ),
                    thruster_physical=replace(
                        propulsion_cfg.thruster_physical,
                        thrust_n=tuple(thrust_n for _ in range(thruster_count)),
                        isp_s=tuple(isp_s for _ in range(thruster_count)),
                        directions_b=tuple(thruster_directions),
                        lever_arms_b_m=tuple(thruster_positions),
                    ),
                    fuel_tank=replace(
                        propulsion_cfg.fuel_tank,
                        initial_mass_kg=initial_propellant_kg,
                        capacity_kg=tank_capacity_kg,
                    ),
                    min_soc_for_burn=min_burn_soc,
                    require_eps_permission=True,
                )
                propulsion_graph = build_propulsion_basilisk_assembly_graph(
                    propulsion_cfg,
                    on_time_s=tuple(0.0 for _ in range(thruster_count)),
                )
                sc.addDynamicEffector(propulsion_graph.thruster_effector)
                propulsion_graph.fuel_tank.addThrusterSet(propulsion_graph.thruster_effector)
                sc.addStateEffector(propulsion_graph.fuel_tank)
                add(propulsion_graph.thruster_effector, "propulsion_thrusters", "basilisk_official_native", 98)
                add(propulsion_graph.fuel_tank, "propulsion_fuel_tank", "basilisk_official_native", 97)
                propulsion_controller = PropulsionBurnController(
                    burn_start_s=burn_start_s,
                    on_time_s=tuple(burn_on_time_s for _ in range(thruster_count)),
                    min_soc=min_burn_soc,
                    events=cfg.events,
                )
                propulsion_controller.batteryInMsg.subscribeTo(battery.batPowerOutMsg)
                propulsion_controller.safeModeInMsg.subscribeTo(gate.safeModeStatusOutMsg)
                propulsion_graph.thruster_effector.cmdsInMsg.subscribeTo(propulsion_controller.commandOutMsg)
                add(propulsion_controller, "project_propulsion_burn_controller", "basilisk_project_native", 102)
                connected("battery.batPowerOutMsg", "project_propulsion_burn_controller")
                connected("project_power_data_mode_gate.safeModeStatusOutMsg", "project_propulsion_burn_controller")
                connected("project_propulsion_burn_controller.commandOutMsg", "propulsion_thrusters")
                connected("propulsion_thrusters", "spacecraft")
                connected("propulsion_thrusters", "propulsion_fuel_tank")
                connected("propulsion_fuel_tank", "spacecraft")
                propulsion_power_bridge = ThrusterActivityPowerBridge(
                    "project_propulsion_activity_power_bridge",
                    thruster_output_msgs=tuple(propulsion_graph.thruster_effector.thrusterOutMsgs),
                    active_power_per_thruster_w=float(values.get("propulsion_power_w", 20.0)),
                    enable_status_msg=propulsion_controller.permissionStatusOutMsg,
                )
                add(propulsion_power_bridge, propulsion_power_bridge.ModelTag, "engineering_proxy", 54)
                battery.addPowerNodeToModel(propulsion_power_bridge.powerOutMsg)
                connected("project_propulsion_activity_power_bridge.powerOutMsg", "battery")
                whole_modules.update({
                    "propulsion_graph": propulsion_graph,
                    "propulsion_controller": propulsion_controller,
                    "propulsion_thrusters": propulsion_graph.thruster_effector,
                    "propulsion_fuel_tank": propulsion_graph.fuel_tank,
                    "propulsion_power_bridge": propulsion_power_bridge,
                })

            from subsystems.thermal.builder import (
                attach_thermal_network_graph_to_task,
                build_thermal_network_assembly_graph,
            )
            from subsystems.thermal.network_config import (
                ThermalConductionPath,
                ThermalNodeParams,
                build_nominal_thermal_network_config,
            )
            base_thermal_cfg = build_nominal_thermal_network_config()
            thermal_nodes = dict(base_thermal_cfg.nodes)
            # Add explicit communication and propulsion nodes so local thermal
            # margins are not hidden inside the electronics/payload temperatures.
            thermal_nodes["comm"] = ThermalNodeParams(
                mass_kg=float(values.get("comm_thermal_mass_kg", 2.5)),
                specific_heat_j_per_kg_k=float(values.get("comm_specific_heat_j_kg_k", 700.0)),
                surface_area_m2=float(values.get("comm_radiating_area_m2", 0.08)),
                emissivity=float(values.get("comm_emissivity", 0.80)),
                absorptivity=float(values.get("comm_absorptivity", 0.25)),
                initial_temp_k=float(values.get("initial_comm_temp_k", 295.0)),
                min_temp_k=float(values.get("comm_min_temp_k", 230.0)),
                max_temp_k=float(values.get("comm_max_temp_k", 335.0)),
            )
            thermal_nodes["propulsion"] = ThermalNodeParams(
                mass_kg=float(values.get("propulsion_thermal_mass_kg", 4.0)),
                specific_heat_j_per_kg_k=float(values.get("propulsion_specific_heat_j_kg_k", 650.0)),
                surface_area_m2=float(values.get("propulsion_radiating_area_m2", 0.10)),
                emissivity=float(values.get("propulsion_emissivity", 0.80)),
                absorptivity=float(values.get("propulsion_absorptivity", 0.25)),
                initial_temp_k=float(values.get("initial_propulsion_temp_k", 292.0)),
                min_temp_k=float(values.get("propulsion_min_temp_k", 220.0)),
                max_temp_k=float(values.get("propulsion_max_temp_k", 350.0)),
            )
            # User-facing project thermal-network physics: these values modify
            # the actual Basilisk-scheduled ThermalNetworkSysModel, never a
            # synthetic post-run curve or an upstream Basilisk heat module.
            payload_node = thermal_nodes["payload"]
            thermal_fields = {
                "payload_thermal_mass_kg": (3., 0., 50.),
                "payload_specific_heat_j_kg_k": (600., 0., 3000.),
                "payload_thermal_emissivity": (.75, 0., 1.),
                "payload_radiator_area_m2": (.25, 0., 5.),
                "battery_heater_power_w": (12., 0., 100.),
                "battery_heater_setpoint_k": (286., 240., 310.),
            }
            thermal_operator = {key: float(values.get(key, default))
                                for key, (default, _, _) in thermal_fields.items()}
            for key, val in thermal_operator.items():
                _, lower, upper = thermal_fields[key]
                if not math.isfinite(val) or val < lower or val > upper or (
                    key in {"payload_thermal_mass_kg", "payload_specific_heat_j_kg_k"} and val <= 0
                ):
                    raise ValueError(f"NATIVE_THERMAL_FIELD_OUT_OF_RANGE:{key}")
            thermal_nodes["payload"] = replace(
                payload_node,
                initial_temp_k=float(values.get("initial_payload_temp_k", payload_node.initial_temp_k)),
                mass_kg=thermal_operator["payload_thermal_mass_kg"],
                specific_heat_j_per_kg_k=thermal_operator["payload_specific_heat_j_kg_k"],
                emissivity=thermal_operator["payload_thermal_emissivity"],
            )
            thermal_radiators = dict(base_thermal_cfg.radiators)
            thermal_radiators["payload"] = replace(
                thermal_radiators["payload"],
                area_m2=thermal_operator["payload_radiator_area_m2"],
            )
            thermal_heaters = dict(base_thermal_cfg.heaters)
            thermal_heaters["battery"] = replace(
                thermal_heaters["battery"],
                power_w=thermal_operator["battery_heater_power_w"],
                setpoint_k=thermal_operator["battery_heater_setpoint_k"],
            )
            conduction_paths = list(base_thermal_cfg.conduction_paths) + [
                ThermalConductionPath("comm", "structure", float(values.get("comm_structure_conductance_w_k", 0.35))),
                ThermalConductionPath("propulsion", "structure", float(values.get("propulsion_structure_conductance_w_k", 0.45))),
            ]
            thermal_cfg = replace(
                base_thermal_cfg,
                nodes=thermal_nodes,
                conduction_paths=conduction_paths,
                radiators=thermal_radiators,
                heaters=thermal_heaters,
                duration_s=max(cfg.duration_s, cfg.step_s),
                step_s=cfg.step_s,
            )
            thermal_inputs = [
                (adcs_power_bridge.powerOutMsg, 0.90, "adcs", "adcs_control"),
                (payload_power_bridge.powerOutMsg, 0.95, "payload", "payload_activity"),
                (comm_power_bridge.powerOutMsg, 0.90, "comm", "comm_downlink"),
                (heater_power_bridge.powerOutMsg, 1.00, "battery", "pdu_heater"),
            ]
            if "propulsion_power_bridge" in whole_modules:
                thermal_inputs.append((whole_modules["propulsion_power_bridge"].powerOutMsg, 0.85, "propulsion", "propulsion_activity"))
            thermal_graph = build_thermal_network_assembly_graph(
                duration_s=cfg.duration_s,
                step_s=cfg.step_s,
                power_inputs=thermal_inputs,
                eclipse_msg=eclipse_model.eclipseOutMsgs[0],
                thermal_network_cfg=thermal_cfg,
            )
            attach_thermal_network_graph_to_task(sim, task_name, thermal_graph)
            instantiated.append({"tag": "thermal_network", "source": "project_deterministic_model"})
            for name in sorted(thermal_graph.nodes):
                instantiated.append({"tag": f"thermal_node_{name}", "source": "project_deterministic_model"})
            for heater in thermal_graph.heaters.values():
                heater.subscribe_enable(pdu.heaterStatusOutMsg)
            gate.subscribe_thermal_status(thermal_graph.network.thermalStatusOutMsg)
            rf_gate.thermalStatusInMsg.subscribeTo(thermal_graph.network.thermalStatusOutMsg)
            connected("thermal_network.thermalStatusOutMsg", "project_power_data_mode_gate")
            connected("thermal_network.thermalStatusOutMsg", "project_native_rf_downlink_gate")
            connected("project_eps_pdu.heaterStatusOutMsg", "thermal_network.heaters")

            parameter_events = WholeSpacecraftParameterEventController(
                cfg.events, battery=battery, solar_panel=panel, thermal=thermal_graph.network
            )
            add(parameter_events, "project_whole_parameter_event_controller", "basilisk_project_native", 58)
            connected("project_whole_parameter_event_controller", "solar_panel.parameters")
            connected("project_whole_parameter_event_controller", "battery.parameters")
            connected("project_whole_parameter_event_controller", "thermal_network.radiators")

            whole_modules.update({
                "panel": panel, "battery": battery, "instrument": instrument,
                "storage": storage, "thermal": thermal_graph.network,
                "thermal_graph": thermal_graph, "parameter_events": parameter_events,
            })


        messages = {
            "state": sc.scStateOutMsg,
            "guid": tracking.attGuidOutMsg,
            "rw": rw.rwSpeedOutMsg,
            "rw_measured": rw_speed_fault.speedOutMsg,
            "rw_command_raw": motor.rwMotorTorqueOutMsg,
            "rw_command_applied": rw_fault.commandOutMsg,
            "rw_effective_drag": rw_fault.dragOutMsg,
            "rw_effective_torque_limit": rw_fault.torqueLimitOutMsg,
            "rw_effective_speed_limit": rw_fault.speedLimitOutMsg,
            "star_raw": tracker.sensorOutMsg,
            "star": star_fault.starOutMsg,
            "star_valid": star_fault.validOutMsg,
            "imu_raw": imu.sensorOutMsg,
            "imu": imu_fault.imuOutMsg,
            "imu_bias": imu_fault.biasOutMsg,
            "imu_noise": imu_fault.noiseOutMsg,
            "mag": magnetometer_model.tamDataOutMsg,
            "sun_sensor": sun_sensor.cssDataOutMsg,
            "sun_vector_raw": sun_direction.bodyOutMsg,
            "sun_vector": sun_fault.vectorOutMsg,
            "sun_valid": sun_fault.validOutMsg,
            "fusion_source_mode": fusion.sourceModeOutMsg,
            "earth_sensor": earth_sensor.directionOutMsg,
            "earth_reference": earth_sensor.referenceOutMsg,
            "eclipse": eclipse_model.eclipseOutMsgs[0],
            "event_fault": event_status.faultOutMsg,
            "event_degradation": event_status.degradationOutMsg,
            "event_constraint": event_status.constraintOutMsg,
        }
        if not cfg.adcs_only:
            messages.update({
                "battery": whole_modules["battery"].batPowerOutMsg,
                "panel": whole_modules["panel"].nodePowerOutMsg,
                "bus_power": whole_modules["bus_power"].nodePowerOutMsg,
                "adcs_power": whole_modules["adcs_power_bridge"].powerOutMsg,
                "payload_power": whole_modules["payload_power_bridge"].powerOutMsg,
                "downlink_power": whole_modules["comm_power_bridge"].powerOutMsg,
                "heater_power": whole_modules["heater_power_bridge"].powerOutMsg,
                "instrument": whole_modules["instrument"].nodeDataOutMsg,
                "storage": whole_modules["storage"].storageUnitDataOutMsg,
                "temperature": whole_modules["thermal_graph"].nodes["payload"].tempOutMsg,
                "thermal_status": whole_modules["thermal"].thermalStatusOutMsg,
                "ground_access": whole_modules["rf_access"].access_msg,
                "native_link_budget": whole_modules["rf_access"].link_budget_msg,
                "gated_link_budget": whole_modules["rf_gate"].linkBudgetOutMsg,
                "downlink_handling": whole_modules["native_downlink"].downlinkOutMsg,
                "payload_status": whole_modules["gate"].payloadStatusOutMsg,
                "downlink_status": whole_modules["gate"].downlinkStatusOutMsg,
                "safe_mode_status": whole_modules["gate"].safeModeStatusOutMsg,
                "pdu_payload_status": whole_modules["pdu"].payloadStatusOutMsg,
                "pdu_adcs_status": whole_modules["pdu"].adcsStatusOutMsg,
                "pdu_comm_status": whole_modules["pdu"].commStatusOutMsg,
                "pdu_heater_status": whole_modules["pdu"].heaterStatusOutMsg,
            })
            for thermal_node_name, thermal_node in whole_modules["thermal_graph"].nodes.items():
                messages[f"thermal_temp_{thermal_node_name}"] = thermal_node.tempOutMsg
                messages[f"thermal_safe_{thermal_node_name}"] = thermal_node.thermalStatusOutMsg
            if "propulsion_controller" in whole_modules:
                messages.update({
                    "propulsion_fuel": whole_modules["propulsion_fuel_tank"].fuelTankOutMsg,
                    "propulsion_burn_status": whole_modules["propulsion_controller"].burnStatusOutMsg,
                    "propulsion_permission": whole_modules["propulsion_controller"].permissionStatusOutMsg,
                })
                messages["propulsion_power"] = whole_modules["propulsion_power_bridge"].powerOutMsg
                for thruster_index, thruster_msg in enumerate(whole_modules["propulsion_thrusters"].thrusterOutMsgs):
                    messages[f"propulsion_thruster_{thruster_index}"] = thruster_msg
        for name, message in messages.items():
            recorder = message.recorder(macros.sec2nano(cfg.sample_s))
            sim.AddModelToTask(task_name, recorder, None, 1)
            recorder_map[name] = recorder
            record_sources.append(name)

        from sat_sim.basilisk_recorder_groups import attach_grouped_message_recorders, group_manifest
        recorder_groups = attach_grouped_message_recorders(
            sim=sim, task_name=task_name, message_handles=messages, streams=cfg.telemetry_streams
        )

        vizard_recording_file = None
        if cfg.vizard_save_file is not None:
            from .vizard_native_export import connect_native_vizard_recording
            viz_module, vizard_recording_file = connect_native_vizard_recording(
                sim, task_name, sc, rw_effector=rw, output_file=cfg.vizard_save_file,
            )
            # vizSupport adds the real upstream vizInterface module to this
            # Basilisk task; Vizard is a post-processing output, not a solver.
            instantiated.append({"tag": "native_vizard_vizInterface", "source": "basilisk_official_native"})
            connected("spacecraft.scStateOutMsg", "native_vizard_vizInterface")
            connected("reaction_wheels", "native_vizard_vizInterface")

        nominal_control = {
            "K": float(controller.K),
            "P": float(controller.P),
            "Ki": float(controller.Ki),
        }
        nominal_min_soc = float(whole_modules["gate"].min_soc) if not cfg.adcs_only else 0.0
        nominal_instrument_rate = float(whole_modules["instrument"].nodeBaudRate) if not cfg.adcs_only else 0.0
        interactive_one_shot = {"reset_instrument_rate": False}
        interactive_events: list[BSKEventSpec] = []

        def apply_interactive_command(command: Any, current_time_s: float, segment_duration_s: float) -> None:
            operation = str(command.operation)
            parameters = dict(command.parameters)
            if operation == "adcs.target.set":
                reference.sigma_R0N = [float(value) for value in parameters["sigma_rn"]]
            elif operation == "adcs.control.set":
                enabled = bool(parameters["enabled"])
                controller.K = nominal_control["K"] if enabled else 0.0
                controller.P = nominal_control["P"] if enabled else 0.0
                controller.Ki = nominal_control["Ki"] if enabled else -1.0
            elif operation == "session.mode.set":
                if cfg.adcs_only:
                    raise ValueError("session.mode.set requires whole-spacecraft runtime")
                mode = str(parameters["mode"])
                requested = whole_modules["pdu_requests"]
                requested["adcs"].requested = True
                requested["payload"].requested = mode == "inertialPoint"
                requested["comm"].requested = mode == "inertialPoint"
                requested["heater"].requested = mode != "safe"
            elif operation == "eps.load.set":
                if cfg.adcs_only:
                    raise ValueError("EPS command requires whole-spacecraft runtime")
                load_name = {"downlink": "comm"}.get(str(parameters["load_id"]), str(parameters["load_id"]))
                whole_modules["pdu_requests"][load_name].requested = bool(parameters["enabled"])
            elif operation == "eps.protection.set":
                if cfg.adcs_only:
                    raise ValueError("EPS command requires whole-spacecraft runtime")
                whole_modules["gate"].min_soc = nominal_min_soc if bool(parameters["enabled"]) else 0.0
            elif operation == "comm_data.generate":
                if cfg.adcs_only:
                    raise ValueError("Comm/Data command requires whole-spacecraft runtime")
                whole_modules["instrument"].nodeBaudRate = float(parameters["bits"]) / max(segment_duration_s, cfg.step_s)
                interactive_one_shot["reset_instrument_rate"] = True
            elif operation == "comm_data.downlink.set":
                if cfg.adcs_only:
                    raise ValueError("Comm/Data command requires whole-spacecraft runtime")
                whole_modules["pdu_requests"]["comm"].requested = bool(parameters["enabled"])
                whole_modules["native_downlink"].setBitRateRequest(float(parameters["rate_bps"]))
            elif operation in {"adcs.fault.inject", "eps.fault.inject", "comm_data.fault.inject"}:
                severity = max(0.0, min(float(parameters["severity"]), 1.0))
                fault_id = str(parameters["fault_id"])
                effect_parameters: dict[str, Any]
                if operation == "adcs.fault.inject":
                    effect = {
                        "rw_jam": "adcs_rw_jamming",
                        "rw_friction": "rw_friction_degradation",
                        "sensor_bias": "gyro_bias_step",
                    }[fault_id]
                    effect_parameters = {
                        "wheel_index": 0,
                        "brake_torque_nm": 0.2 * severity,
                        "drag_nms": 0.02 * severity,
                        "bias_step_deg_s": [0.1 * severity, 0.0, 0.0],
                    }
                elif operation == "eps.fault.inject":
                    if cfg.adcs_only:
                        raise ValueError("EPS fault requires whole-spacecraft runtime")
                    effect = {
                        "battery_capacity_loss": "eps_battery_capacity_loss",
                        "solar_array_degradation": "solar_panel_efficiency_loss",
                    }[fault_id]
                    effect_parameters = {"remaining_capacity_ratio": 1.0 - severity, "remaining_efficiency_ratio": 1.0 - severity}
                else:
                    if cfg.adcs_only:
                        raise ValueError("Comm/Data fault requires whole-spacecraft runtime")
                    effect = {"transmitter_loss": "comm_data_downlink_link_loss"}[fault_id]
                    effect_parameters = {"severity": severity}
                event = BSKEventSpec(
                    event_id=f"interactive:{command.command_id}", category="fault", effect=effect,
                    target=str(command.target), start_s=current_time_s, parameters=effect_parameters,
                )
                interactive_events.append(event)
                event_status.events = (*event_status.events, event)
                if effect in rw_fault.EFFECTS:
                    rw_fault.events = (*rw_fault.events, event)
                if effect in imu_fault.EFFECTS:
                    imu_fault.events = (*imu_fault.events, event)
                if not cfg.adcs_only:
                    if effect in whole_modules["parameter_events"].EFFECTS:
                        whole_modules["parameter_events"].events = (*whole_modules["parameter_events"].events, event)
                    if effect in {"comm_data_downlink_link_loss", "payload_instrument_off", "power_safe_mode_threshold"}:
                        whole_modules["gate"].events = (*whole_modules["gate"].events, event)
            else:
                raise ValueError(f"operation has no unified runtime adapter: {operation}")

        sim.InitializeSimulation()
        if self._test_only_fresh_native_graph_probe is not None:
            # Strictly after Basilisk has registered all native StateData,
            # strictly before executing the FIRST simulation time step.
            # The R9-D lab callback inspects imported vectors and explicitly
            # exits the isolated child. Do not use for session resumption.
            self._test_only_fresh_native_graph_probe(
                sim=sim, process=process, task=native_task,
                spacecraft=sc, rw_effector=rw, controller=controller,
                wheel_count=len(rw_axes), whole_modules=whole_modules,
                rw_command_fault_manager=rw_fault, allocator=motor,
            )
        execution_stops: list[float] = []
        segment_count = 0
        previous_stop_s = 0.0
        last_live_rows: list[dict[str, Any]] = []
        if cfg.execution_controller is not None:
            cfg.execution_controller.runtime_prepared()
            emitted_count = 0
            while True:
                stop_time_s = cfg.execution_controller.wait_for_advance()
                if stop_time_s is None:
                    break
                if stop_time_s == "__NATIVE_CHECKPOINT_AUDIT__":
                    # Capture directly from the *current native model graph*
                    # while its worker owns the paused simulation state.
                    # Observed output messages do not include integrator,
                    # controller-memory or subsystem private state: this is a
                    # fail-closed capability audit, not a resume checkpoint.
                    from sat_sim.interactive.native_checkpoint_audit import (
                        capture_observations, capture_scheduler_phase,
                        create_coverage_report,
                    )
                    import threading
                    observations, unavailable = capture_observations(
                        spacecraft=sc, reference=reference, controller=controller,
                        battery=battery if not cfg.adcs_only else None,
                        storage=storage if not cfg.adcs_only else None,
                    )
                    from sat_sim.interactive.native_state_pilot import capture_state_subset
                    from sat_sim.interactive.native_whole_adcs_observation import (
                        capture_live_adcs_owner_observation,
                    )
                    pilot_subset = None
                    native_adcs_owners = None
                    try:
                        pilot_subset = capture_state_subset(sc, expected_rw_count=len(rw_axes))
                    except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
                        unavailable["native_state_subset_pilot"] = f"{type(exc).__name__}: {exc}"
                    if pilot_subset is not None:
                        try:
                            native_adcs_owners = capture_live_adcs_owner_observation(
                                sim=sim, spacecraft=sc, rw_effector=rw,
                                controller=controller, allocator=motor,
                                applied_command_source=rw_fault,
                                native_state_subset=pilot_subset,
                            )
                        except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
                            unavailable["native_adcs_owner_observations"] = (
                                f"{type(exc).__name__}: {exc}"
                            )
                    else:
                        unavailable["native_adcs_owner_observations"] = (
                            "NATIVE_ADCS_STATE_SUBSET_UNAVAILABLE"
                        )
                    from sat_sim.interactive.native_joint_owner_observation import (
                        capture_joint_owner_observation,
                    )
                    phase = capture_scheduler_phase(sim)
                    from sat_sim.interactive.native_whole_clock_fsw_lab import capture_memento
                    clock_fsw = None
                    try:
                        clock_fsw = capture_memento(
                            sim=sim, process=process, task=native_task,
                            spacecraft=sc, controller=controller,
                            expected_rw_count=len(rw_axes),
                        )
                    except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
                        unavailable["native_clock_fsw_lab"] = f"{type(exc).__name__}: {exc}"
                    native_joint = None
                    if not cfg.adcs_only:
                        try:
                            native_joint = capture_joint_owner_observation(
                                scheduler_phase=phase,
                                battery=whole_modules["battery"],
                                panel=whole_modules["panel"],
                                pdu=whole_modules["pdu"],
                                thermal_graph=whole_modules["thermal_graph"],
                                storage=whole_modules["storage"],
                                downlink=whole_modules["native_downlink"],
                            )
                        except (AttributeError, ValueError, TypeError, RuntimeError) as exc:
                            unavailable["native_joint_owner_observations"] = (
                                f"{type(exc).__name__}: {exc}"
                            )
                    report = create_coverage_report(
                        sim_time_s=previous_stop_s,
                        observations=observations, unavailable=unavailable,
                        native_worker_ident=threading.get_ident(),
                        state_subset_pilot=pilot_subset,
                        native_adcs_owner_observations=native_adcs_owners,
                        native_joint_owner_observations=native_joint,
                        native_clock_fsw_lab=clock_fsw,
                        scheduler_phase=phase,
                    )
                    cfg.execution_controller.runtime_audited(report)
                    continue
                # Interactive telemetry has already copied the preceding
                # delta into its bounded bus/workspace.  Keeping every native
                # recorder sample as well makes long sessions progressively
                # slower and duplicates the authoritative archive.  Clear
                # only between controlled segments so the latest segment is
                # still available to the final native summary.  Batch runs do
                # not enter this branch and retain their complete trace.
                if emitted_count:
                    for recorder in recorder_map.values():
                        recorder.clear()
                    for group in recorder_groups.values():
                        for recorder in group.recorders.values():
                            recorder.clear()
                    for module in (whole_modules.get("thermal"), whole_modules.get("gate")):
                        trace = getattr(module, "trace", None)
                        if isinstance(trace, list):
                            trace.clear()
                    emitted_count = 0
                if stop_time_s <= previous_stop_s or (not cfg.interactive_unbounded and stop_time_s > cfg.duration_s):
                    raise ValueError("controlled execution stop must increase and remain within duration_s")
                for command in cfg.execution_controller.take_commands():
                    apply_interactive_command(command, previous_stop_s, stop_time_s - previous_stop_s)
                sim.ConfigureStopTime(macros.sec2nano(stop_time_s))
                sim.ExecuteSimulation()
                if interactive_one_shot["reset_instrument_rate"]:
                    whole_modules["instrument"].nodeBaudRate = nominal_instrument_rate
                    interactive_one_shot["reset_instrument_rate"] = False
                segment_count += 1
                previous_stop_s = stop_time_s
                if not cfg.interactive_unbounded:
                    execution_stops.append(stop_time_s)
                live_cfg = replace(cfg, events=(*cfg.events, *interactive_events))
                live_rows = _build_unified_rows(
                    recorder_map,
                    live_cfg,
                    cfg.sample_s,
                    start_index=emitted_count,
                )
                if not cfg.adcs_only:
                    _overlay_exact_thermal_trace(live_rows, whole_modules["thermal"], tolerance_s=max(cfg.step_s, cfg.sample_s) + 1.0e-9)
                if live_rows:
                    last_live_rows = live_rows
                cfg.execution_controller.runtime_advanced(stop_time_s, tuple(live_rows))
                emitted_count = len(recorder_map["state"].times())
            if cfg.interactive_unbounded and segment_count:
                # Keep the terminal stop only: unbounded sessions must not store all quanta.
                execution_stops = [previous_stop_s]
        else:
            requested_stops = tuple(float(value) for value in cfg.execution_stops_s)
            if requested_stops:
                if any(stop <= 0.0 or stop > cfg.duration_s for stop in requested_stops):
                    raise ValueError("execution stops must be within (0, duration_s]")
                if any(right <= left for left, right in zip(requested_stops, requested_stops[1:], strict=False)):
                    raise ValueError("execution stops must increase strictly")
            execution_stops = list(requested_stops)
            if not execution_stops or execution_stops[-1] < cfg.duration_s:
                execution_stops.append(cfg.duration_s)
            for stop_time_s in execution_stops:
                sim.ConfigureStopTime(macros.sec2nano(stop_time_s))
                sim.ExecuteSimulation()
            segment_count = len(execution_stops)

        if interactive_events:
            cfg = replace(cfg, events=(*cfg.events, *interactive_events), execution_controller=None)

        rows = _build_unified_rows(recorder_map, cfg, cfg.sample_s)
        if cfg.execution_controller is not None and not rows:
            # A final partial quantum may be shorter than the native recorder
            # cadence.  The preceding published row is still the correct
            # terminal evidence; do not fail finalization merely because this
            # last integration slice emitted no new sample.
            rows = list(last_live_rows)
        if not cfg.adcs_only:
            _overlay_exact_thermal_trace(rows, whole_modules["thermal"], tolerance_s=max(cfg.step_s, cfg.sample_s) + 1.0e-9)
        native_streams: list[dict[str, Any]] = []
        for group in recorder_groups.values():
            stream_rows = _build_unified_rows(group.recorders, cfg, group.spec.sample_s)
            if not cfg.adcs_only:
                _overlay_exact_thermal_trace(
                    stream_rows,
                    whole_modules["thermal"],
                    tolerance_s=max(cfg.step_s, group.spec.sample_s) + 1.0e-9,
                )
            native_streams.append({
                "stream_id": group.spec.stream_id,
                "sample_s": group.spec.sample_s,
                "format": group.spec.format,
                "fields": list(group.spec.fields),
                "rows": stream_rows,
                "recorder_sources": sorted(group.recorders),
                "task_name": group.task_name,
                "native_recorder": True,
            })
        native_multi_rate = {**group_manifest(recorder_groups), "streams": native_streams}

        final = rows[-1]
        bridge_health: dict[str, dict[str, Any]] = {}
        runtime_read_errors: list[str] = []
        causal_evidence: dict[str, Any] = {"status": "PASS", "requirements": []}
        if not cfg.adcs_only:
            bridge_objects = {
                "adcs": whole_modules["adcs_power_bridge"],
                "payload": whole_modules["payload_power_bridge"],
                "comm": whole_modules["comm_power_bridge"],
                "heater": whole_modules["heater_power_bridge"],
            }
            if "propulsion_power_bridge" in whole_modules:
                bridge_objects["propulsion"] = whole_modules["propulsion_power_bridge"]
            bridge_health = {name: _bridge_health_payload(bridge) for name, bridge in bridge_objects.items()}
            for name, health in bridge_health.items():
                runtime_read_errors.extend(f"{name}: {item}" for item in health.get("errors", []))
            runtime_read_errors.extend(f"rf_gate: {item}" for item in getattr(whole_modules["rf_gate"], "read_errors", ()))
            runtime_read_errors.extend(f"thermal: {item}" for item in getattr(whole_modules["thermal"], "read_errors", ()))
            causal_evidence = _required_coupling_evidence(cfg.required_couplings, bridge_health, rows)

        execution_status = "FAIL" if runtime_read_errors else "PASS"
        mission_status = "PASS"
        mission_reason_codes: list[str] = []
        energy_bounds = True
        data_bounds = True
        thermal_bounds = True
        if not cfg.adcs_only:
            energy_bounds = all(0.0 <= row["eps.battery_energy_j"] <= row["eps.battery_capacity_j"] + 1e-6 for row in rows)
            data_bounds = all(0.0 <= row["data.storage_bits"] <= row["data.storage_capacity_bits"] + 1e-6 for row in rows)
            thermal_bounds = all(int(row.get("thermal.safe", 1)) == 1 for row in rows)
            if not energy_bounds:
                mission_reason_codes.append("ENERGY_BOUNDS_FAILED")
            if not data_bounds:
                mission_reason_codes.append("DATA_BOUNDS_FAILED")
            if not thermal_bounds:
                mission_reason_codes.append("THERMAL_LIMIT_EXCEEDED")
            if causal_evidence.get("status") == "FAIL":
                mission_reason_codes.append("CAUSAL_REQUIREMENT_FAILED")
            elif causal_evidence.get("status") == "INCONCLUSIVE":
                mission_status = "INCONCLUSIVE"
                mission_reason_codes.append("CAUSAL_REQUIREMENT_NOT_EXERCISED")
            if any(code != "CAUSAL_REQUIREMENT_NOT_EXERCISED" for code in mission_reason_codes):
                mission_status = "FAIL"
        if execution_status == "FAIL":
            overall_status = "FAIL"
        elif mission_status == "FAIL":
            overall_status = "FAIL"
        elif mission_status == "INCONCLUSIVE":
            overall_status = "INCONCLUSIVE"
        else:
            overall_status = "PASS"

        official_count = sum(1 for item in instantiated if item["source"] == "basilisk_official_native")
        project_count = sum(1 for item in instantiated if item["source"] in {"basilisk_project_native", "project_deterministic_model"})
        engineering_proxy_count = sum(1 for item in instantiated if item["source"] == "engineering_proxy")
        summary = {
            # Legacy completion field retained for campaign compatibility.  New
            # consumers must use the three explicit status fields below.
            "status": "complete",
            "status_semantics_version": "v0.5.7.3",
            "execution_status": execution_status,
            "mission_status": mission_status,
            "overall_status": overall_status,
            "mission_reason_codes": mission_reason_codes,
            "capability_id": cfg.capability_id,
            "engine": "basilisk_unified_native_runtime",
            "backend_type": "basilisk_native_mixed",
            "runtime_truth_status": "instantiated_connected_recorded_unified",
            "process_count": 1,
            "task_count": 1,
            "instantiated_module_count": len(instantiated),
            "official_module_count": official_count,
            "project_native_module_count": project_count,
            "engineering_proxy_module_count": engineering_proxy_count,
            "external_module_count": 0,
            "external_or_proxy_module_count": engineering_proxy_count,
            "connected_message_count": len(connections),
            "recorder_count": len(recorder_map),
            "execution_segment_count": segment_count,
            "execution_stops_s": list(execution_stops),
            "trace_rows": len(rows),
            "initial_pointing_error_deg": rows[0]["adcs.pointing_error_deg"],
            "final_pointing_error_deg": final["adcs.pointing_error_deg"],
            "adcs_closed_loop_improved": final["adcs.pointing_error_deg"] < rows[0]["adcs.pointing_error_deg"],
            "initial_orbit_radius_m": rows[0]["orbit.radius_m"],
            "final_orbit_radius_m": final["orbit.radius_m"],
            "runtime_message_read_error_count": len(runtime_read_errors),
            "runtime_message_read_errors": runtime_read_errors[:20],
        }
        if not cfg.adcs_only:
            resource_status = "FAIL" if any(health.get("status") == "FAIL" for health in bridge_health.values()) else "PASS"
            summary.update({
                "final_battery_soc": final["eps.battery_soc"],
                "final_storage_bits": final["data.storage_bits"],
                "energy_bounds_pass": energy_bounds,
                "data_bounds_pass": data_bounds,
                "thermal_bounds_pass": thermal_bounds,
                "coupling_integrity_pass": bool(energy_bounds and data_bounds and len(connections) >= 20 and resource_status == "PASS"),
                "resource_feedback_closure_status": resource_status,
                "resource_feedback_bridge_health": bridge_health,
                "causal_requirement_evidence_status": causal_evidence.get("status", "INCONCLUSIVE"),
                "coupling_causality_status": "NOT_EVALUATED_PAIRED_RUN_REQUIRED",
                "coupling_claim_scope": "single_run_wiring_numerics_conservation_and_response_observation_only",
                "causal_requirement_evidence": causal_evidence,
                "pdu_payload_feedback_observed": bool(bridge_health.get("payload", {}).get("enabled_transition_observed") and bridge_health.get("payload", {}).get("directional_response_pass")),
                "pdu_comm_feedback_observed": bool(bridge_health.get("comm", {}).get("enabled_transition_observed") and bridge_health.get("comm", {}).get("directional_response_pass")),
                "adcs_dynamic_power_observed": bool(bridge_health.get("adcs", {}).get("stimulus_observed") and bridge_health.get("adcs", {}).get("directional_response_pass")),
                "payload_dynamic_power_observed": bool(bridge_health.get("payload", {}).get("stimulus_observed") and bridge_health.get("payload", {}).get("directional_response_pass")),
                "comm_dynamic_power_observed": bool(bridge_health.get("comm", {}).get("stimulus_observed") and bridge_health.get("comm", {}).get("directional_response_pass")),
                "heater_pdu_power_feedback_observed": bool(bridge_health.get("heater", {}).get("enabled_transition_observed") and bridge_health.get("heater", {}).get("directional_response_pass")),
                "propulsion_enabled": bool(final.get("propulsion.enabled", 0)),
            })
            if bool(final.get("propulsion.enabled", 0)):
                initial_fuel = float(rows[0]["propulsion.fuel_mass_kg"])
                final_fuel = float(final["propulsion.fuel_mass_kg"])
                summary.update({
                    "initial_propellant_kg": initial_fuel,
                    "final_propellant_kg": final_fuel,
                    "propellant_used_kg": max(0.0, initial_fuel - final_fuel),
                    "max_total_thrust_n": max(float(row.get("propulsion.total_thrust_n", 0.0)) for row in rows),
                    "propulsion_burn_observed": any(bool(row.get("propulsion.burn_active", 0)) for row in rows),
                    "propulsion_permission_observed": any(bool(row.get("propulsion.burn_permitted", 0)) for row in rows),
                })

        fault_environment = BSKRLStyleFaultAdapter().summarize(cfg.events, tuple(rows))
        summary.update({
            "runtime_event_count": len(cfg.events),
            "fault_environment_episode_count": fault_environment.episode_count,
            "physical_effect_verified_count": sum(
                1 for episode in fault_environment.episodes
                if bool(episode.get("evidence_summary", {}).get("physical_effect_verified"))
            ),
        })

        metadata = {
            "runtime_manifest": {
                "process": process_name,
                "task": task_name,
                "instantiated_modules": instantiated,
                "connections": connections,
                "recorded_sources": record_sources,
                "excluded_runtime_categories": ["external_solver", "curve_only_proxy"],
            },
            "model_source_boundary": {
                "official_basilisk_native": [item["tag"] for item in instantiated if item["source"] == "basilisk_official_native"],
                "project_deterministic_model": [item["tag"] for item in instantiated if item["source"] in {"basilisk_project_native", "project_deterministic_model"}],
                "engineering_proxy": [item["tag"] for item in instantiated if item["source"] == "engineering_proxy"],
                "external_solver": [],
                # Backward-compatible read aliases retained for v0.5.4.x/v0.5.6.4
                # evidence consumers.  The canonical keys above are authoritative.
                "basilisk_official_native": [item["tag"] for item in instantiated if item["source"] == "basilisk_official_native"],
                "basilisk_project_native": [item["tag"] for item in instantiated if item["source"] in {"basilisk_project_native", "project_deterministic_model"}],
                "external_or_proxy": [item["tag"] for item in instantiated if item["source"] == "engineering_proxy"],
            },
            "runtime_status_semantics": {
                "legacy_status": summary["status"],
                "execution_status": summary["execution_status"],
                "mission_status": summary["mission_status"],
                "overall_status": summary["overall_status"],
            },
            "modifiers_applied_by_adapter": True,
            "fault_environment": fault_environment.to_dict(),
            "native_multi_rate_telemetry": native_multi_rate,
        }
        if vizard_recording_file is not None:
            from .vizard_native_export import verify_native_vizard_recording
            recording_evidence = verify_native_vizard_recording(vizard_recording_file)
            metadata["vizard_native_recording"] = recording_evidence
            summary["vizard_native_recording_status"] = recording_evidence["status"]
        labels = {
            "run_labels": [{"capability_id": cfg.capability_id, "engine": summary["engine"]}],
            "event_labels": [event.to_dict() for event in cfg.events],
            "fault_environment": fault_environment.to_dict(),
        }
        return SimulationResult(summary=summary, trace_rows=tuple(rows), labels=labels, metadata=metadata)


def _config(spec: Mapping[str, Any], capability_id: str, adcs_only: bool) -> UnifiedRuntimeConfig:
    return UnifiedRuntimeConfig(
        capability_id=capability_id,
        duration_s=max(_sim_number(spec, "duration_s", 120.0), 1.0),
        step_s=max(_sim_number(spec, "step_s", 0.2), 1.0e-4),
        sample_s=max(_sim_number(spec, "sample_s", 2.0), _sim_number(spec, "step_s", 0.2)),
        adcs_only=adcs_only,
        values=_parameter_values(spec),
        events=parse_bsk_events(spec),
        required_couplings=tuple(str(item) for item in (_mapping(spec.get("mission")).get("required_couplings") or ())),
        telemetry_streams=tuple(
            dict(item) for item in (_mapping(spec.get("outputs")).get("telemetry_streams") or ())
            if isinstance(item, Mapping)
        ),
    )


class _BaseUnifiedAdapter:
    capability_id = ""
    adcs_only = False

    def validate(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> tuple[ValidationIssue, ...]:
        model = _mapping(spec.get("model"))
        issues: list[ValidationIssue] = []
        if (model.get("capability_id") or spec.get("capability_id")) != self.capability_id:
            issues.append(ValidationIssue("error", "$.model.capability_id", f"must be {self.capability_id!r}", "capability"))
        simulation = _mapping(spec.get("simulation"))
        backend = str(simulation.get("backend") or "basilisk")
        if backend != "basilisk":
            issues.append(ValidationIssue("error", "$.simulation.backend", "must be 'basilisk'", "backend"))
        assurance = _mapping(spec.get("assurance"))
        if assurance.get("allow_proxy") is True:
            issues.append(ValidationIssue("warning", "$.assurance.allow_proxy", "proxy permission is unnecessary; this capability contains no proxy runtime", "assurance"))
        duration_s = _sim_number(spec, "duration_s", 120.0)
        for key, default in (("duration_s", 120.0), ("step_s", 0.2), ("sample_s", 2.0)):
            if _sim_number(spec, key, default) <= 0.0:
                issues.append(ValidationIssue("error", f"$.simulation.{key}", "must be a positive number", "range"))
        if not self.adcs_only:
            values = _parameter_values(spec)
            propulsion_enabled = bool(
                values.get("propulsion_enabled", values.get("enable_propulsion", False))
            ) or any(
                event.effect in {
                    "propulsion_thruster_ignition_failure",
                    "propulsion_burn_impulse_loss",
                }
                for event in parse_bsk_events(spec)
            )
            if propulsion_enabled:
                burn_start_s = float(values.get("propulsion_burn_start_s", 30.0))
                burn_on_time_s = float(values.get("propulsion_burn_on_time_s", 4.0))
                initial_propellant_kg = float(values.get("propulsion_initial_propellant_kg", 1.0))
                tank_capacity_kg = float(values.get("propulsion_tank_capacity_kg", 2.0))
                min_burn_soc = float(values.get("propulsion_min_burn_soc", 0.30))
                if burn_start_s < 0.0 or burn_start_s > duration_s:
                    issues.append(ValidationIssue("error", "$.parameters.values.propulsion_burn_start_s", "must be within simulation duration", "time"))
                if burn_on_time_s <= 0.0:
                    issues.append(ValidationIssue("error", "$.parameters.values.propulsion_burn_on_time_s", "must be positive when propulsion is enabled", "range"))
                if burn_start_s + burn_on_time_s > duration_s + 1.0e-12:
                    issues.append(ValidationIssue("warning", "$.parameters.values.propulsion_burn_on_time_s", "burn extends beyond simulation duration", "time"))
                if initial_propellant_kg < 0.0 or tank_capacity_kg <= 0.0 or initial_propellant_kg > tank_capacity_kg:
                    issues.append(ValidationIssue("error", "$.parameters.values.propulsion_initial_propellant_kg", "must be within [0, propulsion_tank_capacity_kg]", "range"))
                if not 0.0 <= min_burn_soc <= 1.0:
                    issues.append(ValidationIssue("error", "$.parameters.values.propulsion_min_burn_soc", "must be within [0, 1]", "range"))
                if burn_on_time_s < _sim_number(spec, "sample_s", 2.0):
                    issues.append(ValidationIssue("warning", "$.parameters.values.propulsion_burn_on_time_s", "burn is shorter than sample_s; thrust pulse may be absent from sampled trace although fuel use remains recorded", "observability"))
        supported = ADCS_NATIVE_EVENT_EFFECTS if self.adcs_only else WHOLE_NATIVE_EVENT_EFFECTS
        for index, event in enumerate(parse_bsk_events(spec)):
            if event.effect not in supported:
                issues.append(ValidationIssue(
                    "error", f"$.events[{index}].effect",
                    f"effect {event.effect!r} is not migrated into this unified runtime", "event_effect",
                ))
            if event.start_s < 0.0:
                issues.append(ValidationIssue("error", f"$.events[{index}].start_s", "must be non-negative", "time"))
            if event.end_s is not None and event.end_s < event.start_s:
                issues.append(ValidationIssue("error", f"$.events[{index}].end_s", "must not precede start_s", "time"))
            if event.start_s > duration_s:
                issues.append(ValidationIssue("error", f"$.events[{index}].start_s", "must not exceed simulation duration", "time"))
            if event.effect in RW_COMMAND_EVENT_EFFECTS:
                try:
                    wheel_index = int(event.parameters.get("wheel_index", 0))
                except (TypeError, ValueError):
                    wheel_index = -1
                wheel_count = len(_reaction_wheel_axes(str(_parameter_values(spec).get("rw_configuration", "orthogonal_3"))))
                if not 0 <= wheel_index < wheel_count:
                    issues.append(ValidationIssue("error", f"$.events[{index}].parameters.wheel_index", f"must be between 0 and {wheel_count - 1}", "range"))
        return tuple(issues)

    def run(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> SimulationResult:
        return UnifiedNativeRuntime(_config(spec, self.capability_id, self.adcs_only)).run()

    def generate_python(self, spec: Mapping[str, Any], capability: Mapping[str, Any] | None = None) -> str:
        import json
        return (
            "# Auto-generated unified Basilisk runtime\n"
            f"from {self.__class__.__module__} import {self.__class__.__name__}\n\n"
            f"TASK_SPEC = {json.dumps(spec, ensure_ascii=False, indent=2)}\n\n"
            "if __name__ == '__main__':\n"
            f"    print({self.__class__.__name__}().run(TASK_SPEC).summary)\n"
        )

    def output_schema(self, capability: Mapping[str, Any] | None = None) -> dict[str, Any]:
        return {
            "summary": ["status", "runtime_truth_status", "official_module_count", "project_native_module_count", "external_or_proxy_module_count"],
            "trace_fields": ["time_s", "adcs.pointing_error_deg", "adcs.rw.speed_rad_s_*", "propulsion.*"],
            "metadata": ["runtime_manifest", "model_source_boundary", "fault_environment"],
        }


class AdcsUnifiedNativeAdapter(_BaseUnifiedAdapter):
    capability_id = ADCS_UNIFIED_CAPABILITY_ID
    adcs_only = True


class WholeSpacecraftUnifiedNativeAdapter(_BaseUnifiedAdapter):
    capability_id = WHOLE_UNIFIED_CAPABILITY_ID
    adcs_only = False


__all__ = [
    "ADCS_UNIFIED_CAPABILITY_ID", "WHOLE_UNIFIED_CAPABILITY_ID",
    "AdcsUnifiedNativeAdapter", "WholeSpacecraftUnifiedNativeAdapter", "UnifiedNativeRuntime",
]
