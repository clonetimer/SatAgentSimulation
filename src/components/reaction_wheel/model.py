"""Pure-Python reference model for the reaction wheel component."""
from __future__ import annotations

from .degradation import apply_reaction_wheel_degradation
from ..dynamic_models import expand, norm, unit
from .faults import FaultSpec
from .degradation import ReactionWheelDegradation
from .faults import RWFaultType
from .schemas import (
    ReactionWheelCommandConfig,
    ReactionWheelDynamicsConfig,
    ReactionWheelProfileResult,
    ReactionWheelState,
)


def norm_rw(config: ReactionWheelDynamicsConfig) -> ReactionWheelDynamicsConfig:
    wheel_count = config.num_wheels
    return ReactionWheelDynamicsConfig(
        wheel_count,
        expand(config.wheel_inertia_kg_m2, wheel_count, 0.1),
        expand(config.max_motor_torque_nm, wheel_count, 0.2),
        expand(config.max_speed_rad_s, wheel_count, 6000),
        expand(config.damping_nms, wheel_count, 0),
        tuple(unit(config.wheel_axes_B[i] if i < len(config.wheel_axes_B) else (1, 0, 0)) for i in range(wheel_count)),
    )


def clamp_motor_torque(raw: tuple | list, config: ReactionWheelDynamicsConfig) -> tuple[float, ...]:
    normalized = norm_rw(config)
    return tuple(
        max(
            -normalized.max_motor_torque_nm[i],
            min(normalized.max_motor_torque_nm[i], float(raw[i]) if i < len(raw) else 0.0),
        )
        for i in range(normalized.num_wheels)
    )


def step_wheel_speed(
    state: ReactionWheelState,
    command_torque_nm: tuple | list,
    config: ReactionWheelDynamicsConfig,
    dt_s: float,
) -> ReactionWheelState:
    if dt_s < 0:
        raise ValueError("dt_s")
    normalized = norm_rw(config)
    torque = clamp_motor_torque(command_torque_nm, normalized)
    wheel_speeds = expand(state.wheel_speeds_rad_s, normalized.num_wheels, 0)
    return ReactionWheelState(
        tuple(
            max(
                -normalized.max_speed_rad_s[i],
                min(
                    normalized.max_speed_rad_s[i],
                    wheel_speeds[i]
                    + dt_s * (torque[i] - normalized.damping_nms[i] * wheel_speeds[i]) / normalized.wheel_inertia_kg_m2[i],
                ),
            )
            for i in range(normalized.num_wheels)
        )
    )


def total_momentum_vector(state: ReactionWheelState, config: ReactionWheelDynamicsConfig) -> tuple[float, float, float]:
    normalized = norm_rw(config)
    wheel_speeds = expand(state.wheel_speeds_rad_s, normalized.num_wheels, 0)
    wheel_momenta = [
        tuple(normalized.wheel_inertia_kg_m2[i] * wheel_speeds[i] * axis for axis in normalized.wheel_axes_B[i])
        for i in range(normalized.num_wheels)
    ]
    return tuple(sum(momentum[k] for momentum in wheel_momenta) for k in range(3))


def momentum_norm_nms(state: ReactionWheelState, config: ReactionWheelDynamicsConfig) -> float:
    return norm(total_momentum_vector(state, config))


def rotational_energy_j(state: ReactionWheelState, config: ReactionWheelDynamicsConfig) -> float:
    normalized = norm_rw(config)
    wheel_speeds = expand(state.wheel_speeds_rad_s, normalized.num_wheels, 0)
    return sum(0.5 * normalized.wheel_inertia_kg_m2[i] * wheel_speeds[i] ** 2 for i in range(normalized.num_wheels))


def simulate_prescribed_torque_profile(
    state: ReactionWheelState,
    profile: list[list[float]] | tuple[tuple[float, ...], ...],
    config: ReactionWheelDynamicsConfig,
    dt_s: float,
) -> ReactionWheelProfileResult:
    if dt_s <= 0:
        raise ValueError("dt_s")
    time_s = [0.0]
    wheel_speeds = [state.wheel_speeds_rad_s]
    motor_torque_nm = []
    rotational_energy = [rotational_energy_j(state, config)]
    momentum_norm = [momentum_norm_nms(state, config)]
    current_state = state
    for index, command in enumerate(profile):
        torque = clamp_motor_torque(command, config)
        motor_torque_nm.append(torque)
        current_state = step_wheel_speed(current_state, torque, config, dt_s)
        time_s.append((index + 1) * dt_s)
        wheel_speeds.append(current_state.wheel_speeds_rad_s)
        rotational_energy.append(rotational_energy_j(current_state, config))
        momentum_norm.append(momentum_norm_nms(current_state, config))
    return ReactionWheelProfileResult(
        tuple(time_s),
        tuple(wheel_speeds),
        tuple(motor_torque_nm),
        tuple(rotational_energy),
        tuple(momentum_norm),
    )


def apply_rw_command_faults(raw: tuple | list, config: ReactionWheelCommandConfig) -> list[float]:
    output = [0.0] * config.num_wheels
    for i in range(min(config.num_wheels, len(raw))):
        value = 0.0 if abs(raw[i]) < config.deadzone_nm else max(
            -config.torque_limit_nm,
            min(config.torque_limit_nm, float(raw[i])),
        )
        output[i] = value
    for wheel_index in config.failed_ids:
        if 0 <= wheel_index < len(output):
            output[wheel_index] = 0.0
    for wheel_index, value in config.stuck_torque_by_id.items():
        if 0 <= wheel_index < len(output):
            output[wheel_index] = max(-config.torque_limit_nm, min(config.torque_limit_nm, float(value)))
    return output


def apply_rw_config_faults(
    config: ReactionWheelDynamicsConfig,
    fault_specs: list[FaultSpec],
) -> tuple[ReactionWheelDynamicsConfig, tuple, dict]:
    failed_ids = tuple()
    stuck_torque_by_id = {}
    updated_config = config

    for spec in fault_specs:
        if not isinstance(spec.fault_type, RWFaultType):
            continue

        wheel_index = int(spec.target_id.split("_")[-1]) if spec.target_id else 0

        if spec.fault_type == RWFaultType.Jamming:
            if wheel_index < updated_config.num_wheels:
                failed_ids = (*failed_ids, wheel_index)

        elif spec.fault_type == RWFaultType.MotorFailure:
            if wheel_index < updated_config.num_wheels:
                torques = list(updated_config.max_motor_torque_nm)
                torques[wheel_index] *= (1.0 - spec.magnitude * 0.8)
                updated_config = ReactionWheelDynamicsConfig(
                    num_wheels=updated_config.num_wheels,
                    wheel_inertia_kg_m2=updated_config.wheel_inertia_kg_m2,
                    max_motor_torque_nm=tuple(torques),
                    max_speed_rad_s=updated_config.max_speed_rad_s,
                    damping_nms=updated_config.damping_nms,
                    wheel_axes_B=updated_config.wheel_axes_B,
                )

    return updated_config, failed_ids, stuck_torque_by_id


def apply_rw_config_constraints(
    config: ReactionWheelDynamicsConfig,
    constraint_specs: list[object],
) -> ReactionWheelDynamicsConfig:
    """Apply normal operational limits without marking the wheel as failed."""

    from .constraints import RWConstraintType

    updated_config = config
    for spec in constraint_specs:
        raw_type = getattr(spec, "constraint_type", getattr(spec, "fault_type", ""))
        constraint_type = str(getattr(raw_type, "value", raw_type))
        if constraint_type not in {RWConstraintType.SPEED_LIMIT.value, "reaction_wheel_speed_limit"}:
            continue
        target_id = str(getattr(spec, "target_id", "rw_0") or "rw_0")
        try:
            wheel_index = int(target_id.split("_")[-1])
        except Exception:
            wheel_index = 0
        if wheel_index >= updated_config.num_wheels:
            continue
        magnitude = max(0.0, min(1.0, float(getattr(spec, "magnitude", 1.0))))
        speeds = list(updated_config.max_speed_rad_s)
        speeds[wheel_index] *= max(0.05, 1.0 - 0.95 * magnitude)
        updated_config = ReactionWheelDynamicsConfig(
            num_wheels=updated_config.num_wheels,
            wheel_inertia_kg_m2=updated_config.wheel_inertia_kg_m2,
            max_motor_torque_nm=updated_config.max_motor_torque_nm,
            max_speed_rad_s=tuple(speeds),
            damping_nms=updated_config.damping_nms,
            wheel_axes_B=updated_config.wheel_axes_B,
        )
    return updated_config


def _build_nominal_reaction_wheel_dynamics_config_base_impl(
    num_wheels: int = 1,
    wheel_inertia_kg_m2: tuple[float, ...] = (0.1,),
    max_motor_torque_nm: tuple[float, ...] = (0.02,),
    max_speed_rad_s: tuple[float, ...] = (1000,),
    degradation: ReactionWheelDegradation | None = None,
) -> ReactionWheelDynamicsConfig:
    config = ReactionWheelDynamicsConfig(
        num_wheels=num_wheels,
        wheel_inertia_kg_m2=wheel_inertia_kg_m2,
        max_motor_torque_nm=max_motor_torque_nm,
        max_speed_rad_s=max_speed_rad_s,
    )
    if degradation is not None:
        config = apply_reaction_wheel_degradation(config, degradation)
    return config


def _build_nominal_reaction_wheel_config_base_impl(
    degradation: ReactionWheelDegradation | None = None,
    fault_specs: list[FaultSpec] | None = None,
) -> ReactionWheelDynamicsConfig:
    config = build_nominal_reaction_wheel_dynamics_config(degradation=degradation)
    if fault_specs:
        config, _, _ = apply_rw_config_faults(config, fault_specs)
    return config

# Component fault/degradation compatibility wrappers
from .degradation import ReactionWheelDegradationRate
from .degradation import compute_degradation_state
from .faults import apply_reaction_wheel_faults
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_reaction_wheel_config_base = _build_nominal_reaction_wheel_config_base_impl

def build_nominal_reaction_wheel_config(
    *args,
    degradation: ReactionWheelDegradation | None = None,
    degradation_rate: ReactionWheelDegradationRate | None = None,
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
    config = _build_nominal_reaction_wheel_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_reaction_wheel_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_reaction_wheel_faults(config, fault_specs)
    return config

_build_nominal_reaction_wheel_dynamics_config_base = _build_nominal_reaction_wheel_dynamics_config_base_impl

def build_nominal_reaction_wheel_dynamics_config(
    *args,
    degradation: ReactionWheelDegradation | None = None,
    degradation_rate: ReactionWheelDegradationRate | None = None,
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
    config = _build_nominal_reaction_wheel_dynamics_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_reaction_wheel_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_reaction_wheel_faults(config, fault_specs)
    return config

