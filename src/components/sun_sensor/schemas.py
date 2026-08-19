"""Schema contracts for the sun sensor component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

Identity3 = tuple[float, float, float, float, float, float, float, float, float]
Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class SunSensorConfig:
    min_intensity: float = 1e-6
    noise_std: float = 0.0
    accuracy: float = 1.0
    n_hat_b: Vector3 = (1.0, 0.0, 0.0)
    fov_rad: float = 1.5707963267948966
    scale_factor: float = 1.0
    bias: float = 0.0
    walk_bounds: float = -1.0
    fault_noise_std: float = 0.5
    max_output: float = 1.0e6
    min_output: float = 0.0
    k_power: float = 2.0
    css_group_id: int = -1
    fault_state: int | None = None
    sensor_pos_b_m: Vector3 = (0.0, 0.0, 0.0)
    sensor_pos_pb_b_m: Vector3 = (0.0, 0.0, 0.0)
    b2p321_angles_rad: Vector3 = (0.0, 0.0, 0.0)
    dcm_pb: Identity3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
