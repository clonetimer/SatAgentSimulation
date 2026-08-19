"""Schema contracts for the radiator component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class RadiatorConfig:
    area_m2: float = 0.35
    emissivity: float = 0.82
    effective_sink_temp_k: float = 250.0
    max_rejection_w: float = 180.0
