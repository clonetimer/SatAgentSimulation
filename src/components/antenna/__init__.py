"""Antenna component package.

Provides antenna configuration, gain computation, and Basilisk integration.
"""
from .builder import (
    AntennaConfig,
    AntennaResult,
    AntennaBasilisk,
    compute_antenna_gain,
    build_nominal_antenna_config,
    basilisk_available,
    require_basilisk,
    create_antenna_basilisk,
)
from .faults import AntennaFaultType  # noqa: F401
from .degradation import AntennaDegradation, AntennaDegradationRate  # noqa: F401

__all__ = [
    "AntennaConfig",
    "AntennaResult",
    "AntennaBasilisk",
    "compute_antenna_gain",
    "build_nominal_antenna_config",
    "basilisk_available",
    "require_basilisk",
    "create_antenna_basilisk",
    "AntennaFaultType",
    "AntennaDegradation",
    "AntennaDegradationRate",
]
