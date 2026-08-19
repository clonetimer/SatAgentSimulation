"""Schema contracts for the imu component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

Identity3 = tuple[float, float, float, float, float, float, float, float, float]
Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class ImuConfig:
    gyro_scale: Vector3 = (1.0, 1.0, 1.0)
    accel_scale: Vector3 = (1.0, 1.0, 1.0)
    gyro_bias_rad_s: Vector3 = (0.0, 0.0, 0.0)
    accel_bias_m_s2: Vector3 = (0.0, 0.0, 0.0)
    gyro_bias_walk_std_rad_s_sqrt_s: float = 0.0
    accel_bias_walk_std_m_s2_sqrt_s: float = 0.0
    sensor_pos_b_m: Vector3 = (0.0, 0.0, 0.0)
    dcm_pb: Identity3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    sen_rot_max_rad_s: float = 1.0e6
    sen_trans_max_m_s2: float = 1.0e6
    p_matrix_accel: Identity3 = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    a_matrix_accel: Identity3 = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    p_matrix_gyro: Identity3 = (0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    a_matrix_gyro: Identity3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
    output_buffer_count: int = 2
