"""ADCS subsystem degradation aggregation."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional

from components.reaction_wheel import degradation as _rw_degradation
from components.mtb import degradation as _mtb_degradation
from components.cmg import degradation as _cmg_degradation
from components.imu import degradation as _imu_degradation
from components.star_tracker import degradation as _star_tracker_degradation
from components.sun_sensor import degradation as _sun_sensor_degradation
from components.magnetometer import degradation as _magnetometer_degradation
from components.reaction_wheel.degradation import ReactionWheelDegradation
from components.sensor.degradation import SensorDegradation

from ..degradation_base import (
    ComponentDegradationBinding,
    SubsystemDegradationScenario,
    bind_degradation,
    build_degradation_update_specs,
    pick_default,
    register_degradation_events,
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


@dataclass(frozen=True)
class ADCSDegradation:
    rw_friction_factor: float = 0.0
    sensor_noise_factor: float = 0.0
    mtb_dipole_degradation_factor: float = 1.0
    cmg_efficiency_factor: float = 1.0


_COMPONENT_DEGRADATION_MODULES = {
    "reaction_wheel": _rw_degradation,
    "mtb": _mtb_degradation,
    "cmg": _cmg_degradation,
    "imu": _imu_degradation,
    "star_tracker": _star_tracker_degradation,
    "sun_sensor": _sun_sensor_degradation,
    "magnetometer": _magnetometer_degradation,
}


def _pick(component: str, index: int = 0) -> Any:
    return pick_default(_COMPONENT_DEGRADATION_MODULES[component], "default_degradations", component, index)


def default_degradation_scenarios() -> dict[str, SubsystemDegradationScenario]:
    """Return representative ADCS degradation scenarios."""

    rw_friction = bind_degradation(
        "reaction_wheel",
        _pick("reaction_wheel", 0),
        target_id="reaction_wheel.primary",
        mapping={"module": "reactionWheelStateEffector.ReactionWheelData", "parameter": "fCoulomb/fStatic/betaStatic/cViscous", "injection": "native_rw_config_parameter"},
    )
    mtb_drift = bind_degradation(
        "mtb",
        _pick("mtb", 1),
        target_id="mtb.primary",
        mapping={"module": "mtb_effector", "parameter": "magnetic_moment_factor", "injection": "periodic_command_gate"},
    )
    cmg_momentum = bind_degradation(
        "cmg",
        _pick("cmg", 1),
        target_id="cmg.primary",
        mapping={"module": "vscmg_effector", "parameter": "wheel_momentum/torque_authority", "injection": "periodic_module_parameter"},
    )
    imu_bias = bind_degradation(
        "imu",
        _pick("imu", 0),
        target_id="imu.gyro",
        mapping={"module": "imu", "parameter": "bias_rad_s", "injection": "periodic_message_adapter"},
    )
    star_noise = bind_degradation(
        "star_tracker",
        _pick("star_tracker", 0),
        target_id="star_tracker.primary",
        mapping={"module": "star_tracker", "parameter": "attitude_sigma/dropout", "injection": "periodic_message_adapter"},
    )
    sun_sensitivity = bind_degradation(
        "sun_sensor",
        _pick("sun_sensor", 0),
        target_id="sun_sensor.coarse",
        mapping={"module": "sun_sensor", "parameter": "sensitivity/bias", "injection": "periodic_message_adapter"},
    )
    mag_drift = bind_degradation(
        "magnetometer",
        _pick("magnetometer", 1),
        target_id="magnetometer.primary",
        mapping={"module": "magnetometer", "parameter": "scale_factor/bias", "injection": "periodic_message_adapter"},
    )

    return {
        "actuator_aging": SubsystemDegradationScenario(
            name="actuator_aging",
            component_degradations=(rw_friction, mtb_drift, cmg_momentum),
            description="Reaction wheel, magnetorquer, and CMG actuator authority degrades over time.",
        ),
        "sensor_aging": SubsystemDegradationScenario(
            name="sensor_aging",
            component_degradations=(imu_bias, star_noise, sun_sensitivity, mag_drift),
            description="ADCS sensor bias, noise, and scale-factor degradation grows over time.",
        ),
        "combined_adcs_aging": SubsystemDegradationScenario(
            name="combined_adcs_aging",
            component_degradations=(rw_friction, mtb_drift, cmg_momentum, imu_bias, star_noise, sun_sensitivity, mag_drift),
            description="Coverage scenario exercising every ADCS component degradation binding.",
        ),
    }


def build_adcs_degradation(
    *,
    rw_friction_factor: float = 0.0,
    sensor_noise_factor: float = 0.0,
    mtb_dipole_degradation_factor: float = 1.0,
    cmg_efficiency_factor: float = 1.0,
) -> ADCSDegradation:
    """Build the compact ADCS degradation object for higher-level callers."""

    return ADCSDegradation(
        rw_friction_factor=float(rw_friction_factor),
        sensor_noise_factor=float(sensor_noise_factor),
        mtb_dipole_degradation_factor=float(mtb_dipole_degradation_factor),
        cmg_efficiency_factor=float(cmg_efficiency_factor),
    )


def apply_adcs_degradation(cfg: AdcsConfig, degradation: ADCSDegradation) -> AdcsConfig:
    return replace(
        cfg,
        rw_friction_factor=cfg.rw_friction_factor + degradation.rw_friction_factor,
        sensor_noise_factor=cfg.sensor_noise_factor + degradation.sensor_noise_factor,
    )


def apply_actuator_degradation(cfg: AdcsConfig, degradation: ADCSDegradation) -> AdcsConfig:
    return apply_adcs_degradation(cfg, degradation)


def apply_sensor_degradation(cfg: AdcsConfig, degradation: ADCSDegradation) -> AdcsConfig:
    return replace(cfg, sensor_noise_factor=cfg.sensor_noise_factor + degradation.sensor_noise_factor)


def default_adcs_degradation() -> ADCSDegradation:
    return build_adcs_degradation()


def build_degradation_from_config(degradation_config: Optional[dict] = None) -> ADCSDegradation:
    if degradation_config is None:
        return default_adcs_degradation()
    return ADCSDegradation(
        rw_friction_factor=float(degradation_config.get("rw_friction_factor", 0.0)),
        sensor_noise_factor=float(degradation_config.get("sensor_noise_factor", 0.0)),
        mtb_dipole_degradation_factor=float(degradation_config.get("mtb_dipole_degradation_factor", 1.0)),
        cmg_efficiency_factor=float(degradation_config.get("cmg_efficiency_factor", 1.0)),
    )


__all__ = [
    "COMPONENT_COVERAGE",
    "ComponentDegradationBinding",
    "SubsystemDegradationScenario",
    "ADCSDegradation",
    "build_adcs_degradation",
    "ReactionWheelDegradation",
    "SensorDegradation",
    "apply_adcs_degradation",
    "apply_actuator_degradation",
    "apply_sensor_degradation",
    "default_adcs_degradation",
    "build_degradation_from_config",
    "default_degradation_scenarios",
    "build_degradation_update_specs",
    "register_degradation_events",
]
