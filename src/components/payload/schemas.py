"""Schema contracts for the payload component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class PayloadInstrumentConfig:
    name: str
    observation_power_w: float = 18.0
    standby_power_w: float = 3.0
    data_rate_bps: float = 250_000.0
    heat_fraction: float = 0.85
    max_pointing_error_deg: float = 0.25
    allowed_modes: tuple[str, ...] = ("observation", "payload", "NOMINAL_OBSERVATION")
