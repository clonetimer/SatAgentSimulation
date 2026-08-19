"""Pure-Python reference model for the thruster component."""
from __future__ import annotations

from dataclasses import replace

from .degradation import ThrusterDegradation
from .degradation import apply_thruster_degradation
from ..dynamic_models import cross, v3
from .faults import FaultSpec
from .faults import ThrusterFaultType
from .schemas import (
    ThrusterCommandConfig,
    ThrusterPhysicalConfig,
    ThrusterPulseResult,
    ThrusterPulseTrainResult,
)


G0 = 9.80665


def shape_thruster_on_time(config: ThrusterCommandConfig, on: bool) -> list[float]:
    out = [0.0] * config.num_thrusters
    if on:
        for idx in config.active_ids:
            if 0 <= idx < config.num_thrusters:
                out[idx] = max(config.nominal_on_time_s, config.min_pulse_s)
    for idx in config.stuck_closed_ids:
        if 0 <= idx < config.num_thrusters:
            out[idx] = 0.0
    return out


def _g(seq, i, default):
    return seq[i] if i < len(seq) else default


def compute_thruster_pulse(on_time_s, config: ThrusterPhysicalConfig) -> ThrusterPulseResult:
    force = [0.0, 0.0, 0.0]
    torque = [0.0, 0.0, 0.0]
    impulses = []
    propellant_mass = 0.0
    for i, duration_s in enumerate(on_time_s):
        thrust_n = float(_g(config.thrust_n, i, config.thrust_n[-1]))
        impulse = thrust_n * max(0.0, float(duration_s))
        impulses.append(impulse)
        direction = v3(_g(config.directions_b, i, (1, 0, 0)))
        lever_arm = v3(_g(config.lever_arms_b_m, i, (0, 0, 0)))
        for k in range(3):
            force[k] += impulse * direction[k]
        torque_impulse = cross(lever_arm, [impulse * x for x in direction])
        for k in range(3):
            torque[k] += torque_impulse[k]
        propellant_mass += impulse / (max(1e-12, float(_g(config.isp_s, i, config.isp_s[-1]))) * G0)
    return ThrusterPulseResult(tuple(impulses), tuple(force), tuple(torque), propellant_mass)


def simulate_pulse_train(profile, config: ThrusterPhysicalConfig, dt_s: float) -> ThrusterPulseTrainResult:
    if dt_s <= 0:
        raise ValueError("dt_s")
    time_s = [0.0]
    cumulative_propellant_kg = [0.0]
    cumulative_impulse_ns = [0.0]
    propellant = 0.0
    impulse = 0.0
    for i, row in enumerate(profile):
        result = compute_thruster_pulse(row, config)
        propellant += result.propellant_used_kg
        impulse += sum(result.impulse_ns)
        time_s.append((i + 1) * dt_s)
        cumulative_propellant_kg.append(propellant)
        cumulative_impulse_ns.append(impulse)
    return ThrusterPulseTrainResult(tuple(time_s), tuple(cumulative_propellant_kg), tuple(cumulative_impulse_ns))


def build_nominal_thruster_command_config(
    num_thrusters: int = 4,
    active_ids: tuple = (0, 1, 2, 3),
    nominal_on_time_s: float = 0.1,
    min_pulse_s: float = 0.02,
) -> ThrusterCommandConfig:
    return ThrusterCommandConfig(
        num_thrusters=num_thrusters,
        active_ids=active_ids,
        nominal_on_time_s=nominal_on_time_s,
        min_pulse_s=min_pulse_s,
    )


def _build_nominal_thruster_physical_config_base_impl(
    thrust_n: tuple = (1.0, 1.0, 1.0, 1.0),
    isp_s: tuple = (280.0, 280.0, 280.0, 280.0),
    directions_b: tuple = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0)),
    lever_arms_b_m: tuple = ((0, 0.5, 0), (0, 0.5, 0), (0.5, 0, 0), (0.5, 0, 0)),
    degradation: ThrusterDegradation | None = None,
) -> ThrusterPhysicalConfig:
    config = ThrusterPhysicalConfig(
        thrust_n=thrust_n,
        isp_s=isp_s,
        directions_b=directions_b,
        lever_arms_b_m=lever_arms_b_m,
    )
    if degradation is not None:
        config = apply_thruster_degradation(config, degradation)
    return config


def apply_thruster_config_faults(config: ThrusterPhysicalConfig, fault_specs: list[FaultSpec]) -> ThrusterPhysicalConfig:
    updated = config
    for spec in fault_specs:
        if not isinstance(spec.fault_type, ThrusterFaultType):
            continue
        thruster_idx = int(spec.target_id.split("_")[-1]) if spec.target_id and "_" in spec.target_id else 0
        if spec.fault_type == ThrusterFaultType.IgnitionFailure:
            if thruster_idx < len(updated.thrust_n):
                thrusts = list(updated.thrust_n)
                thrusts[thruster_idx] = 0.0
                updated = replace(updated, thrust_n=tuple(thrusts))
        elif spec.fault_type == ThrusterFaultType.NozzleBlockage:
            if thruster_idx < len(updated.thrust_n):
                thrusts = list(updated.thrust_n)
                isps = list(updated.isp_s)
                thrusts[thruster_idx] *= (1.0 - spec.magnitude)
                isps[thruster_idx] *= (1.0 - spec.magnitude * 0.5)
                updated = replace(updated, thrust_n=tuple(thrusts), isp_s=tuple(isps))
        elif spec.fault_type == ThrusterFaultType.FuelLeak:
            if thruster_idx < len(updated.thrust_n):
                isps = list(updated.isp_s)
                isps[thruster_idx] *= (1.0 - spec.magnitude * 0.2)
                updated = replace(updated, isp_s=tuple(isps))
    return updated


def _build_nominal_thruster_config_base_impl(
    degradation: ThrusterDegradation | None = None,
    fault_specs: list[FaultSpec] | None = None,
) -> ThrusterPhysicalConfig:
    config = build_nominal_thruster_physical_config(degradation=degradation)
    if fault_specs:
        config = apply_thruster_config_faults(config, fault_specs)
    return config

# Component fault/degradation compatibility wrappers
from .degradation import ThrusterDegradationRate
from .degradation import compute_degradation_state
from .faults import apply_thruster_faults
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_thruster_config_base = _build_nominal_thruster_config_base_impl

def build_nominal_thruster_config(
    *args,
    degradation: ThrusterDegradation | None = None,
    degradation_rate: ThrusterDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_thruster_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_thruster_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_thruster_faults(config, fault_specs)
    return config

_build_nominal_thruster_physical_config_base = _build_nominal_thruster_physical_config_base_impl

def build_nominal_thruster_physical_config(
    *args,
    degradation: ThrusterDegradation | None = None,
    degradation_rate: ThrusterDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_thruster_physical_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_thruster_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_thruster_faults(config, fault_specs)
    return config

