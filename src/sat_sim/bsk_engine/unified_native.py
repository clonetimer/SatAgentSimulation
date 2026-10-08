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
    if configuration == "pyramid_4":
        scale = 1.0 / math.sqrt(3.0)
        return tuple(
            tuple(scale * value for value in axis)
            for axis in ((1.0, 1.0, 1.0), (1.0, -1.0, -1.0), (-1.0, 1.0, -1.0), (-1.0, -1.0, 1.0))
        )
    if configuration == "orthogonal_3":
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    raise ValueError(f"unsupported rw_configuration: {configuration!r}")



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
            "adcs.rw.effective_torque_ratio_0": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][0]) / max(abs(float(cfg.values.get("rw_max_torque_nm", 0.2))), 1.0e-12),
            "adcs.rw.effective_torque_ratio_1": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][1]) / max(abs(float(cfg.values.get("rw_max_torque_nm", 0.2))), 1.0e-12),
            "adcs.rw.effective_torque_ratio_2": float(recorder_map["rw_effective_torque_limit"].motorTorque[index][2]) / max(abs(float(cfg.values.get("rw_max_torque_nm", 0.2))), 1.0e-12),
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
        nominal_torque = max(abs(float(cfg.values.get("rw_max_torque_nm", 0.2))), 1.0e-12)
        for wheel_index in range(3, wheel_count):
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

    def __init__(self, config: UnifiedRuntimeConfig) -> None:
        self.config = config

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
        from Basilisk.utilities import SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody, simIncludeRW  # type: ignore

        cfg = self.config
        values = cfg.values
        sim = SimulationBaseClass.SimBaseClass()
        process_name = "UnifiedNativeProcess"
        task_name = "UnifiedNativeTask"
        process = sim.CreateNewProcess(process_name)
        process.addTask(sim.CreateNewTask(task_name, macros.sec2nano(cfg.step_s)))
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
        inertia = [100.0, 80.0, 60.0]
        sc.hub.IHubPntBc_B = [[inertia[0], 0.0, 0.0], [0.0, inertia[1], 0.0], [0.0, 0.0, inertia[2]]]
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
        for index, axis in enumerate(rw_axes):
            rw_factory.create(
                "Honeywell_HR16", list(axis), maxMomentum=50.0,
                Omega=float(100.0 * (index + 1)), u_max=0.2, label=f"RW{index + 1}",
            )
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

        tracker = starTracker.StarTracker()
        tracker.dcm_CB = np.eye(3).tolist()
        tracker.scStateInMsg.subscribeTo(sc.scStateOutMsg)
        add(tracker, "star_tracker", "basilisk_official_native", 90)
        connected("spacecraft.scStateOutMsg", "star_tracker")

        imu = imuSensor.ImuSensor()
        imu.setBodyToPlatformDCM(0.0, 0.0, 0.0)
        imu.sensorPos_B = [0.0, 0.0, 0.0]
        imu.scStateInMsg.subscribeTo(sc.scStateOutMsg)
        add(imu, "imu", "basilisk_official_native", 90)
        connected("spacecraft.scStateOutMsg", "imu")

        sun_sensor = coarseSunSensor.CoarseSunSensor()
        sun_sensor.nHat_B = [0.0, 0.0, -1.0]
        sun_sensor.fov = math.radians(float(values.get("sun_sensor_fov_deg", 80.0)))
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
        magnetometer_model.setBodyToSensorDCM(0.0, 0.0, 0.0)
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
            cfg.events, wheel_count=len(rw_axes), max_torque_nm=float(values.get("rw_max_torque_nm", 0.2))
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
            panel.setPanelParameters([1.0, 0.0, 0.0], float(values.get("solar_panel_area_m2", 2.5)), float(values.get("solar_efficiency", 0.28)))
            panel.sunInMsg.subscribeTo(sun_msg)
            panel.stateInMsg.subscribeTo(sc.scStateOutMsg)
            panel.sunEclipseInMsg.subscribeTo(eclipse_model.eclipseOutMsgs[0])
            add(panel, "solar_panel", "basilisk_official_native", 55)
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
                propulsion_cfg = replace(
                    propulsion_cfg,
                    thruster_command=replace(
                        propulsion_cfg.thruster_command,
                        nominal_on_time_s=burn_on_time_s,
                        active_ids=tuple(range(thruster_count)),
                    ),
                    thruster_physical=replace(
                        propulsion_cfg.thruster_physical,
                        thrust_n=tuple(thrust_n for _ in range(thruster_count)),
                        isp_s=tuple(isp_s for _ in range(thruster_count)),
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
            # Apply user-facing payload initial temperature to the dedicated node.
            payload_node = thermal_nodes["payload"]
            thermal_nodes["payload"] = replace(payload_node, initial_temp_k=float(values.get("initial_payload_temp_k", payload_node.initial_temp_k)))
            conduction_paths = list(base_thermal_cfg.conduction_paths) + [
                ThermalConductionPath("comm", "structure", float(values.get("comm_structure_conductance_w_k", 0.35))),
                ThermalConductionPath("propulsion", "structure", float(values.get("propulsion_structure_conductance_w_k", 0.45))),
            ]
            thermal_cfg = replace(
                base_thermal_cfg,
                nodes=thermal_nodes,
                conduction_paths=conduction_paths,
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
        execution_stops: list[float] = []
        last_live_rows: list[dict[str, Any]] = []
        if cfg.execution_controller is not None:
            cfg.execution_controller.runtime_prepared()
            emitted_count = 0
            while True:
                stop_time_s = cfg.execution_controller.wait_for_advance()
                if stop_time_s is None:
                    break
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
                if stop_time_s <= (execution_stops[-1] if execution_stops else 0.0) or stop_time_s > cfg.duration_s:
                    raise ValueError("controlled execution stop must increase and remain within duration_s")
                previous_stop_s = execution_stops[-1] if execution_stops else 0.0
                for command in cfg.execution_controller.take_commands():
                    apply_interactive_command(command, previous_stop_s, stop_time_s - previous_stop_s)
                sim.ConfigureStopTime(macros.sec2nano(stop_time_s))
                sim.ExecuteSimulation()
                if interactive_one_shot["reset_instrument_rate"]:
                    whole_modules["instrument"].nodeBaudRate = nominal_instrument_rate
                    interactive_one_shot["reset_instrument_rate"] = False
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
            "execution_segment_count": len(execution_stops),
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
