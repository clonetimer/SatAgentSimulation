"""Onboard storage component.

Models:
- OnboardStorageConfig, OnboardStorageState (from builder.py)
- step_onboard_storage (from builder.py)

Basilisk Native:
- OnboardStorageBasilisk (from builder.py)
- basilisk_available, require_basilisk (from builder.py)
"""
from .builder import (
    OnboardStorageConfig,
    OnboardStorageState,
    step_onboard_storage,
    OnboardStorageBasilisk,
    basilisk_available,
    require_basilisk,
    create_onboard_storage_basilisk,
)
from .faults import OnboardStorageFaultType  # noqa: F401
from .degradation import OnboardStorageDegradation, OnboardStorageDegradationRate  # noqa: F401

__all__ = [
    "OnboardStorageConfig",
    "OnboardStorageState",
    "step_onboard_storage",
    "OnboardStorageBasilisk",
    "basilisk_available",
    "require_basilisk",
    "create_onboard_storage_basilisk",
    "OnboardStorageFaultType",
    "OnboardStorageDegradation",
    "OnboardStorageDegradationRate",
]
