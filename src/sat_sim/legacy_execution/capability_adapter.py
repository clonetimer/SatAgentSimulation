"""Legacy adapter for capability-backed compiled tasks."""
from __future__ import annotations

from collections.abc import Mapping

from .base import LegacyExecutionAdapterBase

LEGACY_CAPABILITY_ADAPTER_KEY = "legacy.capability"


class LegacyCapabilityAdapter(LegacyExecutionAdapterBase):
    adapter_key = LEGACY_CAPABILITY_ADAPTER_KEY
    route = "capability_adapter"

    def accepts(self, compiled: object) -> bool:
        metadata = getattr(compiled, "metadata", None)
        return isinstance(metadata, Mapping) and bool(metadata.get("capability_id"))


__all__ = ["LEGACY_CAPABILITY_ADAPTER_KEY", "LegacyCapabilityAdapter"]
