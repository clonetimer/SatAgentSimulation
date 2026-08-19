"""Schema contracts for the ground station component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class GroundStationConfig:
    min_elevation_deg: float = 5.0
    max_range_m: float = 2e6

@dataclass(frozen=True)
class GroundAccessNativeConfig:
    duration_s: float = 60.0
    step_s: float = 5.0
    planet_radius_m: float = 6371000.0
    ground_lat_rad: float = 0.0
    ground_lon_rad: float = 0.0
    ground_alt_m: float = 0.0
    spacecraft_radius_m: float = 700.0e3
    minimum_elevation_rad: float = 0.1745
    maximum_range_m: float = 1.0e9
