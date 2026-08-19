"""Legacy adapter for whole-spacecraft execution."""
from __future__ import annotations

from .base import LegacyExecutionAdapterBase

LEGACY_WHOLE_SPACECRAFT_ADAPTER_KEY = "legacy.whole_spacecraft"


class LegacyWholeSpacecraftAdapter(LegacyExecutionAdapterBase):
    adapter_key = LEGACY_WHOLE_SPACECRAFT_ADAPTER_KEY
    route = "whole_spacecraft"

    def accepts(self, compiled: object) -> bool:
        return getattr(compiled, "task_type", None) == "whole_spacecraft"


__all__ = ["LEGACY_WHOLE_SPACECRAFT_ADAPTER_KEY", "LegacyWholeSpacecraftAdapter"]
