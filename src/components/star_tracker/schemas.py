"""Schema contracts for the star tracker component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

Identity3 = tuple[float, float, float, float, float, float, float, float, float]


@dataclass(frozen=True)
class StarTrackerConfig:
    drift_rate_mrp_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    max_drift_norm: float = 1.0
    dcm_cb: Identity3 = (1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0)
