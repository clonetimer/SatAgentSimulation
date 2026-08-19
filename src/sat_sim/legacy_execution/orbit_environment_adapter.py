"""Legacy adapter for orbit/environment execution."""
from __future__ import annotations

from .base import LegacyExecutionAdapterBase

LEGACY_ORBIT_ENVIRONMENT_ADAPTER_KEY = "legacy.orbit_environment"


class LegacyOrbitEnvironmentAdapter(LegacyExecutionAdapterBase):
    adapter_key = LEGACY_ORBIT_ENVIRONMENT_ADAPTER_KEY
    route = "orbit_environment"

    def accepts(self, compiled: object) -> bool:
        return getattr(compiled, "task_type", None) == "orbit_environment"


__all__ = ["LEGACY_ORBIT_ENVIRONMENT_ADAPTER_KEY", "LegacyOrbitEnvironmentAdapter"]
