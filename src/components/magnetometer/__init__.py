from .builder import (  # noqa: F401
    MagnetometerConfig,
    build_magnetometer,
    build_nominal_magnetometer_config,
    measure_magnetic_field,
    measure_magnetic_field_body,
    measure_magnetic_field_sensor,
    require_basilisk_magnetometer,
)
from .faults import MagnetometerFaultType  # noqa: F401
from .degradation import MagnetometerDegradation  # noqa: F401
