"""Explicit compatibility boundary for pre-A3 model runners."""
from .capability_adapter import LEGACY_CAPABILITY_ADAPTER_KEY, LegacyCapabilityAdapter
from .module_adapter import LEGACY_MODULE_ADAPTER_KEY, LegacyModuleAdapter
from .orbit_environment_adapter import LEGACY_ORBIT_ENVIRONMENT_ADAPTER_KEY, LegacyOrbitEnvironmentAdapter
from .whole_spacecraft_adapter import LEGACY_WHOLE_SPACECRAFT_ADAPTER_KEY, LegacyWholeSpacecraftAdapter

LEGACY_EXECUTION_VERSION = "sat-sim.legacy-execution.a2r.v1"

__all__ = [
    "LEGACY_EXECUTION_VERSION",
    "LEGACY_CAPABILITY_ADAPTER_KEY",
    "LEGACY_MODULE_ADAPTER_KEY",
    "LEGACY_ORBIT_ENVIRONMENT_ADAPTER_KEY",
    "LEGACY_WHOLE_SPACECRAFT_ADAPTER_KEY",
    "LegacyCapabilityAdapter",
    "LegacyModuleAdapter",
    "LegacyOrbitEnvironmentAdapter",
    "LegacyWholeSpacecraftAdapter",
]
