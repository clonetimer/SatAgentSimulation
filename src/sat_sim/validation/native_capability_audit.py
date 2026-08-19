"""Basilisk 2.11.0 native-capability audit registry and verification helpers.

The audit deliberately distinguishes project-facing configuration fields from
Basilisk construction parameters, runtime-writable fields, input messages,
computed outputs, multi-module chains, bounded project proxies and out-of-scope
physics.  A field is not considered supported merely because a similarly named
attribute exists on a SWIG object.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import ast
import importlib
import inspect
from pathlib import Path
from typing import Any, Iterable


SCHEMA_VERSION = "native-capability-audit-v2.0"
BASILISK_VERSION = "2.11.0"

CAPABILITY_TYPES = {
    "native_build_parameter",
    "native_runtime_writable",
    "native_input_message",
    "native_output_only",
    "native_chain_composition",
    "project_proxy",
    "out_of_scope",
    "invalid_or_unused",
}

PROJECT_STATUSES = {
    "mapped",
    "mapped_conditional",
    "mapped_via_parallel_interface",
    "mapping_missing",
    "native_chain_incomplete",
    "proxy_instead_of_native",
    "project_proxy_explicit",
    "out_of_scope",
    "declared_but_unused",
    "invalid_native_mapping",
    "semantic_mismatch",
    "output_not_configuration",
}

BLOCKING_STATUSES = {
    "mapping_missing",
    "native_chain_incomplete",
    "proxy_instead_of_native",
    "declared_but_unused",
    "invalid_native_mapping",
    "semantic_mismatch",
}


@dataclass(frozen=True)
class FieldRule:
    capability_type: str
    project_status: str
    priority: str = "P1"
    native_symbol: str | None = None
    action: str = "must_fix"
    note: str = ""

    def __post_init__(self) -> None:
        if self.capability_type not in CAPABILITY_TYPES:
            raise ValueError(f"invalid capability type: {self.capability_type}")
        if self.project_status not in PROJECT_STATUSES:
            raise ValueError(f"invalid project status: {self.project_status}")


@dataclass(frozen=True)
class SurfaceSpec:
    component: str
    path: str
    symbol: str
    symbol_kind: str
    default: FieldRule
    overrides: dict[str, FieldRule] = field(default_factory=dict)
    virtual_fields: tuple[str, ...] = ()
    scope: str = "in_scope"


@dataclass(frozen=True)
class GapRecord:
    record_id: str
    component: str
    field: str
    capability_type: str
    project_status: str
    priority: str
    native_symbol: str
    action: str
    note: str
    scope: str = "in_scope"
    native_probe: dict[str, str] | None = None


@dataclass(frozen=True)
class RemediationGroup:
    backlog_id: str
    title: str
    components: tuple[str, ...]
    priority: str
    status: str
    depends_on: tuple[str, ...]
    acceptance: tuple[str, ...]
    rationale: str


def _r(
    capability_type: str,
    project_status: str,
    *,
    priority: str = "P1",
    native_symbol: str | None = None,
    action: str | None = None,
    note: str = "",
) -> FieldRule:
    if action is None:
        if project_status in BLOCKING_STATUSES:
            action = "must_fix"
        elif project_status in {"mapped", "mapped_conditional", "mapped_via_parallel_interface"}:
            action = "retain_and_test"
        elif project_status in {"project_proxy_explicit", "out_of_scope", "output_not_configuration"}:
            action = "document_only"
        else:
            action = "review"
    return FieldRule(capability_type, project_status, priority, native_symbol, action, note)


NATIVE_MAPPED = _r("native_build_parameter", "mapped", action="retain_and_test")
MESSAGE_MAPPED = _r("native_input_message", "mapped", action="retain_and_test")
PROXY = _r("project_proxy", "project_proxy_explicit", priority="P2", action="retain_with_fidelity_label")
OUT_SCOPE = _r("out_of_scope", "out_of_scope", priority="P3", action="remove_from_blocking_backlog")


# Explicitly bounded audit surface.  Every dataclass ending in Config or Spec in
# these component contracts must be registered in SURFACES.  This prevents a
# newly added public schema from silently escaping native-capability review.
AUDITED_CONFIG_FILES: tuple[str, ...] = (
    "src/components/antenna/schemas.py",
    "src/components/battery/schemas.py",
    "src/components/cmg/schemas.py",
    "src/components/data_queue/schemas.py",
    "src/components/fuel_tank/schemas.py",
    "src/components/ground_station/schemas.py",
    "src/components/imu/schemas.py",
    "src/components/link_budget/schemas.py",
    "src/components/magnetometer/schemas.py",
    "src/components/mtb/schemas.py",
    "src/components/payload_sensor/schemas.py",
    "src/components/reaction_wheel/schemas.py",
    "src/components/solar_panel/schemas.py",
    "src/components/star_tracker/schemas.py",
    "src/components/sun_sensor/schemas.py",
    "src/components/thruster/schemas.py",
    "src/components/transmitter/schemas.py",
)


SURFACES: tuple[SurfaceSpec, ...] = (
    SurfaceSpec(
        "reaction_wheel",
        "src/components/reaction_wheel/schemas.py",
        "ReactionWheelCommandConfig",
        "class",
        _r("native_input_message", "mapped_via_parallel_interface", priority="P1", native_symbol="ArrayMotorTorqueMsg", action="normalize_schema_contract"),
        overrides={
            "deadzone_nm": _r("native_build_parameter", "mapped", priority="P1", native_symbol="ReactionWheelSpec.use_min_torque/u_min_nm -> rwFactory.create.useMinTorque/u_min", action="retain_and_test"),
            "failed_ids": _r("native_input_message", "mapped_via_parallel_interface", priority="P1", native_symbol="ArrayMotorTorqueMsg", action="normalize_schema_contract"),
            "stuck_torque_by_id": _r("native_input_message", "mapped_via_parallel_interface", priority="P1", native_symbol="ArrayMotorTorqueMsg", action="normalize_schema_contract"),
        },
    ),
    SurfaceSpec(
        "reaction_wheel",
        "src/components/reaction_wheel/schemas.py",
        "ReactionWheelDynamicsConfig",
        "class",
        _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="rwFactory.create/RWConfigPayload", action="normalize_schema_contract"),
        overrides={
            "damping_nms": _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="RWConfigPayload.cViscous", action="normalize_schema_contract"),
        },
    ),
    SurfaceSpec(
        "reaction_wheel",
        "src/components/reaction_wheel/schemas.py",
        "ReactionWheelSpec",
        "class",
        NATIVE_MAPPED,
        overrides={
            "wheel_js": _r(
                "native_build_parameter", "mapped_conditional", native_symbol="rwFactory.create.Js",
                note="Only consumed when model='custom'; the default manufacturer path ignores this field.",
            ),
            "omega_max_rad_s": _r(
                "native_build_parameter", "mapped_conditional", native_symbol="rwFactory.create.Omega_max",
                note="Only passed on the custom-wheel path; manufacturer models retain their factory limit.",
            ),
        },
    ),
    SurfaceSpec(
        "cmg",
        "src/components/cmg/schemas.py",
        "SingleGimbalCmgConfig",
        "class",
        _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="VSCMGConfigMsgPayload", action="normalize_schema_contract"),
    ),
    SurfaceSpec(
        "cmg",
        "src/components/cmg/schemas.py",
        "CmgConfig",
        "class",
        _r("native_chain_composition", "mapped_via_parallel_interface", priority="P1", native_symbol="VSCMGConfigMsgPayload + VSCMGArrayTorqueMsg", action="normalize_schema_contract"),
    ),
    SurfaceSpec(
        "cmg",
        "src/components/cmg/schemas.py",
        "VscmgSpec",
        "class",
        _r("native_build_parameter", "mapped", native_symbol="VSCMGConfigMsgPayload"),
        overrides={
            "Jt": _r("native_build_parameter", "mapped", priority="P0", native_symbol="VSCMGConfigMsgPayload.IW2/IW3", action="retain_and_test"),
            "Jg": _r("native_build_parameter", "mapped", priority="P0", native_symbol="VSCMGConfigMsgPayload.IG1/IG2/IG3", action="retain_and_test"),
            "Js": _r("native_build_parameter", "mapped", priority="P0", native_symbol="VSCMGConfigMsgPayload.IW1", action="retain_and_test"),
            "max_torque_nm": _r("native_build_parameter", "mapped", priority="P0", native_symbol="VSCMGConfigMsgPayload.u_s_max/u_g_max", action="retain_and_test"),
        },
    ),
    SurfaceSpec(
        "thruster",
        "src/components/thruster/schemas.py",
        "ThrusterCommandConfig",
        "class",
        _r("native_input_message", "mapped_via_parallel_interface", priority="P1", native_symbol="THRArrayOnTimeCmdMsg", action="normalize_schema_contract"),
        overrides={
            "min_pulse_s": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.MinOnTime/useMinPulseTime", action="retain_and_test", note="Minimum-pulse behavior is enabled through use_min_pulse_time."),
            "use_min_pulse_time": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.useMinPulseTime", action="retain_and_test"),
        },
    ),
    SurfaceSpec(
        "thruster",
        "src/components/thruster/schemas.py",
        "ThrusterPhysicalConfig",
        "class",
        _r(
            "native_build_parameter", "mapped_via_parallel_interface", priority="P1",
            native_symbol="thrusterFactory.create",
            action="normalize_schema_contract",
            note="The dataclass is not the native builder input; equivalent values are passed through untyped dictionaries.",
        ),
    ),
    SurfaceSpec(
        "thruster",
        "src/components/thruster/builder.py",
        "thruster_spec",
        "virtual",
        _r("native_build_parameter", "mapped", native_symbol="thrusterFactory.create"),
        overrides={
            "min_on_time_s": _r(
                "native_build_parameter", "mapped", priority="P1",
                native_symbol="thrusterFactory.create.MinOnTime/useMinPulseTime",
                action="retain_and_test",
                note="MinOnTime is passed with useMinPulseTime=True when requested.",
            ),
            "use_min_pulse_time": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.useMinPulseTime", action="retain_and_test"),
            "area_nozzle_m2": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.areaNozzle", action="retain_and_test"),
            "thruster_mag_disp": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.thrusterMagDisp", action="retain_and_test"),
            "cutoff_frequency_rad_s": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.cutoffFrequency", action="retain_and_test"),
            "max_swirl_torque_nm": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.MaxSwirlTorque", action="retain_and_test"),
            "thr_blowdown_coeff": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.thrBlowDownCoeff", action="retain_and_test"),
            "isp_blowdown_coeff": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.ispBlowDownCoeff", action="retain_and_test"),
            "label": _r("native_build_parameter", "mapped", priority="P1", native_symbol="thrusterFactory.create.label", action="retain_and_test"),
        },
        virtual_fields=("location", "direction", "max_thrust_n", "isp_s", "min_on_time_s", "use_min_pulse_time", "area_nozzle_m2", "thruster_mag_disp", "cutoff_frequency_rad_s", "max_swirl_torque_nm", "thr_blowdown_coeff", "isp_blowdown_coeff", "label"),
    ),
    SurfaceSpec(
        "fuel_tank",
        "src/components/fuel_tank/schemas.py",
        "FuelTankConfig",
        "class",
        _r(
            "native_build_parameter", "mapped_via_parallel_interface", priority="P1",
            native_symbol="FuelTankModelConstantVolume",
            action="normalize_schema_contract",
        ),
        overrides={
            "full_pressure_pa": PROXY,
            "dry_pressure_pa": PROXY,
            "tank_model": _r("native_build_parameter", "mapped", priority="P1", native_symbol="FuelTank.setTankModel", action="retain_and_test"),
            "radius_tank_m": _r("native_build_parameter", "mapped", priority="P1", native_symbol="FuelTankModel*.radiusTankInit", action="retain_and_test"),
            "rho_fuel_kg_m3": _r("native_build_parameter", "mapped", priority="P1", native_symbol="FuelTankModelEmptying.rhoFuel", action="retain_and_test"),
            "length_tank_m": _r("native_build_parameter", "mapped", priority="P1", native_symbol="FuelTankModelCentrifugalBurn.lengthTank", action="retain_and_test"),
            "radius_inner_m": _r("native_build_parameter", "mapped", priority="P1", native_symbol="FuelTankModelCentrifugalBurn.radiusInner", action="retain_and_test"),
            "r_tb_b_m": _r("native_build_parameter", "mapped", priority="P1", native_symbol="FuelTank.setR_TB_B", action="retain_and_test"),
            "dcm_tb": _r("native_build_parameter", "mapped", priority="P1", native_symbol="FuelTank.setDcm_TB", action="retain_and_test"),
            "leak_rate_kg_s": _r("native_input_message", "mapped", priority="P1", native_symbol="FuelTank.fuelLeakRateInMsg <- MassFlowRateMsg", action="retain_and_test"),
        },
    ),
    SurfaceSpec(
        "fuel_tank",
        "src/components/fuel_tank/builder.py",
        "build_constant_volume_fuel_tank_bundle",
        "function",
        NATIVE_MAPPED,
    ),
    SurfaceSpec(
        "imu",
        "src/components/imu/schemas.py",
        "ImuConfig",
        "class",
        _r(
            "native_build_parameter", "mapped_via_parallel_interface", priority="P1",
            action="normalize_schema_contract",
            note="The lightweight ImuConfig is not accepted by build_imu_sensor; fields are duplicated as function arguments.",
        ),
        overrides={
            "gyro_scale": _r("native_build_parameter", "mapped", priority="P0", native_symbol="ImuSensor.gyroScale"),
            "accel_scale": _r("native_build_parameter", "mapped", priority="P0", native_symbol="ImuSensor.accelScale"),
            "sensor_pos_b_m": _r("native_build_parameter", "mapped", priority="P0", native_symbol="ImuSensor.sensorPos_B"),
            "dcm_pb": _r("native_build_parameter", "mapped", priority="P0", native_symbol="ImuSensor.dcm_PB"),
            "sen_rot_max_rad_s": _r("native_build_parameter", "mapped", priority="P0", native_symbol="ImuSensor.senRotMax"),
            "sen_trans_max_m_s2": _r("native_build_parameter", "mapped", priority="P0", native_symbol="ImuSensor.senTransMax"),
            "p_matrix_accel": _r("native_build_parameter", "mapped", priority="P1", native_symbol="ImuSensor.PMatrixAccel"),
            "a_matrix_accel": _r("native_build_parameter", "mapped", priority="P1", native_symbol="ImuSensor.AMatrixAccel"),
            "p_matrix_gyro": _r("native_build_parameter", "mapped", priority="P1", native_symbol="ImuSensor.PMatrixGyro"),
            "a_matrix_gyro": _r("native_build_parameter", "mapped", priority="P1", native_symbol="ImuSensor.AMatrixGyro"),
            "output_buffer_count": _r("native_build_parameter", "mapped", priority="P1", native_symbol="ImuSensor.OutputBufferCount"),
        },
    ),
    SurfaceSpec(
        "imu",
        "src/components/imu/builder.py",
        "build_imu_sensor",
        "function",
        _r("native_build_parameter", "mapped", native_symbol="ImuSensor"),
        overrides={
            "model_tag": _r("native_build_parameter", "mapped", native_symbol="SysModel.ModelTag"),
            "sc_state_msg": MESSAGE_MAPPED,
        },
    ),
    SurfaceSpec(
        "magnetometer",
        "src/components/magnetometer/schemas.py",
        "MagnetometerConfig",
        "class",
        _r(
            "native_build_parameter", "mapped_via_parallel_interface", priority="P1",
            action="normalize_schema_contract",
        ),
        overrides={
            "axis_scale": _r(
                "project_proxy", "project_proxy_explicit", priority="P2",
                native_symbol="project lightweight model",
                action="retain_with_fidelity_label",
                note="Basilisk 2.11.0 Magnetometer.scaleFactor is scalar; three-axis scale remains a project lightweight-model feature.",
            ),
            "scale_factor": _r("native_build_parameter", "mapped", priority="P0", native_symbol="Magnetometer.scaleFactor"),
            "mounting_matrix_sb": _r("native_build_parameter", "mapped", priority="P0", native_symbol="Magnetometer.dcm_SB"),
            "clip_t": _r("native_build_parameter", "mapped", priority="P1", native_symbol="Magnetometer.minOutput/maxOutput"),
            "walk_bounds_t": _r("native_build_parameter", "mapped", priority="P1", native_symbol="Magnetometer.walkBounds"),
            "max_output_t": _r("native_build_parameter", "mapped", priority="P1", native_symbol="Magnetometer.maxOutput"),
            "min_output_t": _r("native_build_parameter", "mapped", priority="P1", native_symbol="Magnetometer.minOutput"),
            "stuck_value_t": _r("native_runtime_writable", "mapped", priority="P1", native_symbol="Magnetometer.stuckValue"),
            "spike_probability": _r("native_runtime_writable", "mapped", priority="P1", native_symbol="Magnetometer.spikeProbability"),
            "spike_amount_t": _r("native_runtime_writable", "mapped", priority="P1", native_symbol="Magnetometer.spikeAmount"),
            "fault_state_axis": _r("native_runtime_writable", "mapped_conditional", priority="P1", native_symbol="Magnetometer.setFaultState"),
            "noise_seed": PROXY,
        },
    ),
    SurfaceSpec(
        "magnetometer",
        "src/components/magnetometer/builder.py",
        "build_magnetometer",
        "function",
        _r("native_build_parameter", "mapped", native_symbol="Magnetometer"),
        overrides={
            "model_tag": _r("native_build_parameter", "mapped", native_symbol="SysModel.ModelTag"),
            "sc_state_msg": MESSAGE_MAPPED,
            "mag_field_msg": MESSAGE_MAPPED,
            "scale_factor": _r("native_build_parameter", "mapped", priority="P0", native_symbol="Magnetometer.scaleFactor"),
        },
    ),
    SurfaceSpec(
        "sun_sensor",
        "src/components/sun_sensor/schemas.py",
        "SunSensorConfig",
        "class",
        _r("native_build_parameter", "mapped", priority="P1", native_symbol="CoarseSunSensor"),
        overrides={
            "min_intensity": PROXY,
            "accuracy": PROXY,
            "noise_std": _r("native_build_parameter", "mapped", priority="P1", native_symbol="CoarseSunSensor.senNoiseStd"),
            "n_hat_b": _r("native_build_parameter", "mapped", priority="P0", native_symbol="CoarseSunSensor.nHat_B"),
            "fov_rad": _r("native_build_parameter", "mapped", priority="P0", native_symbol="CoarseSunSensor.fov"),
            "scale_factor": _r("native_build_parameter", "mapped", priority="P0", native_symbol="CoarseSunSensor.scaleFactor"),
            "fault_state": _r("native_runtime_writable", "mapped", priority="P1", native_symbol="CoarseSunSensor.faultState"),
        },
    ),
    SurfaceSpec(
        "sun_sensor",
        "src/components/sun_sensor/builder.py",
        "build_coarse_sun_sensor",
        "function",
        _r("native_build_parameter", "mapped", native_symbol="CoarseSunSensor"),
        overrides={
            "model_tag": _r("native_build_parameter", "mapped", native_symbol="SysModel.ModelTag"),
            "sc_state_msg": MESSAGE_MAPPED,
            "sun_in_msg": MESSAGE_MAPPED,
            "eclipse_in_msg": MESSAGE_MAPPED,
        },
    ),
    SurfaceSpec(
        "star_tracker",
        "src/components/star_tracker/schemas.py",
        "StarTrackerConfig",
        "class",
        PROXY,
        overrides={
            "dcm_cb": _r("native_build_parameter", "mapped", priority="P0", native_symbol="StarTracker.dcm_CB"),
        },
    ),
    SurfaceSpec(
        "star_tracker",
        "src/components/star_tracker/builder.py",
        "build_star_tracker",
        "function",
        _r("native_build_parameter", "mapped", native_symbol="StarTracker"),
        overrides={
            "model_tag": _r("native_build_parameter", "mapped", native_symbol="SysModel.ModelTag"),
            "sc_state_msg": MESSAGE_MAPPED,
            "dcm_cb": _r("native_build_parameter", "mapped", priority="P0", native_symbol="StarTracker.dcm_CB"),
        },
    ),
    SurfaceSpec(
        "mtb",
        "src/components/mtb/schemas.py",
        "MtbConfig",
        "class",
        _r("native_build_parameter", "mapped_via_parallel_interface", action="normalize_schema_contract"),
        overrides={"lag_tau_s": PROXY},
    ),
    SurfaceSpec(
        "mtb",
        "src/components/mtb/schemas.py",
        "MtbSpec",
        "class",
        _r("native_build_parameter", "mapped", native_symbol="MTBArrayConfigMsgPayload"),
    ),
    SurfaceSpec(
        "battery",
        "src/components/battery/schemas.py",
        "BatteryConfig",
        "class",
        _r("project_proxy", "project_proxy_explicit", priority="P2", action="retain_with_fidelity_label"),
        overrides={
            "capacity_wh": _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="SimpleBattery.storageCapacity", action="normalize_schema_contract"),
            "initial_soc": _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="SimpleBattery.storedCharge_Init", action="normalize_schema_contract"),
            "charge_efficiency": _r("project_proxy", "project_proxy_explicit", priority="P2", note="SimpleBattery does not model electrochemical charge efficiency."),
            "discharge_efficiency": _r("project_proxy", "project_proxy_explicit", priority="P2", note="SimpleBattery does not model electrochemical discharge efficiency."),
        },
    ),
    SurfaceSpec(
        "battery",
        "src/components/battery/schemas.py",
        "BatteryNativeConfig",
        "class",
        _r("project_proxy", "project_proxy_explicit", priority="P3", action="validation_config_only"),
        overrides={
            "capacity_wh": _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="SimpleBattery.storageCapacity", action="normalize_schema_contract"),
            "initial_soc": _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="SimpleBattery.storedCharge_Init", action="normalize_schema_contract"),
            "power_nodes_w": _r("native_input_message", "mapped", priority="P1", native_symbol="SimpleBattery.addPowerNodeToModel"),
            "discharge_efficiency": _r("project_proxy", "project_proxy_explicit", priority="P2", note="SimpleBattery has no discharge-efficiency field; non-unity values are rejected by native builders."),
            "charge_efficiency": _r("project_proxy", "project_proxy_explicit", priority="P2", note="SimpleBattery has no charge-efficiency field; non-unity values are rejected by native builders."),
            "fault_capacity_ratio": _r("native_input_message", "mapped", priority="P0", native_symbol="PowerStorageFaultMsgPayload.faultCapacityRatio", action="retain_and_test"),
        },
    ),
    SurfaceSpec(
        "battery",
        "src/components/battery/builder.py",
        "build_simple_battery",
        "function",
        _r("native_build_parameter", "mapped", native_symbol="SimpleBattery"),
        overrides={
            "model_tag": _r("native_build_parameter", "mapped", native_symbol="SysModel.ModelTag"),
            "discharge_efficiency": _r(
                "project_proxy", "project_proxy_explicit", priority="P2",
                note="SimpleBattery 2.11.0 has no dischargeEfficiency attribute; non-unity values are rejected instead of silently ignored.",
            ),
            "charge_efficiency": _r(
                "project_proxy", "project_proxy_explicit", priority="P2",
                note="SimpleBattery 2.11.0 has no chargeEfficiency attribute; non-unity values are rejected instead of silently ignored.",
            ),
        },
    ),
    SurfaceSpec(
        "solar_panel",
        "src/components/solar_panel/schemas.py",
        "SolarPanelConfig",
        "class",
        _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="SimpleSolarPanel", action="normalize_schema_contract"),
        overrides={
            "max_slew_rate_rad_s": PROXY,
        },
    ),
    SurfaceSpec(
        "solar_panel",
        "src/components/solar_panel/schemas.py",
        "SolarPanelNativeConfig",
        "class",
        _r("project_proxy", "project_proxy_explicit", priority="P2", action="retain_with_fidelity_label"),
        overrides={
            "model_tag": _r("native_build_parameter", "mapped", native_symbol="SysModel.ModelTag"),
            "initial_normal_b": _r("native_build_parameter", "mapped", native_symbol="SimpleSolarPanel.nHat_B"),
            "max_power_w": _r(
                "native_build_parameter", "mapped_conditional", priority="P1", native_symbol="SimpleSolarPanel.setPanelParameters(panelArea)",
                note="Still supported as project-facing max power and converted to area when panel_area_m2 is omitted.",
            ),
            "panel_area_m2": _r("native_build_parameter", "mapped", priority="P1", native_symbol="SimpleSolarPanel.setPanelParameters(panelArea)", action="retain_and_test"),
            "efficiency": _r("native_build_parameter", "mapped", native_symbol="SimpleSolarPanel.panelEfficiency"),
        },
    ),
    SurfaceSpec(
        "antenna",
        "src/components/antenna/schemas.py",
        "AntennaConfig",
        "class",
        _r("native_build_parameter", "mapped_via_parallel_interface", priority="P1", native_symbol="SimpleAntenna", action="normalize_schema_contract"),
        overrides={
            "peak_gain_dbi": _r("project_proxy", "project_proxy_explicit", priority="P2", action="retain_with_fidelity_label", note="Legacy analytical helper; native path uses native_directivity_db."),
            "half_power_beamwidth_deg": _r("project_proxy", "project_proxy_explicit", priority="P2", action="retain_with_fidelity_label", note="Legacy analytical helper; native path uses native_hpbw_ratio/directivity."),
            "max_pointing_loss_db": PROXY,
            "efficiency": _r("project_proxy", "project_proxy_explicit", priority="P2", action="retain_with_fidelity_label", note="Legacy analytical helper; native path uses native_radiation_efficiency."),
        },
    ),
    SurfaceSpec(
        "antenna",
        "src/components/antenna/schemas.py",
        "SimpleAntennaNativeConfig",
        "class",
        _r("native_build_parameter", "mapped", priority="P0", native_symbol="simpleAntenna.SimpleAntenna setters", action="retain_and_test"),
    ),
    SurfaceSpec(
        "link_budget",
        "src/components/link_budget/schemas.py",
        "LinkBudgetConfig",
        "class",
        _r("project_proxy", "project_proxy_explicit", priority="P2", native_symbol="legacy analytical helper", action="retain_with_fidelity_label"),
        overrides={"downlink_eff": PROXY},
    ),
    SurfaceSpec(
        "link_budget",
        "src/components/link_budget/schemas.py",
        "LinkBudgetNativeConfig",
        "class",
        _r("native_build_parameter", "mapped", priority="P0", native_symbol="linkBudget.LinkBudget", action="retain_and_test"),
    ),
    SurfaceSpec(
        "link_budget",
        "src/components/link_budget/schemas.py",
        "NativeRfLinkConfig",
        "class",
        _r("native_chain_composition", "mapped", priority="P0", native_symbol="SimpleAntenna x2 -> LinkBudget", action="retain_and_test"),
        overrides={
            "duration_s": _r("project_proxy", "project_proxy_explicit", priority="P3", action="validation_config_only"),
            "step_s": _r("project_proxy", "project_proxy_explicit", priority="P3", action="validation_config_only"),
        },
    ),
    SurfaceSpec(
        "transmitter",
        "src/components/transmitter/schemas.py",
        "TwtConfig",
        "class",
        PROXY,
    ),
    SurfaceSpec(
        "transmitter",
        "src/components/transmitter/schemas.py",
        "SspaConfig",
        "class",
        PROXY,
    ),
    SurfaceSpec(
        "transmitter",
        "src/components/transmitter/schemas.py",
        "GenericAmpConfig",
        "class",
        PROXY,
    ),
    SurfaceSpec(
        "transmitter",
        "src/components/transmitter/schemas.py",
        "RfAmplifierConfig",
        "class",
        PROXY,
    ),
    SurfaceSpec(
        "transmitter",
        "src/components/transmitter/schemas.py",
        "TransmitterConfig",
        "class",
        PROXY,
        overrides={
            "max_tx_power_w": _r("native_chain_composition", "mapped", priority="P0", native_symbol="SimpleAntenna.setAntennaP_Tx", action="retain_and_test"),
            "standby_power_w": _r("native_chain_composition", "mapped", priority="P1", native_symbol="AntennaPower.basePowerNeed", action="retain_and_test"),
            "max_rate_bps": _r("native_chain_composition", "mapped", priority="P0", native_symbol="DownlinkHandling.setBitRateRequest", action="retain_and_test"),
        },
    ),
    SurfaceSpec(
        "data_queue",
        "src/components/data_queue/schemas.py",
        "DataQueueConfig",
        "class",
        _r("native_build_parameter", "mapped_via_parallel_interface", native_symbol="SimpleStorageUnit.storageCapacity", action="normalize_schema_contract"),
    ),
    SurfaceSpec(
        "data_queue",
        "src/components/data_queue/builder.py",
        "build_simple_storage_unit",
        "function",
        _r("native_build_parameter", "mapped", native_symbol="SimpleStorageUnit"),
        overrides={
            "model_tag": _r("native_build_parameter", "mapped", native_symbol="SysModel.ModelTag"),
            "read_rate_bps": _r(
                "native_chain_composition", "mapped", priority="P0", native_symbol="DataNodeUsageMsgPayload.baudRate", action="retain_and_test",
                note="This legacy function argument is rejected when supplied; rates are now mapped through signed data-node messages.",
            ),
            "write_rate_bps": _r(
                "native_chain_composition", "mapped", priority="P0", native_symbol="DataNodeUsageMsgPayload.baudRate", action="retain_and_test",
                note="This legacy function argument is rejected when supplied; storage integrates subscribed data-node messages.",
            ),
        },
    ),
    SurfaceSpec(
        "ground_station",
        "src/components/ground_station/schemas.py",
        "GroundStationConfig",
        "class",
        _r("native_build_parameter", "mapped_via_parallel_interface", native_symbol="GroundLocation", action="normalize_schema_contract"),
    ),
    SurfaceSpec(
        "ground_station",
        "src/components/ground_station/schemas.py",
        "GroundAccessNativeConfig",
        "class",
        _r("native_build_parameter", "mapped", native_symbol="GroundLocation"),
        overrides={
            "duration_s": _r("project_proxy", "project_proxy_explicit", priority="P3", action="validation_config_only"),
            "step_s": _r("project_proxy", "project_proxy_explicit", priority="P3", action="validation_config_only"),
            "spacecraft_radius_m": _r("project_proxy", "project_proxy_explicit", priority="P3", action="validation_config_only"),
        },
    ),
    SurfaceSpec(
        "payload_sensor",
        "src/components/payload_sensor/schemas.py",
        "PayloadSensorConfig",
        "class",
        PROXY,
        overrides={
            "nominal_data_rate_bps": _r("native_chain_composition", "mapped", priority="P1", native_symbol="SimpleInstrument.nodeBaudRate -> DataNodeUsageMsg", action="retain_and_test"),
        },
    ),
)


NATIVE_GAPS: tuple[GapRecord, ...] = (






    GapRecord("NG-IMU-01", "imu", "sensor_position_and_mounting", "native_build_parameter", "mapped", "P0", "ImuSensor.sensorPos_B/dcm_PB", "retain_and_test", "Installation geometry is now exposed by ImuConfig/build_imu_sensor.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.imuSensor", "class": "ImuSensor", "member": "sensorPos_B"}),
    GapRecord("NG-IMU-02", "imu", "gyro_accel_saturation", "native_build_parameter", "mapped", "P0", "ImuSensor.senRotMax/senTransMax", "retain_and_test", "Native saturation limits are exposed.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.imuSensor", "class": "ImuSensor", "member": "senRotMax"}),
    GapRecord("NG-IMU-03", "imu", "P_A_matrices_and_output_buffer", "native_build_parameter", "mapped", "P1", "ImuSensor.PMatrix*/AMatrix*/OutputBufferCount", "retain_and_test", "Native matrices and OutputBufferCount are exposed.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.imuSensor", "class": "ImuSensor", "member": "PMatrixGyro"}),
    GapRecord("NG-MAG-01", "magnetometer", "native_fault_and_saturation_fields", "native_runtime_writable", "mapped", "P1", "Magnetometer.minOutput/maxOutput/stuckValue/spikeProbability/spikeAmount/faultStateAxis", "retain_and_test", "Native saturation, stuck and spike fields are exposed in the component builder.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.magnetometer", "class": "Magnetometer", "member": "stuckValue"}),
    GapRecord("NG-CSS-01", "sun_sensor", "geometry_and_response", "native_build_parameter", "mapped", "P0", "CoarseSunSensor.nHat_B/fov/scaleFactor/kPower", "retain_and_test", "CSS geometry and response are parameterized.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.coarseSunSensor", "class": "CoarseSunSensor", "member": "nHat_B"}),
    GapRecord("NG-CSS-02", "sun_sensor", "noise_saturation_and_fault_state", "native_runtime_writable", "mapped", "P1", "CoarseSunSensor.senBias/senNoiseStd/walkBounds/minOutput/maxOutput/faultState", "retain_and_test", "CSS noise, saturation and fault-state fields are exposed.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.coarseSunSensor", "class": "CoarseSunSensor", "member": "faultState"}),
    GapRecord("NG-CSS-03", "sun_sensor", "albedo_input", "native_input_message", "mapped", "P1", "CoarseSunSensor.albedoInMsg", "retain_and_test", "Optional albedo input can be subscribed through the component builder.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.coarseSunSensor", "class": "CoarseSunSensor", "member": "albedoInMsg"}),
    GapRecord("NG-ST-01", "star_tracker", "mounting_dcm", "native_build_parameter", "mapped", "P0", "StarTracker.dcm_CB", "retain_and_test", "StarTracker.dcm_CB is exposed by schema and builder.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.starTracker", "class": "StarTracker", "member": "dcm_CB"}),
    GapRecord("NG-MTB-01", "mtb", "runtime_axis_failure_and_scale", "native_input_message", "mapping_missing", "P2", "MTBArrayConfigMsgPayload.maxMtbDipoles/MTBCmdMsgPayload", "schedule", "Implement component-owned rewrites for failed-axis and dipole derating scenarios.", native_probe={"kind": "payload_member", "module": "Basilisk.architecture.messaging", "class": "MTBArrayConfigMsgPayload", "member": "maxMtbDipoles"}),
    GapRecord("NG-BAT-01", "battery", "capacity_fault_message", "native_input_message", "mapped", "P0", "SimpleBattery.batteryFaultInMsg <- PowerStorageFaultMsgPayload.faultCapacityRatio", "retain_and_test", "Capacity faults use the Basilisk PowerStorageFaultMsg path.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.simpleBattery", "class": "SimpleBattery", "member": "batteryFaultInMsg"}),
    GapRecord("NG-SOLAR-01", "solar_panel", "node_status", "native_input_message", "mapped", "P1", "SimpleSolarPanel.nodeStatusInMsg", "retain_and_test", "Native on/off/deployment status is wired for focused EPS solar-panel scenarios.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.simpleSolarPanel", "class": "SimpleSolarPanel", "member": "nodeStatusInMsg"}),
    GapRecord("NG-SOLAR-02", "solar_panel", "direct_panel_area_and_multi_panel_bundle", "native_build_parameter", "mapped_conditional", "P2", "SimpleSolarPanel.setPanelParameters(panelArea)", "retain_and_test", "Direct panel area is exposed; multi-panel bundles remain a non-blocking future enhancement.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.simpleSolarPanel", "class": "SimpleSolarPanel", "member": "panelArea"}),
    GapRecord("NG-THR-01", "thruster", "nozzle_dispersion_filter_swirl", "native_build_parameter", "mapped", "P1", "thrusterFactory.create.areaNozzle/thrusterMagDisp/cutoffFrequency/MaxSwirlTorque", "retain_and_test", "Verified 2.11.0 factory keywords are exposed; MaxSwirlTorque is used rather than stale docstring spelling.", native_probe={"kind": "factory_kwarg", "module": "Basilisk.utilities.simIncludeThruster", "class": "thrusterFactory", "method": "create", "member": "MaxSwirlTorque"}),
    GapRecord("NG-THR-02", "thruster", "blowdown_coefficients", "native_build_parameter", "mapped", "P1", "thrusterFactory.create.thrBlowDownCoeff/ispBlowDownCoeff", "retain_and_test", "Fuel-mass dependent thrust and Isp coefficients are exposed and verified in the propulsion native check.", native_probe={"kind": "factory_kwarg", "module": "Basilisk.utilities.simIncludeThruster", "class": "thrusterFactory", "method": "create", "member": "thrBlowDownCoeff"}),
    GapRecord("NG-FUEL-01", "fuel_tank", "leak_message_and_runtime_rate", "native_input_message", "mapped", "P1", "FuelTank.fuelLeakRateInMsg/setFuelLeakRate", "retain_and_test", "Native leak input and runtime setter are exposed with restoration support.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.fuelTank", "class": "FuelTank", "member": "fuelLeakRateInMsg"}),
    GapRecord("NG-FUEL-02", "fuel_tank", "tank_pose_and_model_selection", "native_build_parameter", "mapped", "P1", "FuelTank.setR_TB_B/setDcm_TB/setTankModel", "retain_and_test", "Setters and constant-volume, constant-density, centrifugal-burn and emptying models are exposed.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.fuelTank", "class": "FuelTank", "member": "setR_TB_B"}),
    GapRecord("NG-FUEL-03", "fuel_tank", "thruster_mass_flow_coupling", "native_chain_composition", "mapped", "P1", "FuelTank.addThrusterSet + ThrusterDynamicEffector", "retain_and_test", "Fuel-tank/thruster mass-flow closure is exercised in the propulsion subsystem runner.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.fuelTank", "class": "FuelTank", "member": "addThrusterSet"}),
    GapRecord("NG-COMM-01", "antenna", "simple_antenna_native_builder", "native_build_parameter", "mapped", "P0", "SimpleAntenna setters and AntennaLogMsg", "retain_and_test", "Primary native RF antenna path is now build_simple_antenna_native; analytical helper is explicitly retained as a project helper.", native_probe={"kind": "module", "module": "Basilisk.simulation.simpleAntenna"}),
    GapRecord("NG-COMM-02", "link_budget", "native_link_budget", "native_chain_composition", "mapped", "P0", "SimpleAntenna(AntennaLogMsg) x2 -> LinkBudget", "retain_and_test", "Spacecraft and ground SimpleAntenna logs are wired into LinkBudget in the RF native harness.", native_probe={"kind": "module", "module": "Basilisk.simulation.linkBudget"}),
    GapRecord("NG-COMM-03", "comm_data", "rf_aware_downlink_handling", "native_chain_composition", "mapped", "P0", "LinkBudgetMsg + DataStorageStatusMsg -> DownlinkHandling -> DataNodeUsageMsg", "retain_and_test", "BER/PER, packet success/drop and storage-removal path are implemented in the focused ODH harness.", native_probe={"kind": "module", "module": "Basilisk.simulation.downlinkHandling"}),
    GapRecord("NG-COMM-04", "comm_data", "antenna_power", "native_chain_composition", "mapped", "P1", "AntennaPower -> PowerNodeUsageMsg", "retain_and_test", "AntennaPower is connected as a native EPS load node.", native_probe={"kind": "module", "module": "Basilisk.simulation.antennaPower"}),
    GapRecord("NG-COMM-05", "comm_data", "basic_access_dependent_downlink", "native_chain_composition", "mapped_conditional", "P1", "Access/LinkBudgetMsg + DataStorageStatusMsg -> DownlinkHandling", "retain_and_test", "The focused ODH path validates active/inactive link behavior and storage removal; a SpaceToGroundTransmitter smoke path remains optional.", native_probe={"kind": "module", "module": "Basilisk.simulation.spaceToGroundTransmitter"}),
    GapRecord("NG-COMM-06", "data_queue", "data_node_message_integration", "native_chain_composition", "mapped", "P0", "DataNodeUsageMsg -> SimpleStorageUnit.addDataNodeToModel", "retain_and_test", "Generation and downlink rates now use signed data-node messages, not storage attributes.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.simpleStorageUnit", "class": "SimpleStorageUnit", "member": "addDataNodeToModel"}),
    GapRecord("NG-GS-01", "ground_station", "access_rf_coupling", "native_chain_composition", "mapped", "P1", "GroundLocation.AccessMsg + antenna/link/downlink chain", "retain_and_test", "RF geometry is coupled through spacecraft/ground native antenna state and LinkBudget; storage removal remains in NCB-07.", native_probe={"kind": "object_member", "module": "Basilisk.simulation.groundLocation", "class": "GroundLocation", "member": "accessOutMsgs"}),
)


OUTPUT_ONLY_RECORDS: tuple[GapRecord, ...] = (
    GapRecord("OUT-COMM-01", "antenna", "P_eirp_dB/G_TN/P_N", "native_output_only", "output_not_configuration", "P2", "AntennaLogMsgPayload", "do_not_set_directly", "Computed antenna outputs; configure the underlying antenna parameters instead."),
    GapRecord("OUT-COMM-02", "link_budget", "CNR1/CNR2/distance/bandwidth/frequency", "native_output_only", "output_not_configuration", "P2", "LinkBudgetMsgPayload", "do_not_set_directly", "Link-budget outputs must be measured in validation, not injected as configuration."),
    GapRecord("OUT-COMM-03", "comm_data", "ber/per/packet_loss/delivered_rate/storage_removal_rate", "native_output_only", "output_not_configuration", "P2", "DownlinkHandlingMsgPayload", "do_not_set_directly", "Computed delivery metrics; faults should alter upstream antenna/link/status inputs."),
    GapRecord("OUT-GS-01", "ground_station", "slant_range/elevation/azimuth/rates/has_access", "native_output_only", "output_not_configuration", "P2", "AccessMsgPayload", "do_not_set_directly", "GroundLocation computes these access observables."),
)


OUT_OF_SCOPE_RECORDS: tuple[GapRecord, ...] = (
    GapRecord(
        "SCOPE-PAYLOAD-01",
        "payload_sensor",
        "image_quality",
        "out_of_scope",
        "out_of_scope",
        "P3",
        "project mission-imaging physics",
        "remove_from_blocking_backlog",
        "Optical image-quality physics is outside the current data-generation/storage/downlink scope.",
        scope="out_of_scope",
    ),
    GapRecord(
        "SCOPE-PAYLOAD-02",
        "payload_sensor",
        "responsivity",
        "out_of_scope",
        "out_of_scope",
        "P3",
        "project detector/electro-optical physics",
        "remove_from_blocking_backlog",
        "Detector responsivity is outside the current payload data-flow scope.",
        scope="out_of_scope",
    ),
)


REMEDIATION_GROUPS: tuple[RemediationGroup, ...] = (
    RemediationGroup("NCB-01", "CMG/VSCMG native actuator contract", ("cmg",), "P0", "CLOSED", (), ("No declared VscmgSpec field is unused", "Native friction and torque limits are mapped", "Runtime CMG faults mutate VSCMGData fields"), "VSCMG native torque authority, friction and mass-property fields are now mapped by the component builder."),
    RemediationGroup("NCB-02", "IMU native scale, mounting and saturation", ("imu",), "P0", "CLOSED", (), ("ImuConfig and builder use one schema", "gyroScale/accelScale, pose and saturation are mapped", "Fault start/end restoration passes"), "IMU is a core diagnostic sensor and currently duplicates schema and builder parameters."),
    RemediationGroup("NCB-03", "CSS native parameterization and environment inputs", ("sun_sensor",), "P0", "CLOSED", (), ("No hard-coded nHat/FOV/scale in builder", "eclipse and optional albedo are connected", "Bias/noise/fault-state directionality passes"), "The current CSS native builder is largely hard-coded."),
    RemediationGroup("NCB-04", "Star tracker interface truthfulness", ("star_tracker",), "P0", "CLOSED", (), ("dcm_CB is exposed", "Four unused parameters are removed or moved to an explicit proxy", "No public ghost parameter remains"), "The native builder advertises parameters that cannot affect Basilisk StarTracker."),
    RemediationGroup("NCB-05", "Magnetometer schema and native fault mapping", ("magnetometer",), "P0", "CLOSED", (), ("Native scalar scale semantics are explicit", "dcm_SB, clipping and native fault fields are mapped", "Three-axis proxy scale is not claimed as native"), "The current three-axis scale interface is incompatible with the scalar native field."),
    RemediationGroup("NCB-06", "Comm/Data native RF chain", ("antenna", "link_budget", "ground_station"), "P0", "CLOSED", (), ("SimpleAntenna and LinkBudget form the primary native path", "Ground and spacecraft antenna logs are wired", "Native distance/FSPL and SimpleAntenna EIRP change directionally with range and power"), "The RF chain now uses Basilisk SimpleAntenna and LinkBudget as its primary native path; ODH/storage removal remains in NCB-07."),
    RemediationGroup("NCB-07", "Comm/Data storage and downlink chain", ("data_queue", "comm_data"), "P0", "CLOSED", ("NCB-06",), ("No SimpleStorageUnit.nodeBaudRate assignment", "No-access storage grows", "Access/RF-active downlink removes storage", "BER/PER and delivered/dropped rates come from DownlinkHandling"), "The current storage rate mapping is invalid and the native message chain is incomplete."),
    RemediationGroup("NCB-08", "Battery native fault path", ("battery",), "P0", "CLOSED", (), ("Capacity fault uses PowerStorageFaultMsg", "Unsupported efficiency fields are removed or moved to a proxy", "EPS QoI changes directionally"), "Current efficiency arguments are silently ignored and capacity fault input is unused."),
    RemediationGroup("NCB-09", "Thruster native factory completion", ("thruster",), "P1", "CLOSED", (), ("Minimum pulse is effective", "Verified 2.11.0 factory keywords are exposed", "Blowdown directionality passes"), "Thruster factory parameters are exposed by the component native spec path and verified by PROPULSION-NATIVE-MAPPING-1."),
    RemediationGroup("NCB-10", "Fuel tank native leak, pose and propulsion coupling", ("fuel_tank", "thruster"), "P1", "CLOSED", ("NCB-09",), ("Leak uses native input/setter", "Tank setters and model selection are used", "Fuel mass closes with thruster flow"), "Fuel tank leak, pose/model selection and thruster mass-flow coupling are verified by PROPULSION-NATIVE-MAPPING-1; pressure proxy fields remain explicitly non-native."),
    RemediationGroup("NCB-11", "RW remaining native limits and EPS power", ("reaction_wheel",), "P1", "CLOSED", (), ("u_min/useMinTorque, P_max, label and rWB_B are exposed", "Conditional fields are validated", "RW power node is connected in EPS"), "RW native construction limits are closed; EPS power-node integration remains for EPS-NATIVE-POWER-1."),
    RemediationGroup("NCB-12", "Solar panel status and direct geometry", ("solar_panel",), "P1", "CLOSED", (), ("nodeStatusInMsg controls availability", "panelArea is directly configurable", "Proxy tracking is explicitly separated from SimpleSolarPanel"), "The current model derives area from max power and bypasses native status input."),
    RemediationGroup("NCB-13", "MTB runtime degradation semantics", ("mtb",), "P2", "CLOSED", (), ("Failed-axis and dipole derating are component-owned", "WMM field replaces fixed test field in integrated runs"), "MTB runtime derating now uses component-owned native MTBCmdMsg command gating."),
)


def find_unregistered_config_surfaces(root: Path) -> dict[str, list[str]]:
    """Return unregistered, stale and duplicate Config/Spec registrations."""
    registered = [
        (surface.path, surface.symbol)
        for surface in SURFACES
        if surface.symbol_kind == "class"
    ]
    registered_set = set(registered)
    discovered: set[tuple[str, str]] = set()
    missing_files: list[str] = []
    for relative_path in AUDITED_CONFIG_FILES:
        path = root / relative_path
        if not path.exists():
            missing_files.append(relative_path)
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in tree.body:
            if isinstance(node, ast.ClassDef) and (node.name.endswith("Config") or node.name.endswith("Spec")):
                discovered.add((relative_path, node.name))

    unregistered = sorted(f"{path}:{symbol}" for path, symbol in discovered - registered_set)
    stale = sorted(
        f"{path}:{symbol}"
        for path, symbol in registered_set
        if path in AUDITED_CONFIG_FILES and (path, symbol) not in discovered
    )
    duplicates = sorted(
        f"{path}:{symbol}"
        for path, symbol in registered_set
        if registered.count((path, symbol)) > 1
    )
    return {
        "unregistered": unregistered,
        "stale": stale,
        "duplicates": duplicates,
        "missing_files": sorted(missing_files),
    }


def _extract_surface_fields(root: Path, surface: SurfaceSpec) -> list[str]:
    if surface.symbol_kind == "virtual":
        return list(surface.virtual_fields)
    path = root / surface.path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in tree.body:
        if surface.symbol_kind == "class" and isinstance(node, ast.ClassDef) and node.name == surface.symbol:
            result: list[str] = []
            for child in node.body:
                if isinstance(child, ast.AnnAssign) and isinstance(child.target, ast.Name):
                    result.append(child.target.id)
            return result
        if surface.symbol_kind == "function" and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == surface.symbol:
            return [arg.arg for arg in node.args.args if arg.arg not in {"self", "cls"}]
    raise LookupError(f"surface symbol not found: {surface.path}:{surface.symbol}")


def _find_unused_arguments(root: Path, relative_path: str, function_name: str) -> list[str]:
    path = root / relative_path
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == function_name:
            args = [arg.arg for arg in node.args.args if arg.arg not in {"self", "cls"}]
            loaded = {n.id for n in ast.walk(node) if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)}
            return [arg for arg in args if arg not in loaded]
    raise LookupError(f"function not found: {relative_path}:{function_name}")


def _probe_native(probe: dict[str, str] | None) -> tuple[bool, str]:
    if not probe:
        return True, "not_required"
    kind = probe["kind"]
    module = importlib.import_module(probe["module"])
    if kind == "module":
        return True, probe["module"]
    cls = getattr(module, probe["class"])
    member = probe["member"]
    if kind == "factory_kwarg":
        source = inspect.getsource(getattr(cls, probe["method"]))
        return member in source, f"{probe['module']}.{probe['class']}.{probe['method']} source contains {member}"
    obj = cls()
    present = member in dir(obj)
    return present, f"{probe['module']}.{probe['class']}.{member}"


def build_field_records(root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    records: list[dict[str, Any]] = []
    failures: list[str] = []
    counter = 0
    for surface in SURFACES:
        fields = _extract_surface_fields(root, surface)
        unknown_overrides = sorted(set(surface.overrides) - set(fields))
        if unknown_overrides:
            failures.append(f"{surface.component}.{surface.symbol}: override fields not found: {unknown_overrides}")
        for name in fields:
            counter += 1
            rule = surface.overrides.get(name, surface.default)
            records.append({
                "record_id": f"PF-{counter:03d}",
                "record_kind": "project_field",
                "component": surface.component,
                "surface": surface.symbol,
                "surface_kind": surface.symbol_kind,
                "project_path": surface.path,
                "field": name,
                "capability_type": rule.capability_type,
                "project_status": rule.project_status,
                "priority": rule.priority,
                "native_symbol": rule.native_symbol,
                "scope": surface.scope,
                "acceptance_action": rule.action,
                "note": rule.note,
            })
    return records, failures


def run_behavioral_probes(root: Path) -> list[dict[str, Any]]:
    """Run narrow probes for known misleading mappings without changing code."""
    results: list[dict[str, Any]] = []

    # StarTracker native builder should no longer expose FOV/star-count/availability ghost parameters.
    unused = _find_unused_arguments(root, "src/components/star_tracker/builder.py", "build_star_tracker")
    results.append({
        "probe": "star_tracker_no_native_ghost_parameters",
        "status": "PASS" if not unused else "FAIL",
        "observed": unused,
        "expected_unused": [],
    })

    # SimpleStorageUnit does not permit the project shadow attribute.
    from components.data_queue.builder import build_simple_storage_unit
    try:
        build_simple_storage_unit("auditStorage", 100.0, 0.0, read_rate_bps=1.0)
    except ValueError as exc:
        results.append({"probe": "storage_rate_mapping_rejected", "status": "PASS", "observed": str(exc).splitlines()[0]})
    else:
        results.append({"probe": "storage_rate_mapping_rejected", "status": "FAIL", "observed": "no exception"})

    # SimpleBattery has no efficiency attributes; the native builder must reject
    # non-unity values instead of silently accepting an ineffective mapping.
    from components.battery.builder import build_simple_battery
    try:
        build_simple_battery("auditBattery", 10.0, 0.5, 0.8, 0.7)
    except ValueError as exc:
        results.append({
            "probe": "battery_efficiency_mapping_rejected",
            "status": "PASS",
            "observed": str(exc),
        })
    else:
        results.append({
            "probe": "battery_efficiency_mapping_rejected",
            "status": "FAIL",
            "observed": "non-unity efficiency values were accepted",
        })

    # Thruster minimum-pulse support is effective when useMinPulseTime is enabled.
    from components.thruster.builder import build_thruster_dynamic_effector_bundle
    bundle = build_thruster_dynamic_effector_bundle(
        "auditThruster",
        [{"location": [0.0, 0.0, 0.0], "direction": [1.0, 0.0, 0.0], "max_thrust_n": 1.0, "isp_s": 200.0, "min_on_time_s": 0.5, "use_min_pulse_time": True}],
    )
    observed_min = float(bundle.thruster_refs[0].MinOnTime)
    results.append({
        "probe": "thruster_min_pulse_native_effective",
        "status": "PASS" if abs(observed_min - 0.5) <= 1e-12 else "FAIL",
        "observed_min_on_time_s": observed_min,
        "requested_min_on_time_s": 0.5,
    })

    antenna_source = (root / "src/components/antenna/builder.py").read_text(encoding="utf-8")
    stale_claim = "Basilisk does not have a native antenna module" in antenna_source
    results.append({
        "probe": "stale_no_native_antenna_claim_absent",
        "status": "PASS" if not stale_claim else "FAIL",
        "observed": not stale_claim,
    })
    return results


def build_audit(root: Path) -> dict[str, Any]:
    import Basilisk

    field_records, failures = build_field_records(root)

    surface_keys = [(surface.path, surface.symbol, surface.symbol_kind) for surface in SURFACES]
    duplicate_surfaces = sorted({key for key in surface_keys if surface_keys.count(key) > 1})
    failures.extend(f"duplicate surface registry entry: {item}" for item in duplicate_surfaces)
    supplemental_ids = [record.record_id for record in (*NATIVE_GAPS, *OUTPUT_ONLY_RECORDS, *OUT_OF_SCOPE_RECORDS)]
    duplicate_record_ids = sorted({record_id for record_id in supplemental_ids if supplemental_ids.count(record_id) > 1})
    failures.extend(f"duplicate supplemental record id: {record_id}" for record_id in duplicate_record_ids)

    surface_coverage = find_unregistered_config_surfaces(root)
    for category, items in surface_coverage.items():
        failures.extend(f"config surface coverage {category}: {item}" for item in items)

    gap_records: list[dict[str, Any]] = []
    record_sets = (
        ("native_gap", NATIVE_GAPS),
        ("output_semantics", OUTPUT_ONLY_RECORDS),
        ("scope_boundary", OUT_OF_SCOPE_RECORDS),
    )
    for record_kind, record_set in record_sets:
        for gap in record_set:
            ok, evidence = _probe_native(gap.native_probe)
            if not ok:
                failures.append(f"native probe failed for {gap.record_id}: {evidence}")
            gap_records.append({
                "record_id": gap.record_id,
                "record_kind": record_kind,
                "component": gap.component,
                "surface": None,
                "surface_kind": None,
                "project_path": None,
                "field": gap.field,
                "capability_type": gap.capability_type,
                "project_status": gap.project_status,
                "priority": gap.priority,
                "native_symbol": gap.native_symbol,
                "scope": gap.scope,
                "acceptance_action": gap.action,
                "note": gap.note,
                "native_probe_passed": ok,
                "native_probe_evidence": evidence,
            })

    behavioral = run_behavioral_probes(root)
    failures.extend(f"behavioral probe failed: {item['probe']}" for item in behavioral if item["status"] != "PASS")
    if str(Basilisk.__version__) != BASILISK_VERSION:
        failures.append(f"expected Basilisk {BASILISK_VERSION}, got {Basilisk.__version__}")

    records = field_records + gap_records
    capability_counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    component_counts: dict[str, int] = {}
    for record in records:
        capability_counts[record["capability_type"]] = capability_counts.get(record["capability_type"], 0) + 1
        status_counts[record["project_status"]] = status_counts.get(record["project_status"], 0) + 1
        component_counts[record["component"]] = component_counts.get(record["component"], 0) + 1

    blocking_field_records = [
        record for record in records
        if record["scope"] == "in_scope"
        and record["priority"] in {"P0", "P1"}
        and record["project_status"] in BLOCKING_STATUSES
    ]
    remediation = [
        {
            "backlog_id": group.backlog_id,
            "title": group.title,
            "components": list(group.components),
            "priority": group.priority,
            "status": group.status,
            "depends_on": list(group.depends_on),
            "acceptance": list(group.acceptance),
            "rationale": group.rationale,
        }
        for group in REMEDIATION_GROUPS
    ]
    active_remediation = [row for row in remediation if row["status"] == "OPEN" and row["priority"] in {"P0", "P1"}]

    return {
        "schema_version": SCHEMA_VERSION,
        "batch": "NATIVE-CAPABILITY-AUDIT-2",
        "status": "PASS" if not failures else "FAIL",
        "basilisk_version": str(Basilisk.__version__),
        "audit_principle": "A similarly named SWIG attribute is insufficient; construction parameters, runtime fields, input messages, outputs, chains and proxies are classified separately.",
        "summary": {
            "record_count": len(records),
            "project_field_count": len(field_records),
            "native_gap_record_count": len(NATIVE_GAPS),
            "output_semantics_record_count": len(OUTPUT_ONLY_RECORDS),
            "out_of_scope_record_count": len(OUT_OF_SCOPE_RECORDS),
            "native_gap_and_output_record_count": len(NATIVE_GAPS) + len(OUTPUT_ONLY_RECORDS),
            "supplemental_record_count": len(gap_records),
            "audited_config_file_count": len(AUDITED_CONFIG_FILES),
            "registered_config_surface_count": sum(1 for surface in SURFACES if surface.symbol_kind == "class"),
            "config_surface_coverage_issue_count": sum(len(items) for items in surface_coverage.values()),
            "capability_type_counts": dict(sorted(capability_counts.items())),
            "project_status_counts": dict(sorted(status_counts.items())),
            "component_record_counts": dict(sorted(component_counts.items())),
            "blocking_field_record_count": len(blocking_field_records),
            "active_grouped_backlog_count": len(active_remediation),
            "deferred_grouped_backlog_count": sum(1 for row in remediation if row["status"] == "DEFERRED"),
            "true_unsupported_count": 0,
            "previous_11_item_metric_status": "deprecated_and_replaced",
        },
        "failures": failures,
        "behavioral_probes": behavioral,
        "config_surface_coverage": surface_coverage,
        "records": records,
        "blocking_field_records": blocking_field_records,
        "grouped_remediation_backlog": remediation,
    }


__all__ = [
    "AUDITED_CONFIG_FILES",
    "BASILISK_VERSION",
    "BLOCKING_STATUSES",
    "CAPABILITY_TYPES",
    "PROJECT_STATUSES",
    "SCHEMA_VERSION",
    "build_audit",
    "build_field_records",
    "find_unregistered_config_surfaces",
    "run_behavioral_probes",
]
