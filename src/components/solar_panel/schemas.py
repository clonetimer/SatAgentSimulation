"""Schema contracts for the solar panel component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

SOLAR_CONSTANT_W_M2 = 1367.0
Vector3 = tuple[float, float, float]

@dataclass(frozen=True)
class SolarPanelConfig:
    max_power_w: float = 120.0
    efficiency: float = 0.28
    max_slew_rate_rad_s: float = 0.05

@dataclass(frozen=True)
class SolarPanelNativeConfig:
    """Configuration for a Basilisk-compatible solar-panel model.

    ``max_power_w`` follows the project's user-facing convention:
    ``P = max_power_w * efficiency * shadow_factor * cos(theta)``.

    For Basilisk ``simpleSolarPanel``, ``max_power_w`` is converted to an
    equivalent area through ``max_power_w / solar_constant_w_m2`` because the
    native module uses solar flux, panel area, and efficiency.
    """

    model_tag: str = "SolarPanel"
    initial_normal_b: Vector3 = (1.0, 0.0, 0.0)
    max_power_w: float = 100.0
    panel_area_m2: float | None = None
    efficiency: float = 1.0
    shadow_factor: float = 1.0
    sun_direction_b: Vector3 = (1.0, 0.0, 0.0)
    enable_tracking: bool = False
    max_slew_rate_rad_s: float = 0.0
    solar_constant_w_m2: float = SOLAR_CONSTANT_W_M2
    fallback_sun_b: Vector3 = (1.0, 0.0, 0.0)
    fallback_shadow: float = 1.0

    @property
    def equivalent_panel_area_m2(self) -> float:
        if self.panel_area_m2 is not None:
            return max(0.0, float(self.panel_area_m2))
        return max(0.0, float(self.max_power_w)) / max(
            float(self.solar_constant_w_m2), 1e-12
        )
