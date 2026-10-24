"""Validate the real Basilisk CoarseSunSensor boresight in the spacecraft B frame.

This shared pre-start/finite TaskSpec validation does not synthesize sensor
physics. The returned unit vector is forwarded to official C++ nHat_B.
"""
from __future__ import annotations

import math
from typing import Any, Mapping


_DEFAULT = (0.0, 0.0, -1.0)


def resolve_sun_sensor_boresight(values: Mapping[str, Any]) -> tuple[float, float, float]:
    """Require all three components when overriding, reject invalid geometry.

    The zero-config legacy model is exactly (0, 0, -1). An explicit partial
    override is rejected, never silently mixed with legacy components.
    """
    keys = tuple(f"sun_sensor_normal_b_{axis}" for axis in "xyz")
    present = tuple(key in values and values[key] is not None for key in keys)
    if not any(present):
        return _DEFAULT
    if not all(present):
        raise ValueError("NATIVE_SUN_SENSOR_NORMAL_TRIPLE_REQUIRED")
    components = []
    for key in keys:
        value = values[key]
        if type(value) not in (int, float):
            raise ValueError("NATIVE_SUN_SENSOR_NORMAL_INVALID")
        val = float(value)
        if not math.isfinite(val) or not -1.0 <= val <= 1.0:
            raise ValueError("NATIVE_SUN_SENSOR_NORMAL_INVALID")
        components.append(val)
    magnitude_sq = sum(x * x for x in components)
    if not 0.999 <= magnitude_sq <= 1.001:
        raise ValueError("NATIVE_SUN_SENSOR_NORMAL_NOT_UNIT")
    return tuple(components)  # type: ignore[return-value]
