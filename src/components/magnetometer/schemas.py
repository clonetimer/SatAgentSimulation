"""Schema contracts for the magnetometer component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

Identity3 = tuple[float, float, float, float, float, float, float, float, float]
Vector3 = tuple[float, float, float]


@dataclass(frozen=True)
class MagnetometerConfig:
    # Project lightweight sensor model keeps axis_scale.  Basilisk native uses
    # the scalar scale_factor below.
    axis_scale: Vector3 = (1.0, 1.0, 1.0)
    scale_factor: float = 1.0
    bias_t: Vector3 = (0.0, 0.0, 0.0)
    mounting_matrix_sb: Identity3 | tuple[tuple[float, float, float], ...] | None = None
    clip_t: float | tuple[float, float] | None = None
    noise_std_t: float | Vector3 = 0.0
    walk_bounds_t: float | Vector3 = 0.0
    max_output_t: float | None = None
    min_output_t: float | None = None
    stuck_value_t: Vector3 | None = None
    spike_probability: float | Vector3 | None = None
    spike_amount_t: float | Vector3 | None = None
    fault_state_axis: int | None = None
    noise_seed: int | None = None
    noise_std_nT: float = 0.0
