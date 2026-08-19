"""Public capability adapter remains available for validation and script export."""
from __future__ import annotations

from sat_sim.bsk_engine.unified_native import WholeSpacecraftUnifiedNativeAdapter


class AttitudeControlGraphCapabilityAdapter(WholeSpacecraftUnifiedNativeAdapter):
    """Compatibility facade; production execution is selected by execution.adapter_key."""


__all__ = ["AttitudeControlGraphCapabilityAdapter"]
