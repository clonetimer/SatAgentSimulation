"""Legacy adapter for generic component and subsystem runner modules."""
from __future__ import annotations

from .base import LegacyExecutionAdapterBase

LEGACY_MODULE_ADAPTER_KEY = "legacy.module"


class LegacyModuleAdapter(LegacyExecutionAdapterBase):
    adapter_key = LEGACY_MODULE_ADAPTER_KEY
    route = "generic_module"

    def accepts(self, compiled: object) -> bool:
        return getattr(compiled, "task_type", None) in {"component", "subsystem"}


__all__ = ["LEGACY_MODULE_ADAPTER_KEY", "LegacyModuleAdapter"]
