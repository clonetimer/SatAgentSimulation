"""Schema definitions for the reaction wheel component."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ReactionWheelCommandConfig:
    num_wheels: int = 4
    torque_limit_nm: float = 0.2
    deadzone_nm: float = 0.0
    failed_ids: tuple = ()
    stuck_torque_by_id: dict = field(default_factory=dict)


@dataclass(frozen=True)
class ReactionWheelDynamicsConfig:
    num_wheels: int = 4
    wheel_inertia_kg_m2: tuple = (0.1, 0.1, 0.1, 0.1)
    max_motor_torque_nm: tuple = (0.2, 0.2, 0.2, 0.2)
    max_speed_rad_s: tuple = (6000, 6000, 6000, 6000)
    damping_nms: tuple = (0, 0, 0, 0)
    wheel_axes_B: tuple = ((1, 0, 0), (0, 1, 0), (0, 0, 1), (0.577350269, 0.577350269, 0.577350269))


@dataclass(frozen=True)
class ReactionWheelState:
    wheel_speeds_rad_s: tuple


@dataclass(frozen=True)
class ReactionWheelProfileResult:
    time_s: tuple
    wheel_speeds_rad_s: tuple
    motor_torque_nm: tuple
    rotational_energy_j: tuple
    momentum_norm_nms: tuple


@dataclass(frozen=True)
class ReactionWheelSpec:
    """Basilisk-native reaction-wheel construction parameters.

    Friction fields intentionally mirror ``simIncludeRW.rwFactory.create`` so
    component builders can map them without subsystem- or spacecraft-level
    reimplementation.
    """

    axis_b: tuple[float, float, float]
    model: str = "Honeywell_HR16"
    max_momentum_nms: float = 50.0
    initial_speed_rpm: float = 0.0
    wheel_js: float = 0.015
    u_max_nm: float = 0.25
    omega_max_rad_s: float = 1047.2
    use_min_torque: bool = False
    u_min_nm: float = 0.0
    max_power_w: float = -1.0
    position_b_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    label: str | None = None
    use_rw_friction: bool = False
    fCoulomb: float = 0.0
    fStatic: float = 0.0
    betaStatic: float = -1.0
    cViscous: float = 0.0


@dataclass(frozen=True)
class ReactionWheelBundle:
    factory: object
    effector: object
    config_msg: object
    wheel_count: int
