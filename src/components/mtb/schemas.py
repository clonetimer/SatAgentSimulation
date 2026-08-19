"""Schema contracts for the mtb component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class MtbConfig:
    num_axes: int = 3
    dipole_limit_am2: tuple = (1, 1, 1)
    lag_tau_s: float = 0.0

@dataclass(frozen=True)
class MtbSpec:
    """Specification for a single magnetic torque bar.

    Attributes
    ----------
    axis_b : tuple of float
        Unit direction vector of the dipole axis in body frame.
    max_dipole_am2 : float
        Maximum dipole moment in ampere-square meters.
    """
    axis_b: tuple[float, float, float]
    max_dipole_am2: float = 0.2
