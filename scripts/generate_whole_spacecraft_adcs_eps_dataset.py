#!/usr/bin/env python3
"""Generate paired whole-spacecraft nominal/ADCS-fault time-series data.

Design: mission x condition x Monte-Carlo sample.  A nominal run and every
single-anomaly run in the same ``pair_id`` share the identical spacecraft and
mission parameters.  Every native trace field is exported.
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sat_sim.capability_registry import get_adapter_for_capability, get_capability


CAPABILITY_ID = "whole_spacecraft.unified_native.v1"
SCHEMA_VERSION = "sat-sim.whole-spacecraft-adcs-paired-dataset.v3"
DEFAULT_OUTPUT = Path("data/generated/whole_spacecraft_adcs_paired_dataset")

# Production time base.  The 300 s window provides at least 84 s of healthy
# history and at least 204 s of post-event response for the 28%-32% onset
# range.  A 0.1 s Basilisk task period resolves the ADCS control loop, while a
# 0.5 s recorder period preserves fast sensor/RW signatures without exporting
# every integration point (601 aligned rows per sample).
PRODUCTION_DURATION_S = 300.0
PRODUCTION_STEP_S = 0.1
PRODUCTION_SAMPLE_S = 0.5
EVENT_START_FRACTION_RANGE = (0.28, 0.32)
MIN_PRE_EVENT_S = 60.0
MIN_POST_EVENT_S = 180.0
MIN_PRODUCTION_ROWS = 600


@dataclass(frozen=True)
class Mission:
    mission_id: str
    pointing_error_deg: tuple[float, float]
    propulsion_thrust_n: tuple[float, float]
    payload_enabled: bool
    description: str


@dataclass(frozen=True)
class Condition:
    condition_id: str
    category: str
    effect: str | None
    parameter_ranges: Mapping[str, Any]
    component_source: str
    description: str
    calibration_basis: str
    calibration_source_ids: tuple[str, ...]
    calibration_confidence: str
    severity_band: str


MISSIONS: tuple[Mission, ...] = (
    Mission("attitude_acquisition", (12.0, 18.0), (0.0, 0.0), False, "Large initial attitude error; acquire the inertial target."),
    Mission("steady_pointing_payload", (1.0, 3.0), (0.0, 0.0), True, "Fine inertial pointing with payload/data activity."),
    Mission("propulsive_maneuver", (3.0, 7.0), (0.8, 1.2), False, "Pointing control during a finite propulsion burn."),
)

HR16_MAX_TORQUE_NM = 0.200
HR16_MAX_SPEED_RAD_S = 6000.0 * 2.0 * 3.141592653589793 / 60.0
RW_FRICTION_REFERENCE_SPEED_RAD_S = 200.0 * 2.0 * 3.141592653589793 / 60.0

CALIBRATION_SOURCES: Mapping[str, Mapping[str, str]] = {
    "BASILISK_HR16": {
        "title": "Basilisk simIncludeRW Honeywell_HR16 model",
        "url": "https://avslab.github.io/basilisk/_modules/simIncludeRW.html",
        "use": "u_max=0.200 N m, Omega_max=6000 rpm, Coulomb friction=0.0005 N m",
    },
    "NASA_ORION_FDIR": {
        "title": "Orion GN&C Failure Detection and Isolation Design",
        "url": "https://ntrs.nasa.gov/api/citations/20160001200/downloads/20160001200.pdf",
        "use": "failure-signature approach and source hierarchy; engineering bounds when historical/vendor data are unavailable",
    },
    "NASA_SLS_INS": {
        "title": "SLS Inertial Navigation System Fault Detection Threshold Scheduling",
        "url": "https://ntrs.nasa.gov/api/citations/20180005156/downloads/20180005156.pdf",
        "use": "3.6 deg/hour gyro failure magnitude as a published detection-scale anchor",
    },
    "NASA_CASSINI_RW_DRAG": {
        "title": "Determining Spacecraft Reaction Wheel Friction Parameters",
        "url": "https://ntrs.nasa.gov/citations/20090040058",
        "use": "drag depends on wheel speed and should be estimated as a friction model, including distinct low/high-speed behavior",
    },
    "ESA_RW_DEGRADATION": {
        "title": "ESA Space Debris Mitigation Compliance Verification Guidelines",
        "url": "https://sdup.esoc.esa.int/documents/download/ESA_Space_Debris_Mitigation_Compliance_Verification_Guidelines.pdf",
        "use": "bearing/lubrication degradation manifests as increased dry/viscous friction, power and temperature",
    },
    "PROJECT_L2_FAULTS": {
        "title": "sat component L2-light fault declarations",
        "url": "src/components/reaction_wheel/faults.py; src/components/imu/faults.py",
        "use": "bearing seizure 0.02 N m friction signature, 0.25 torque factor, gyro noise burst factor 8",
    },
}

CONDITIONS: tuple[Condition, ...] = (
    Condition("nominal", "nominal", None, {}, "shared paired baseline", "No injected event.", "No fault parameter.", (), "high", "nominal"),
    Condition("adcs_rw_jamming", "fault", "adcs_rw_jamming", {"wheel_index": (0, 3), "brake_torque_nm": (0.90 * HR16_MAX_TORQUE_NM, HR16_MAX_TORQUE_NM), "lock_tolerance_rad_s": (0.15, 0.35)}, "reaction_wheel.rw_jamming", "Wheel braking/jamming.", "Brake torque is 90-100% of the HR16 model torque limit; lock tolerance is a numerical observability threshold.", ("BASILISK_HR16", "PROJECT_L2_FAULTS"), "medium", "severe"),
    Condition("adcs_rw_motor_failure", "fault", "adcs_rw_motor_failure", {"wheel_index": (0, 3), "torque_scale": (0.0, 0.0)}, "reaction_wheel.rw_motor_failure", "Motor-open torque-path loss.", "Project motor-open signature specifies zero torque authority.", ("PROJECT_L2_FAULTS", "NASA_ORION_FDIR"), "high", "hard_failure"),
    Condition("adcs_gyro_bias_step", "fault", "gyro_bias_step", {"bias_step_deg_s": (3.6 / 3600.0, 36.0 / 3600.0)}, "imu.bias_step", "One-axis gyro bias step.", "Lower bound is the published 3.6 deg/hour SLS detection-scale example; upper bound is a 10x logarithmic severity boundary.  The production post-event window yields about 0.20-2.04 deg of uncompensated angular accumulation.", ("NASA_SLS_INS", "NASA_ORION_FDIR"), "medium", "detectable_to_severe"),
    Condition("adcs_gyro_noise_increase", "degradation", "gyro_noise_increase", {"noise_scale": (8.0, 12.0)}, "imu.noise_burst/gyro_noise_increase", "Gyro noise amplification.", "Factor 8 is the project L2 noise-burst signature; 8-12 adds bounded Monte-Carlo dispersion.", ("PROJECT_L2_FAULTS", "NASA_ORION_FDIR"), "medium", "medium_to_severe"),
    Condition("adcs_rw_bearing_seizure", "degradation", "rw_friction_degradation", {"wheel_index": (0, 3), "drag_nms": (0.005 / RW_FRICTION_REFERENCE_SPEED_RAD_S, 0.020 / RW_FRICTION_REFERENCE_SPEED_RAD_S)}, "reaction_wheel.bearing_seizure", "Bearing seizure represented by viscous drag.", "Equivalent drag is 0.005-0.020 N m at 200 rpm; upper anchor is the project bearing-seizure friction signature.", ("PROJECT_L2_FAULTS", "NASA_CASSINI_RW_DRAG", "ESA_RW_DEGRADATION"), "medium", "incipient_to_severe"),
    Condition("adcs_rw_torque_authority_loss", "degradation", "adcs_rw_torque_authority_loss", {"wheel_index": (0, 3), "remaining_torque_ratio": (0.25, 0.70)}, "reaction_wheel.motor_open/actuator_failure", "Partial torque-authority loss.", "Lower bound matches the project bearing-seizure 0.25 max-torque factor; upper bound represents an observable partial loss.", ("PROJECT_L2_FAULTS", "NASA_ORION_FDIR"), "medium", "medium_to_severe"),
    Condition("adcs_rw_speed_limit", "constraint", "adcs_reaction_wheel_speed_limit", {"wheel_index": (0, 3), "max_speed_rad_s": (5.0, 9.0), "overspeed_brake_gain_nms": (0.015, 0.025)}, "reaction_wheel speed operating constraint", "Reduced wheel speed limit with overspeed braking.", "Sensitivity constraint is deliberately below the model's minimum 100 rpm initial speed so every selected wheel exercises the protection path; it is not a hardware failure distribution.", ("BASILISK_HR16",), "low", "stress_constraint"),
    Condition("adcs_rw_speed_sensor_fault", "fault", "adcs_rw_speed_sensor_fault", {"wheel_index": (0, 3), "speed_bias_rad_s": (15.0, 30.0)}, "reaction_wheel.speed_sensor_fault", "Tachometer bias.", "Brackets the project L2 25 rad/s tachometer-bias signature.", ("PROJECT_L2_FAULTS",), "high", "severe"),
    Condition("adcs_imu_signal_loss", "fault", "adcs_imu_signal_loss", {}, "imu.signal_loss", "All IMU outputs are zero.", "Hard signal-loss signature.", ("NASA_ORION_FDIR",), "medium", "hard_failure"),
    Condition("adcs_imu_gyro_bias_drift", "degradation", "adcs_imu_gyro_bias_drift", {"axis": (0, 2), "drift_deg_s2": (0.0002, 0.0010)}, "imu.gyro_bias_drift", "Linearly growing gyro bias.", "Chosen to grow from an incipient bias into a clearly observable closed-loop signature over the production post-event window.", ("NASA_SLS_INS", "NASA_ORION_FDIR"), "medium", "incipient_to_severe"),
    Condition("adcs_imu_accel_bias_drift", "degradation", "adcs_imu_accel_bias_drift", {"axis": (0, 2), "bias_m_s2": (0.01, 0.05)}, "imu.accel_bias_drift", "Accelerometer bias.", "Engineering sensitivity range; no selected IMU vendor distribution is available.", ("NASA_ORION_FDIR",), "low", "medium"),
    Condition("adcs_imu_accel_noise_increase", "degradation", "adcs_imu_accel_noise_increase", {"noise_scale": (8.0, 12.0)}, "imu.accel_noise_increase", "Accelerometer noise amplification.", "Mirrors the catalogued gyro noise-burst severity family.", ("PROJECT_L2_FAULTS",), "low", "medium_to_severe"),
    Condition("adcs_imu_stuck_at_zero", "fault", "adcs_imu_stuck_at_zero", {}, "imu.stuck_at_zero", "IMU channels stuck at zero.", "Hard substitution failure signature.", ("NASA_ORION_FDIR",), "medium", "hard_failure"),
    Condition("adcs_imu_axis_dropout", "fault", "adcs_imu_axis_dropout", {"axis": (0, 2)}, "imu.axis_dropout", "One IMU axis is forced to zero.", "Project L2 availability-zero signature.", ("PROJECT_L2_FAULTS",), "high", "hard_failure"),
    Condition("adcs_imu_noise_burst", "fault", "adcs_imu_noise_burst", {"noise_scale": (8.0, 12.0)}, "imu.noise_burst", "Finite high-noise gyro signature.", "Project L2 factor-8 noise-burst anchor.", ("PROJECT_L2_FAULTS",), "high", "medium_to_severe"),
    Condition("adcs_star_tracker_signal_loss", "fault", "adcs_star_tracker_signal_loss", {}, "star_tracker.signal_loss", "Star solution invalid.", "Hard availability loss; Sun/Earth TRIAD becomes fallback.", ("NASA_ORION_FDIR",), "medium", "hard_failure"),
    Condition("adcs_star_tracker_bias_drift", "degradation", "adcs_star_tracker_bias_drift", {"axis": (0, 2), "bias_deg": (0.02, 0.10)}, "star_tracker.bias_drift", "Star solution angular bias.", "Engineering range above nominal arcsecond-class knowledge for observable short-run response.", ("NASA_ORION_FDIR",), "low", "medium"),
    Condition("adcs_star_tracker_accuracy_loss", "degradation", "adcs_star_tracker_accuracy_loss", {"noise_arcsec": (20.0, 80.0)}, "star_tracker.accuracy_loss", "Star solution noise increase.", "Spans tens of arcseconds around the project/NASA sensor-performance scale.", ("NASA_ORION_FDIR",), "low", "medium"),
    Condition("adcs_star_tracker_fov_obstruction", "fault", "adcs_star_tracker_fov_obstruction", {}, "star_tracker.fov_obstruction", "Obstructed star field invalidates solution.", "Availability signature from component catalog.", ("PROJECT_L2_FAULTS",), "medium", "hard_failure"),
    Condition("adcs_star_tracker_stuck_at_last", "fault", "adcs_star_tracker_stuck_at_last", {}, "star_tracker.stuck_at_last", "Hold last valid quaternion.", "Substitution failure from component catalog.", ("NASA_ORION_FDIR",), "medium", "hard_failure"),
    Condition("adcs_star_tracker_blinding", "fault", "adcs_star_tracker_blinding", {}, "star_tracker.blinding", "Blinding invalidates star solution.", "Project L2 availability-zero signature.", ("PROJECT_L2_FAULTS",), "high", "hard_failure"),
    Condition("adcs_star_tracker_dropout", "fault", "adcs_star_tracker_dropout", {}, "star_tracker.dropout", "Complete star-solution dropout.", "Project L2 dropout-probability-one signature.", ("PROJECT_L2_FAULTS",), "high", "hard_failure"),
    Condition("adcs_star_tracker_misalignment", "fault", "adcs_star_tracker_misalignment", {"axis": (0, 2), "misalignment_deg": (0.02, 0.10)}, "star_tracker.misalignment", "Star tracker mounting misalignment.", "Bounded short-run angular-bias sensitivity range.", ("PROJECT_L2_FAULTS",), "medium", "medium"),
    Condition("adcs_sun_sensor_signal_loss", "fault", "adcs_sun_sensor_signal_loss", {}, "sun_sensor.signal_loss", "Sun vector invalid.", "Hard availability loss from component catalog.", ("PROJECT_L2_FAULTS",), "medium", "hard_failure"),
    Condition("adcs_sun_sensor_bias_drift", "degradation", "adcs_sun_sensor_bias_drift", {"bias_deg": (0.2, 0.8)}, "sun_sensor.bias_drift", "Sun-vector angular bias proxy.", "Centered on the project contamination 0.5 deg signature.", ("PROJECT_L2_FAULTS",), "medium", "medium"),
    Condition("adcs_sun_sensor_noise_increase", "degradation", "adcs_sun_sensor_noise_increase", {"noise_deg": (0.2, 0.8)}, "sun_sensor.noise_increase", "Sun-vector noise.", "Engineering dispersion around coarse-sensor accuracy scale.", ("NASA_ORION_FDIR",), "low", "medium"),
    Condition("adcs_sun_sensor_saturation", "fault", "adcs_sun_sensor_saturation", {"saturation_level": (0.25, 0.50)}, "sun_sensor.saturation", "Vector channel saturation.", "Bounded component-output sensitivity range.", ("NASA_ORION_FDIR",), "low", "medium"),
    Condition("adcs_sun_sensor_eclipse_blindness", "fault", "adcs_sun_sensor_eclipse_blindness", {}, "sun_sensor.eclipse_blindness", "Sun vector forced invalid.", "Catalogued availability signature.", ("PROJECT_L2_FAULTS",), "medium", "hard_failure"),
    Condition("adcs_sun_sensor_false_eclipse", "fault", "adcs_sun_sensor_false_eclipse", {}, "sun_sensor.false_eclipse", "False eclipse flag invalidates sun vector.", "Project L2 availability-zero signature.", ("PROJECT_L2_FAULTS",), "high", "hard_failure"),
    Condition("adcs_sun_sensor_cell_failure", "fault", "adcs_sun_sensor_cell_failure", {}, "sun_sensor.cell_failure", "Sun-vector sensitivity reduced to 45%.", "Project L2 sensitivity-factor 0.45 signature.", ("PROJECT_L2_FAULTS",), "high", "severe"),
    Condition("adcs_sun_sensor_contamination", "degradation", "adcs_sun_sensor_contamination", {}, "sun_sensor.contamination", "60% sensitivity plus 0.5 deg bias.", "Project L2 contamination signature.", ("PROJECT_L2_FAULTS",), "high", "medium"),
    Condition("adcs_sensor_failure", "fault", "adcs_sensor_failure", {}, "adcs.sensor_failure", "Subsystem sensor-path loss mapped to the primary star solution.", "ADCS compatibility fault; executed as one sensor-path fault, not a multi-fault scenario.", ("NASA_ORION_FDIR",), "medium", "hard_failure"),
    Condition("adcs_control_loop_failure", "fault", "adcs_control_loop_failure", {"command_scale": (0.0, 0.10)}, "adcs.control_loop_failure", "All reaction-wheel commands lose control authority.", "Subsystem-level control-output failure signature.", ("NASA_ORION_FDIR",), "low", "hard_failure"),
    Condition("adcs_actuator_failure", "fault", "adcs_actuator_failure", {"wheel_index": (0, 3)}, "adcs.actuator_failure", "One selected reaction-wheel torque path is disabled.", "ADCS compatibility fault mapped to the selected RW architecture.", ("PROJECT_L2_FAULTS",), "medium", "hard_failure"),
)

FAULT_TYPE_TO_CONDITION: Mapping[str, str] = {
    "reaction_wheel.rw_jamming": "adcs_rw_jamming", "reaction_wheel.rw_motor_failure": "adcs_rw_motor_failure",
    "reaction_wheel.rw_bearing_seizure": "adcs_rw_bearing_seizure", "reaction_wheel.bearing_seizure": "adcs_rw_bearing_seizure",
    "reaction_wheel.motor_open": "adcs_rw_motor_failure", "reaction_wheel.speed_sensor_fault": "adcs_rw_speed_sensor_fault",
    "imu.signal_loss": "adcs_imu_signal_loss", "imu.gyro_bias_drift": "adcs_imu_gyro_bias_drift",
    "imu.accel_bias_drift": "adcs_imu_accel_bias_drift", "imu.gyro_noise_increase": "adcs_gyro_noise_increase",
    "imu.accel_noise_increase": "adcs_imu_accel_noise_increase", "imu.stuck_at_zero": "adcs_imu_stuck_at_zero",
    "imu.bias_step": "adcs_gyro_bias_step", "imu.axis_dropout": "adcs_imu_axis_dropout", "imu.noise_burst": "adcs_imu_noise_burst",
    "star_tracker.signal_loss": "adcs_star_tracker_signal_loss", "star_tracker.bias_drift": "adcs_star_tracker_bias_drift",
    "star_tracker.accuracy_loss": "adcs_star_tracker_accuracy_loss", "star_tracker.fov_obstruction": "adcs_star_tracker_fov_obstruction",
    "star_tracker.stuck_at_last": "adcs_star_tracker_stuck_at_last", "star_tracker.blinding": "adcs_star_tracker_blinding",
    "star_tracker.dropout": "adcs_star_tracker_dropout", "star_tracker.misalignment": "adcs_star_tracker_misalignment",
    "sun_sensor.signal_loss": "adcs_sun_sensor_signal_loss", "sun_sensor.bias_drift": "adcs_sun_sensor_bias_drift",
    "sun_sensor.noise_increase": "adcs_sun_sensor_noise_increase", "sun_sensor.saturation": "adcs_sun_sensor_saturation",
    "sun_sensor.eclipse_blindness": "adcs_sun_sensor_eclipse_blindness", "sun_sensor.false_eclipse": "adcs_sun_sensor_false_eclipse",
    "sun_sensor.cell_failure": "adcs_sun_sensor_cell_failure", "sun_sensor.contamination": "adcs_sun_sensor_contamination",
    "adcs.rw_jamming": "adcs_rw_jamming", "adcs.sensor_failure": "adcs_sensor_failure",
    "adcs.control_loop_failure": "adcs_control_loop_failure", "adcs.actuator_failure": "adcs_actuator_failure",
}

# Catalog audit: declared items are not silently claimed as executable.  The
# runtime conditions above are the executable/observable intersection for the
# selected architecture and enforce exactly one active event per anomaly run.
DECLARED_COMPONENT_FAULTS: Mapping[str, tuple[str, ...]] = {
    "reaction_wheel": ("rw_jamming", "rw_motor_failure", "rw_bearing_seizure", "bearing_seizure", "motor_open", "speed_sensor_fault"),
    "imu": ("signal_loss", "gyro_bias_drift", "accel_bias_drift", "gyro_noise_increase", "accel_noise_increase", "stuck_at_zero", "bias_step", "axis_dropout", "noise_burst"),
    "star_tracker": ("signal_loss", "bias_drift", "accuracy_loss", "fov_obstruction", "stuck_at_last", "blinding", "dropout", "misalignment"),
    "sun_sensor": ("signal_loss", "bias_drift", "noise_increase", "saturation", "eclipse_blindness", "false_eclipse", "cell_failure", "contamination"),
}
SUBSYSTEM_SCENARIO_AUDIT: Mapping[str, str] = {
    "rw_bearing_seizure": "included as one rw_friction_degradation event",
    "mtb_cmg_actuator_fault": "excluded: MTB/CMG are outside selected RW-only architecture and scenario is multi-fault",
    "sensor_dropout_bias_fault": "excluded: multi-fault and its component injectors are not wired into unified whole-spacecraft runtime",
    "combined_adcs_fault": "excluded: multi-fault",
}
DECLARED_NOT_EXECUTABLE: Mapping[str, str] = {
    "earth_sensor.*": "project earth sensor has no declared fault catalog yet",
    "mtb.*": "excluded because the selected architecture uses reaction wheels and no magnetorquer",
    "cmg.*": "excluded because the selected architecture uses reaction wheels and no CMG",
    "magnetometer.*": "retained only as internal legacy telemetry, not part of the selected attitude-determination sensor set",
}

REQUIRED_MODULE_GROUPS: Mapping[str, tuple[str, ...]] = {
    "platform_orbit": ("spacecraft", "eclipse"),
    "adcs": ("reaction_wheels", "imu", "star_tracker", "coarse_sun_sensor", "project_sun_direction_sensor", "project_earth_horizon_sensor", "project_adcs_sensor_fusion"),
    "eps": ("solar_panel", "battery", "project_eps_pdu"),
    "payload": ("payload_instrument",),
    "comm_data": ("storage", "native_downlink_handling"),
    "thermal": ("thermal_network",),
    "propulsion": ("propulsion_thrusters", "propulsion_fuel_tank"),
}


def _uniform(rng: random.Random, bounds: Sequence[float]) -> float:
    return rng.uniform(float(bounds[0]), float(bounds[1]))


def sample_event_parameters(condition: Condition, rng: random.Random) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, bounds in condition.parameter_ranges.items():
        if name in {"wheel_index", "axis"}:
            result[name] = rng.randint(int(bounds[0]), int(bounds[1]))
        elif name == "bias_step_deg_s":
            vector = [0.0, 0.0, 0.0]
            vector[rng.randrange(3)] = _uniform(rng, bounds) * (-1.0 if rng.random() < 0.5 else 1.0)
            result[name] = vector
        else:
            result[name] = _uniform(rng, bounds)
    return result


def sample_spacecraft_parameters(mission: Mission, rng: random.Random, pair_seed: int, duration_s: float, step_s: float) -> dict[str, Any]:
    maneuver = mission.mission_id == "propulsive_maneuver"
    burn_start = min(duration_s * 0.40, max(0.0, duration_s - step_s))
    burn_time = max(step_s, min(duration_s * 0.10, duration_s - burn_start))
    return {
        "simulation_seed": pair_seed,
        "rw_configuration": "pyramid_4",
        "adcs_sensor_suite": "star_tracker_imu_plus_backside_sun_earth",
        "initial_pointing_error_deg": _uniform(rng, mission.pointing_error_deg),
        "inclination_deg": rng.uniform(32.0, 38.0),
        "initial_true_anomaly_rad": rng.uniform(-0.08, 0.08),
        "initial_soc": rng.uniform(0.58, 0.66),
        "battery_capacity_wh": rng.uniform(152.0, 168.0),
        "solar_panel_area_m2": rng.uniform(2.40, 2.60),
        "solar_efficiency": rng.uniform(0.266, 0.294),
        "bus_power_w": rng.uniform(17.1, 18.9),
        "adcs_power_w": rng.uniform(11.4, 12.6),
        "payload_power_w": rng.uniform(36.1, 39.9) if mission.payload_enabled else 0.0,
        "payload_data_rate_bps": rng.uniform(2.35e6, 2.65e6) if mission.payload_enabled else 0.0,
        "downlink_power_w": rng.uniform(15.2, 16.8),
        "propulsion_enabled": True,
        "propulsion_thrust_n": _uniform(rng, mission.propulsion_thrust_n),
        "propulsion_burn_start_s": burn_start,
        "propulsion_burn_on_time_s": burn_time,
        "mission_profile": mission.mission_id,
    }


def build_task_spec(mission: Mission, condition: Condition, *, sample_index: int, base_seed: int, duration_s: float, step_s: float, sample_s: float) -> tuple[dict[str, Any], dict[str, Any]]:
    mission_index = MISSIONS.index(mission)
    condition_index = CONDITIONS.index(condition)
    pair_seed = base_seed + 100_000 * mission_index + sample_index
    pair_rng = random.Random(pair_seed)
    parameters = sample_spacecraft_parameters(mission, pair_rng, pair_seed, duration_s, step_s)
    pair_id = f"{mission.mission_id}:mc_{sample_index:02d}"
    sample_id = f"{mission.mission_id}:{condition.condition_id}:mc_{sample_index:02d}"
    events: dict[str, list[dict[str, Any]]] = {"faults": [], "degradations": [], "constraints": []}
    event_parameters: dict[str, Any] = {}
    event_start_s: float | None = None
    if condition.effect is not None:
        event_rng = random.Random(pair_seed + 10_000_000 + 10_000 * condition_index)
        event_start_s = duration_s * event_rng.uniform(*EVENT_START_FRACTION_RANGE)
        event_parameters = sample_event_parameters(condition, event_rng)
        events[f"{condition.category}s"].append({
            "id": sample_id, "effect": condition.effect, "target": "whole_spacecraft",
            "start_s": event_start_s, "end_s": None, "parameters": event_parameters,
        })
    spec = {
        "task": {"id": sample_id, "name": sample_id},
        "model": {"capability_id": CAPABILITY_ID},
        "simulation": {"level": "whole_spacecraft", "duration_s": duration_s, "step_s": step_s, "sample_s": sample_s, "backend": "basilisk"},
        "assurance": {"allow_proxy": False},
        "parameters": {"values": parameters}, "events": events, "outputs": {"plots": []},
    }
    metadata = {
        "sample_id": sample_id, "pair_id": pair_id, "sample_index": sample_index,
        "pair_seed": pair_seed, "mission_id": mission.mission_id,
        "condition_id": condition.condition_id, "event_category": condition.category,
        "effect": condition.effect or "nominal", "event_start_s": event_start_s,
        "event_parameters": event_parameters, "spacecraft_parameters": parameters,
        "calibration": {
            "basis": condition.calibration_basis,
            "source_ids": list(condition.calibration_source_ids),
            "confidence": condition.calibration_confidence,
            "severity_band": condition.severity_band,
        },
    }
    return spec, metadata


def validate_timing_settings(duration_s: float, step_s: float, sample_s: float, *, allow_short_run: bool = False) -> dict[str, Any]:
    """Validate a numerically aligned, fault-observable dataset time base."""
    values = {"duration_s": duration_s, "step_s": step_s, "sample_s": sample_s}
    if any(not isinstance(value, (int, float)) or isinstance(value, bool) or value <= 0 for value in values.values()):
        raise ValueError("duration_s, step_s and sample_s must be positive numbers")
    if step_s > sample_s:
        raise ValueError("step_s must not exceed sample_s")

    def require_integer_ratio(numerator: float, denominator: float, label: str) -> None:
        ratio = numerator / denominator
        if abs(ratio - round(ratio)) > 1.0e-9:
            raise ValueError(f"{label} must be an integer ratio for an aligned time grid")

    require_integer_ratio(sample_s, step_s, "sample_s / step_s")
    require_integer_ratio(duration_s, sample_s, "duration_s / sample_s")
    expected_rows = int(round(duration_s / sample_s)) + 1
    earliest_event_s = duration_s * EVENT_START_FRACTION_RANGE[0]
    latest_event_s = duration_s * EVENT_START_FRACTION_RANGE[1]
    min_post_event_s = duration_s - latest_event_s
    if not allow_short_run:
        problems = []
        if step_s > PRODUCTION_STEP_S:
            problems.append(f"step_s must be <= {PRODUCTION_STEP_S:g} s")
        if sample_s > PRODUCTION_SAMPLE_S:
            problems.append(f"sample_s must be <= {PRODUCTION_SAMPLE_S:g} s")
        if earliest_event_s < MIN_PRE_EVENT_S:
            problems.append(f"pre-event window must be >= {MIN_PRE_EVENT_S:g} s")
        if min_post_event_s < MIN_POST_EVENT_S:
            problems.append(f"post-event window must be >= {MIN_POST_EVENT_S:g} s")
        if expected_rows < MIN_PRODUCTION_ROWS:
            problems.append(f"each sample must contain >= {MIN_PRODUCTION_ROWS} rows")
        if problems:
            raise ValueError("production timing requirements failed: " + "; ".join(problems) + "; use --allow-short-run only for tests/smoke runs")
    return {
        **values,
        "expected_row_count": expected_rows,
        "event_start_fraction_range": list(EVENT_START_FRACTION_RANGE),
        "event_start_s_range": [earliest_event_s, latest_event_s],
        "minimum_pre_event_s": earliest_event_s,
        "minimum_post_event_s": min_post_event_s,
        "integration_steps": int(round(duration_s / step_s)),
        "integration_steps_per_sample": int(round(sample_s / step_s)),
        "production_gate_bypassed": bool(allow_short_run),
    }


def select_output_row(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> dict[str, Any]:
    """Add dataset labels while preserving every whole-spacecraft trace field."""
    effect = metadata["effect"]
    selected = {
        "sample_id": metadata["sample_id"], "pair_id": metadata["pair_id"],
        "mission_id": metadata["mission_id"], "condition_id": metadata["condition_id"],
        "event_category": metadata["event_category"], "effect": effect,
        "is_nominal": int(effect == "nominal"),
        "event_active": int(effect != "nominal" and effect in str(row.get("event.active_effects", "")).split(",")),
    }
    selected.update(row)
    return selected


def _write_csv(path: Path, rows: Sequence[Mapping[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty telemetry: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader(); writer.writerows(rows)
        handle.flush()
    temporary.replace(path)


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
    temporary.replace(path)


def _load_resumable_sample(sample_dir: Path, item: Mapping[str, Any], output_root: Path) -> tuple[dict[str, Any], set[str], dict[str, Any] | None] | None:
    """Return a verified completed sample, or None for a missing/partial sample."""
    telemetry_path = sample_dir / "telemetry.csv"
    metadata_path = sample_dir / "sample_metadata.json"
    if not telemetry_path.is_file() or not metadata_path.is_file():
        return None
    try:
        payload = json.loads(metadata_path.read_text(encoding="utf-8"))
        if payload.get("sample_id") != item["metadata"]["sample_id"]:
            return None
        if payload.get("task_spec") != item["spec"]:
            return None
        with telemetry_path.open(newline="", encoding="utf-8") as handle:
            reader = csv.reader(handle)
            fields = next(reader)
            first_row = next(reader, None)
        if not fields or first_row is None or int(payload.get("row_count", 0)) <= 0:
            return None
        record_keys = set(item["metadata"]) | {
            "row_count", "field_count", "telemetry_file", "execution_status", "mission_status", "overall_status",
        }
        record = {key: payload[key] for key in record_keys if key in payload}
        expected_path = str(telemetry_path.relative_to(output_root))
        if record.get("telemetry_file") != expected_path:
            return None
        return record, set(fields), payload.get("module_coverage")
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def _progress_manifest(summary: Mapping[str, Any], output_root: Path, records: Sequence[Mapping[str, Any]], coverage: Mapping[str, Any] | None, fields: set[str], total: int, *, status: str) -> dict[str, Any]:
    return {
        **summary, "status": status, "resume_supported": True,
        "output_root": str(output_root.resolve()), "completed_run_count": len(records),
        "remaining_run_count": max(0, total - len(records)), "module_coverage": coverage,
        "telemetry_fields": sorted(fields), "telemetry_field_count": len(fields), "samples": list(records),
    }


def _module_coverage(metadata: Mapping[str, Any]) -> dict[str, Any]:
    tags = {str(item.get("tag")) for item in metadata.get("runtime_manifest", {}).get("instantiated_modules", [])}
    groups = {name: {"required_tags": list(required), "present": all(tag in tags for tag in required)} for name, required in REQUIRED_MODULE_GROUPS.items()}
    forbidden_truth_navigation_tags = sorted(tags & {"simple_nav", "simpleNav", "SimpleNav"})
    return {
        "all_required_groups_present": all(item["present"] for item in groups.values()),
        "no_truth_navigation_module": not forbidden_truth_navigation_tags,
        "forbidden_truth_navigation_tags": forbidden_truth_navigation_tags,
        "groups": groups, "instantiated_module_tags": sorted(tags),
    }


def build_plan(missions: Iterable[Mission] = MISSIONS, conditions: Iterable[Condition] = CONDITIONS, *, samples_per_combination: int, base_seed: int, duration_s: float, step_s: float, sample_s: float) -> list[dict[str, Any]]:
    if not 5 <= samples_per_combination <= 10:
        raise ValueError("samples_per_combination must be within [5, 10]")
    plan = []
    for mission in missions:
        for condition in conditions:
            for sample_index in range(samples_per_combination):
                spec, metadata = build_task_spec(mission, condition, sample_index=sample_index, base_seed=base_seed, duration_s=duration_s, step_s=step_s, sample_s=sample_s)
                plan.append({"mission": mission, "condition": condition, "spec": spec, "metadata": metadata})
    return plan


def _select(items: Sequence[Any], names: Sequence[str] | None, key: str) -> tuple[Any, ...]:
    if not names:
        return tuple(items)
    index = {getattr(item, key): item for item in items}
    unknown = sorted(set(names) - set(index))
    if unknown:
        raise ValueError(f"unknown values: {unknown}; available={sorted(index)}")
    return tuple(index[name] for name in names)


def generate(args: argparse.Namespace) -> dict[str, Any]:
    missions = _select(MISSIONS, args.missions, "mission_id")
    conditions = _select(CONDITIONS, args.conditions, "condition_id")
    timing_design = validate_timing_settings(
        args.duration_s, args.step_s, args.sample_s,
        allow_short_run=bool(getattr(args, "allow_short_run", False)),
    )
    plan = build_plan(missions, conditions, samples_per_combination=args.samples_per_combination, base_seed=args.seed, duration_s=args.duration_s, step_s=args.step_s, sample_s=args.sample_s)
    anomaly_count = sum(item.condition_id != "nominal" for item in conditions)
    adapter = get_adapter_for_capability(CAPABILITY_ID)
    capability = get_capability(CAPABILITY_ID).data
    validation_warning_count = 0
    for item in plan:
        issues = adapter.validate(item["spec"], capability)
        errors = [issue for issue in issues if issue.severity == "error"]
        if errors:
            raise ValueError(f"invalid generated task {item['metadata']['sample_id']}: {errors}")
        validation_warning_count += sum(issue.severity == "warning" for issue in issues)
    summary = {
        "schema_version": SCHEMA_VERSION, "capability_id": CAPABILITY_ID,
        "design": "mission_x_condition_x_monte_carlo_sample_with_shared_nominal_pairing",
        "mission_count": len(missions), "condition_count": len(conditions), "anomaly_condition_count": anomaly_count,
        "samples_per_combination": args.samples_per_combination, "total_run_count": len(plan),
        "timing_design": timing_design,
        "missions": [asdict(item) for item in missions], "conditions": [asdict(item) for item in conditions],
        "selected_adcs_architecture": {"actuators": "four_reaction_wheel_tetrahedral_pyramid", "primary_attitude_sensors": ["star_tracker", "imu"], "supplementary_backside_sensors": ["coarse_sun_sensor", "earth_horizon_sensor"], "excluded_equivalent_actuators": ["cmg", "magnetorquer"]},
        "output_field_policy": "dataset_labels_plus_all_native_whole_spacecraft_trace_fields",
        "plan_validation": {"status": "PASS", "validated_run_count": len(plan), "warning_count": validation_warning_count},
        "calibration_version": "reference-informed-v1",
        "calibration_constants": {
            "hr16_max_torque_nm": HR16_MAX_TORQUE_NM,
            "hr16_max_speed_rad_s": HR16_MAX_SPEED_RAD_S,
            "rw_friction_reference_speed_rad_s": RW_FRICTION_REFERENCE_SPEED_RAD_S,
        },
        "calibration_sources": CALIBRATION_SOURCES,
        "fault_catalog_audit": {"declared_component_faults": DECLARED_COMPONENT_FAULTS, "subsystem_scenarios": SUBSYSTEM_SCENARIO_AUDIT, "declared_not_executable": DECLARED_NOT_EXECUTABLE},
        "fault_type_to_condition": FAULT_TYPE_TO_CONDITION,
    }
    if args.dry_run:
        return {**summary, "status": "dry_run", "samples": [item["metadata"] for item in plan]}
    output_root = Path(args.output)
    resume = bool(getattr(args, "resume", False))
    if output_root.exists() and any(output_root.iterdir()) and not resume:
        raise FileExistsError(f"output directory is not empty: {output_root}")
    output_root.mkdir(parents=True, exist_ok=True)
    records: list[dict[str, Any]] = []; coverage: dict[str, Any] | None = None; field_union: set[str] = set()
    for number, item in enumerate(plan, 1):
        metadata = dict(item["metadata"])
        sample_dir = output_root / metadata["mission_id"] / metadata["condition_id"] / f"sample_{metadata['sample_index']:02d}"
        if resume:
            resumed = _load_resumable_sample(sample_dir, item, output_root)
            if resumed is not None:
                record, fields, stored_coverage = resumed
                records.append(record); field_union.update(fields); coverage = coverage or stored_coverage
                print(f"[{number}/{len(plan)}] SKIP complete {metadata['sample_id']}", file=sys.stderr, flush=True)
                continue
        print(f"[{number}/{len(plan)}] {metadata['sample_id']}", file=sys.stderr, flush=True)
        result = adapter.run(item["spec"], capability)
        rows = [select_output_row(row, metadata) for row in result.trace_rows]; field_union.update(key for row in rows for key in row)
        telemetry_path = sample_dir / "telemetry.csv"; _write_csv(telemetry_path, rows)
        current_coverage = _module_coverage(result.metadata)
        if not current_coverage["all_required_groups_present"] or not current_coverage["no_truth_navigation_module"]:
            missing = [name for name, detail in current_coverage["groups"].items() if not detail["present"]]
            raise RuntimeError(f"whole-spacecraft module coverage failed; missing groups={missing}")
        coverage = coverage or current_coverage
        record = {**metadata, "row_count": len(rows), "field_count": len(rows[0]), "telemetry_file": str(telemetry_path.relative_to(output_root)), "execution_status": result.summary.get("execution_status"), "mission_status": result.summary.get("mission_status"), "overall_status": result.summary.get("overall_status")}
        _write_json(sample_dir / "sample_metadata.json", {**record, "task_spec": item["spec"], "simulation_summary": result.summary, "module_coverage": current_coverage}); records.append(record)
        progress = _progress_manifest(summary, output_root, records, coverage, field_union, len(plan), status="in_progress")
        _write_json(output_root / "dataset_manifest.progress.json", progress); _write_csv(output_root / "sample_index.progress.csv", records)
    manifest = _progress_manifest(summary, output_root, records, coverage, field_union, len(plan), status="complete")
    _write_json(output_root / "dataset_manifest.json", manifest); _write_csv(output_root / "sample_index.csv", records)
    _write_json(output_root / "dataset_manifest.progress.json", manifest); _write_csv(output_root / "sample_index.progress.csv", records)
    return manifest


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT)); parser.add_argument("--samples-per-combination", type=int, default=5, help="5 to 10 (default: 5)")
    parser.add_argument("--seed", type=int, default=20260810)
    parser.add_argument("--duration-s", type=float, default=PRODUCTION_DURATION_S, help="simulation duration (production default: 300 s)")
    parser.add_argument("--step-s", type=float, default=PRODUCTION_STEP_S, help="Basilisk integration/control task period (production default: 0.1 s)")
    parser.add_argument("--sample-s", type=float, default=PRODUCTION_SAMPLE_S, help="telemetry recorder period (production default: 0.5 s)")
    parser.add_argument("--missions", nargs="+"); parser.add_argument("--conditions", nargs="+"); parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--resume", action="store_true", help="resume an interrupted run and skip verified complete samples")
    parser.add_argument("--allow-short-run", action="store_true", help="bypass production observability limits for tests/smoke runs only")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    try:
        result = generate(parse_args(argv))
    except (FileExistsError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr); return 2
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
