"""Schema definitions for the thruster component."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ThrusterCommandConfig:
    num_thrusters: int = 4
    active_ids: tuple = (0, 1, 2, 3)
    nominal_on_time_s: float = 0.1
    min_pulse_s: float = 0.02
    use_min_pulse_time: bool = True
    stuck_closed_ids: tuple = ()


@dataclass(frozen=True)
class ThrusterPhysicalConfig:
    thrust_n: tuple = (1.0, 1.0, 1.0, 1.0)
    isp_s: tuple = (280.0, 280.0, 280.0, 280.0)
    directions_b: tuple = ((1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0))
    lever_arms_b_m: tuple = ((0, 0.5, 0), (0, 0.5, 0), (0.5, 0, 0), (0.5, 0, 0))
    area_nozzle_m2: tuple = ()
    thruster_mag_disp: tuple = ()
    cutoff_frequency_rad_s: tuple = ()
    max_swirl_torque_nm: tuple = ()
    thr_blowdown_coeff: tuple = ()
    isp_blowdown_coeff: tuple = ()
    labels: tuple = ()


@dataclass(frozen=True)
class ThrusterPulseResult:
    impulse_ns: tuple
    total_force_impulse_b_ns: tuple
    total_torque_impulse_b_nms: tuple
    propellant_used_kg: float


@dataclass(frozen=True)
class ThrusterPulseTrainResult:
    time_s: tuple
    cumulative_propellant_kg: tuple
    cumulative_impulse_ns: tuple
