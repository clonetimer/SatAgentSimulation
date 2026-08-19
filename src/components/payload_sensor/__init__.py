"""Payload sensor component.

Models:
- PayloadSensorConfig, PayloadSensorResult
- compute_payload_sensor

Basilisk Native:
- PayloadSensorBasilisk
- basilisk_available, require_basilisk
"""
from .builder import (
    PayloadSensorConfig,
    PayloadSensorResult,
    compute_payload_sensor,
    build_nominal_payload_sensor_config,
    PayloadSensorBasilisk,
    basilisk_available,
    require_basilisk,
    create_payload_sensor_basilisk,
)
from .faults import PayloadSensorFaultType  # noqa: F401
from .degradation import PayloadSensorDegradation, PayloadSensorDegradationRate  # noqa: F401

__all__ = [
    "PayloadSensorConfig",
    "PayloadSensorResult",
    "compute_payload_sensor",
    "build_nominal_payload_sensor_config",
    "PayloadSensorBasilisk",
    "basilisk_available",
    "require_basilisk",
    "create_payload_sensor_basilisk",
    "PayloadSensorFaultType",
    "PayloadSensorDegradation",
    "PayloadSensorDegradationRate",
]
