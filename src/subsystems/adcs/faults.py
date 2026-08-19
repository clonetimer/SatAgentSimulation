"""ADCS subsystem fault aggregation.

Subsystem scenarios are composed from component-local faults in reaction wheel,
MTB, CMG, IMU, star tracker, sun sensor, and magnetometer modules.  The ADCS
subsystem does not redefine component mechanisms; it binds them to subsystem
Basilisk module/message targets.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
from typing import Any, Optional

from components.fault_spec import FaultSpec
from components.reaction_wheel import faults as _rw_faults
from components.mtb import faults as _mtb_faults
from components.cmg import faults as _cmg_faults
from components.imu import faults as _imu_faults
from components.star_tracker import faults as _star_tracker_faults
from components.sun_sensor import faults as _sun_sensor_faults
from components.magnetometer import faults as _magnetometer_faults
from components.reaction_wheel.faults import (
    RWFaultType,
    apply_runtime_reaction_wheel_fault,
    build_reaction_wheel_fault_spec,
    reaction_wheel_fault_friction_factor,
)
from components.cmg.faults import apply_runtime_cmg_fault
from components.mtb.faults import apply_runtime_mtb_fault

from ..fault_base import (
    ComponentFaultBinding,
    SubsystemFaultScenario,
    bind_fault,
    build_fault_event_specs,
    pick_default,
    register_fault_events,
)
from .schemas import AdcsConfig


COMPONENT_COVERAGE: tuple[str, ...] = (
    "reaction_wheel",
    "mtb",
    "cmg",
    "imu",
    "star_tracker",
    "sun_sensor",
    "magnetometer",
)


class ADCSFaultType(Enum):
    RW_JAMMING = "rw_jamming"
    MTB_DIPOLE_STUCK = "mtb_dipole_stuck"
    SENSOR_FAILURE = "sensor_failure"
    CONTROL_LOOP_FAILURE = "control_loop_failure"
    ACTUATOR_FAILURE = "actuator_failure"


@dataclass
class AdcsFaultSpec(FaultSpec):
    pass


_COMPONENT_FAULT_MODULES = {
    "reaction_wheel": _rw_faults,
    "mtb": _mtb_faults,
    "cmg": _cmg_faults,
    "imu": _imu_faults,
    "star_tracker": _star_tracker_faults,
    "sun_sensor": _sun_sensor_faults,
    "magnetometer": _magnetometer_faults,
}


def _pick(component: str, index: int = 0) -> Any:
    return pick_default(_COMPONENT_FAULT_MODULES[component], "default_faults", component, index)


def default_fault_scenarios() -> dict[str, SubsystemFaultScenario]:
    """Return representative ADCS scenarios traceable to component faults."""

    rw_bearing = bind_fault(
        "reaction_wheel",
        _pick("reaction_wheel", 0),
        target_id="reaction_wheel.primary",
        mapping={"module": "reactionWheelStateEffector.ReactionWheelData", "parameter": "fCoulomb/fStatic/betaStatic/cViscous/u_max", "injection": "native_rw_config_parameter"},
    )
    mtb_coil = bind_fault(
        "mtb",
        _pick("mtb", 0),
        target_id="mtb.primary",
        mapping={"module": "mtb_effector", "parameter": "dipole/current_factor", "injection": "command_gate"},
    )
    cmg_gimbal = bind_fault(
        "cmg",
        _pick("cmg", 0),
        target_id="cmg.primary",
        mapping={"module": "vscmg_effector", "parameter": "gimbal_rate_limit/torque_authority", "injection": "module_parameter"},
    )
    imu_bias = bind_fault(
        "imu",
        _pick("imu", 0),
        target_id="imu.gyro",
        mapping={"module": "imu", "parameter": "bias_rad_s", "injection": "message_adapter"},
    )
    star_dropout = bind_fault(
        "star_tracker",
        _pick("star_tracker", 1),
        target_id="star_tracker.primary",
        mapping={"module": "star_tracker", "parameter": "availability/dropout_probability", "injection": "message_adapter"},
    )
    sun_fault = bind_fault(
        "sun_sensor",
        _pick("sun_sensor", 0),
        target_id="sun_sensor.coarse",
        mapping={"module": "sun_sensor", "parameter": "eclipse_flag/sensitivity", "injection": "message_adapter"},
    )
    mag_bias = bind_fault(
        "magnetometer",
        _pick("magnetometer", 0),
        target_id="magnetometer.primary",
        mapping={"module": "magnetometer", "parameter": "axis_bias/scale", "injection": "message_adapter"},
    )

    return {
        "rw_bearing_seizure": SubsystemFaultScenario(
            name="rw_bearing_seizure",
            component_faults=(rw_bearing,),
            description="Reaction wheel bearing seizure reduces torque authority and raises friction.",
        ),
        "mtb_cmg_actuator_fault": SubsystemFaultScenario(
            name="mtb_cmg_actuator_fault",
            component_faults=(mtb_coil, cmg_gimbal),
            description="Magnetorquer coil and CMG gimbal faults reduce ADCS actuator authority.",
        ),
        "sensor_dropout_bias_fault": SubsystemFaultScenario(
            name="sensor_dropout_bias_fault",
            component_faults=(imu_bias, star_dropout, sun_fault, mag_bias),
            description="ADCS sensor-path faults bias or drop attitude, sun, and magnetic measurements.",
        ),
        "combined_adcs_fault": SubsystemFaultScenario(
            name="combined_adcs_fault",
            component_faults=(rw_bearing, mtb_coil, cmg_gimbal, imu_bias, star_dropout, sun_fault, mag_bias),
            description="Coverage scenario exercising every ADCS component fault binding.",
        ),
    }


def apply_adcs_config_faults(cfg: AdcsConfig, fault_specs: list[FaultSpec]) -> AdcsConfig:
    """Compose build-time ADCS effects by delegating RW physics to the component."""

    component_specs: list[FaultSpec] = []
    for fault in fault_specs:
        if isinstance(fault.fault_type, RWFaultType):
            component_specs.append(fault)
            continue
        if isinstance(fault.fault_type, ADCSFaultType):
            # Compatibility translation only. The resulting mechanics are still
            # defined and evaluated by components.reaction_wheel.faults.
            mapping = {
                ADCSFaultType.RW_JAMMING: RWFaultType.Jamming,
                ADCSFaultType.ACTUATOR_FAILURE: RWFaultType.MotorFailure,
            }
            rw_type = mapping.get(fault.fault_type)
            if rw_type is not None:
                component_specs.append(build_reaction_wheel_fault_spec(
                    rw_type,
                    onset_time_s=fault.onset_time_s,
                    duration_s=fault.duration_s,
                    magnitude=fault.magnitude,
                    target_id=fault.target_id or "rw_0",
                ))
    factor = reaction_wheel_fault_friction_factor(component_specs)
    return replace(cfg, rw_friction_factor=float(cfg.rw_friction_factor) + factor)


def build_adcs_fault_spec(
    fault_type: ADCSFaultType,
    time_s: float = 0.0,
    magnitude: float = 1.0,
    duration_s: Optional[float] = None,
    target_id: str | None = None,
) -> AdcsFaultSpec:
    return AdcsFaultSpec(
        fault_type=fault_type,
        onset_time_s=float(time_s),
        duration_s=-1.0 if duration_s is None else float(duration_s),
        magnitude=float(magnitude),
        target_id=target_id,
    )


def build_adcs_direct_fault_specs(name: str) -> tuple[FaultSpec, ...]:
    """Build runtime specs by calling the component-owned RW fault factory."""

    mapping = {
        "rw_jamming": RWFaultType.Jamming,
        "rw_bearing_seizure": RWFaultType.BearingSeizure,
    }
    fault_type = mapping.get(name)
    if fault_type is None:
        return ()
    return (build_reaction_wheel_fault_spec(fault_type, target_id="rw_0"),)


def default_adcs_fault_specs() -> list[FaultSpec]:
    return [build_reaction_wheel_fault_spec(RWFaultType.Jamming, magnitude=0.5, target_id="reaction_wheel.primary")]


def apply_runtime_adcs_fault(
    spec: FaultSpec,
    component_registry: dict[str, Any],
    *,
    set_attr=None,
    resolve_component=None,
    record_mutation=None,
    register_post_restore=None,
) -> tuple[dict[str, object], ...]:
    """Locate the ADCS target and delegate mutation to its component module."""

    target_id = str(getattr(spec, "target_id", "") or "")
    target_lower = target_id.lower()
    fault_value = str(getattr(spec.fault_type, "value", spec.fault_type))
    is_rw = (
        fault_value in {"rw_jamming", "rw_motor_failure", "rw_bearing_seizure"}
        or target_lower.startswith("rw")
        or "reaction_wheel" in target_lower
    )
    if is_rw:
        rw_target = (
            component_registry.get(target_id)
            or component_registry.get("rw_cluster")
            or component_registry.get("rw_0")
        )
        if rw_target is None and resolve_component is not None:
            rw_target = resolve_component(target_id, "reaction_wheel")
        if rw_target is None:
            return ()
        return apply_runtime_reaction_wheel_fault(rw_target, spec, set_attr=set_attr)

    is_cmg = (
        fault_value in {"gimbal_motor_failure", "spin_motor_failure", "bearing_wear", "gimbal_lock", "torque_decay", "thermal_overload", "communication_loss", "singularity_lock", "gimbal_stuck", "wheel_drive_fault", "rate_limit_fault"}
        or target_lower.startswith("cmg")
        or "vscmg" in target_lower
    )
    if is_cmg:
        cmg_target = (
            component_registry.get(target_id)
            or component_registry.get("cmg")
            or component_registry.get("vscmg")
            or component_registry.get("cmg_cluster")
        )
        if cmg_target is None and resolve_component is not None:
            cmg_target = resolve_component(target_id, "cmg")
        if cmg_target is None:
            return ()
        return apply_runtime_cmg_fault(cmg_target, spec, set_attr=set_attr)

    is_mtb = (
        fault_value in {"coil_open", "coil_short", "dipole_saturation", "coil_open_circuit", "coil_short_circuit", "demagnetization", "power_loss", "saturation", "temperature_drift", "coil_burnout"}
        or target_lower.startswith("mtb")
        or "magnetorquer" in target_lower
        or "magnetic_torquer" in target_lower
    )
    if is_mtb:
        mtb_target = (
            component_registry.get(target_id)
            or component_registry.get("mtb")
            or component_registry.get("magnetorquer")
        )
        if mtb_target is None and resolve_component is not None:
            mtb_target = resolve_component(target_id, "mtb")
        if mtb_target is None:
            return ()
        return apply_runtime_mtb_fault(mtb_target, spec, set_attr=set_attr, register_post_restore=register_post_restore)

    from components.sensor.faults import apply_runtime_sensor_fault

    sensor = component_registry.get(target_id)
    if sensor is None and resolve_component is not None:
        sensor = resolve_component(target_id, "sensor")
    if sensor is None:
        for key in ("imu", "star_tracker", "sun_sensor", "magnetometer"):
            if key in target_lower and component_registry.get(key) is not None:
                sensor = component_registry[key]
                break
    if sensor is None or set_attr is None:
        return ()
    changed = apply_runtime_sensor_fault(sensor, spec, set_attr=set_attr)
    return ({"component": "sensor", "target_id": target_id, "changed": changed},) if changed else ()


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentFaultBinding",
    "SubsystemFaultScenario",
    "ADCSFaultType",
    "AdcsFaultSpec",
    "apply_adcs_config_faults",
    "build_adcs_fault_spec",
    "build_adcs_direct_fault_specs",
    "apply_runtime_adcs_fault",
    "default_adcs_fault_specs",
    "default_fault_scenarios",
    "build_fault_event_specs",
    "register_fault_events",
]
