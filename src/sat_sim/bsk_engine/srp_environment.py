"""Basilisk 2.11.0 SRP option contract; no external ephemeris or CAD inference.

The model is deliberately disabled in R0. Enabled cannonball force requires
explicit scalar engineering area/reflectivity and the same offline SPICE/Eclipse
orbit environment as the recommended whole-spacecraft native runtime.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping, Any


@dataclass(frozen=True)
class NativeSrpSelection:
    model: str
    reference_area_m2: float | None
    coefficient_reflection: float | None

    @classmethod
    def from_values(
        cls, values: Mapping[str, Any], *,
        environment_profile: str, adcs_only: bool = False,
    ) -> "NativeSrpSelection":
        model = values.get("srp_model", "disabled")
        if type(model) is not str or model not in {"disabled", "cannonball"}:
            raise ValueError("NATIVE_SRP_MODEL_UNSUPPORTED")
        if model == "disabled":
            return cls("disabled", None, None)
        if adcs_only:
            raise ValueError("NATIVE_SRP_REQUIRES_WHOLE_SPACECRAFT")
        if environment_profile != "spice_wmm":
            raise ValueError("NATIVE_SRP_REQUIRES_SPICE_WMM")
        if values.get("srp_reference_area_m2") is None or values.get("srp_coefficient_reflection") is None:
            raise ValueError("NATIVE_SRP_EXPLICIT_AREA_AND_COEFFICIENT_REQUIRED")
        raw_area = values["srp_reference_area_m2"]
        raw_reflection = values["srp_coefficient_reflection"]
        if type(raw_area) not in (int, float) or type(raw_reflection) not in (int, float):
            raise ValueError("NATIVE_SRP_OPTICAL_PARAMETERS_INVALID")
        area, reflection = float(raw_area), float(raw_reflection)
        if not (math.isfinite(area) and 0.0 < area <= 1000.0 and
                math.isfinite(reflection) and 0.0 < reflection <= 2.0):
            raise ValueError("NATIVE_SRP_OPTICAL_PARAMETERS_INVALID")
        return cls(model, area, reflection)

    @property
    def enabled(self) -> bool:
        return self.model == "cannonball"
