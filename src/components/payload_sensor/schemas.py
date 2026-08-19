"""Schema contracts for the payload sensor component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class PayloadSensorConfig:
    nominal_data_rate_bps: float = 250_000.0
    max_pointing_error_deg: float = 0.25
    min_quality_score: float = 0.5
