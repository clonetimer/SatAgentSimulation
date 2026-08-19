"""Schema contracts for the cmg component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SingleGimbalCmgConfig:
    wheel_inertia_kg_m2: float = 1.0
    wheel_speed_rad_s: float = 10.0
    gimbal_angle_rad: float = 0.0
    spin_axis_g0_b: tuple = (1, 0, 0)
    gimbal_axis_b: tuple = (0, 0, 1)
    gimbal_rate_limit_rad_s: float | None = 1.0
    wheel_speed_limit_rad_s: float | None = 100.0


@dataclass(frozen=True)
class CmgConfig:
    num_cmgs: int = 4
    wheel_torque_limit_nm: float = 0.4
    gimbal_torque_limit_nm: float = 0.4


@dataclass(frozen=True)
class VscmgSpec:
    """Basilisk-native VSCMG construction parameters.

    The field names follow Basilisk ``VSCMGConfigMsgPayload`` semantics where
    practical.  ``Jt/Jg/Js`` are kept as compact engineering aliases and are
    mapped to the native wheel/gimbal inertia entries by the component builder.
    """

    gs_frame_gs_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    sg_hat_b: tuple[float, float, float] = (1.0, 0.0, 0.0)
    gt_hat_b: tuple[float, float, float] = (0.0, 1.0, 0.0)
    w_b: float = 1000.0 * 2 * 3.1415926535 / 60.0
    gamma_rad: float = 0.0
    gamma_dot_rad_s: float = 0.0
    Jt: float = 0.006
    Jg: float = 0.006
    Js: float = 0.12
    U_s: float = 0.0
    U_g: float = 0.0
    max_wheel_speed_rad_s: float = 6000.0 * 2 * 3.1415926535 / 60.0
    max_gimbal_rate_rad_s: float = 1.0
    max_torque_nm: float = 0.2

    # Native torque authority and static/friction offsets.
    u_s_max_nm: float | None = None
    u_s_min_nm: float = 0.0
    u_s_f_nm: float = 0.0
    u_g_max_nm: float | None = None
    u_g_min_nm: float = 0.0
    u_g_f_nm: float = 0.0
    wheel_linear_friction_ratio: float = 0.0
    gimbal_linear_friction_ratio: float = 0.0

    # Native mass and inertia entries.  If left unset, compact aliases above
    # populate the dominant diagonal entries.
    massV: float = 0.0
    massG: float = 0.0
    massW: float = 0.0
    IW1: float | None = None
    IW2: float | None = None
    IW3: float | None = None
    IW13: float = 0.0
    IG1: float | None = None
    IG2: float | None = None
    IG3: float | None = None
    IG12: float = 0.0
    IG13: float = 0.0
    IG23: float = 0.0
    IV1: float = 0.0
    IV2: float = 0.0
    IV3: float = 0.0
    rhoG: float = 0.0
    rhoW: float = 0.0
