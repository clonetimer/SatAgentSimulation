"""Validated physical Basilisk reaction wheel axes and B-frame installation points.

No numeric authority is inferred from UI-only geometry. The returned vectors
feed the actual upstream rwFactory.create(gsHat_B=..., rWB_B=...).
"""
from __future__ import annotations

from itertools import combinations
import math
from typing import Any, Mapping, Sequence


def _number(raw: Any, *, limit: float, error: str) -> float:
    if type(raw) not in (int, float):
        raise ValueError(error)
    value = float(raw)
    if not math.isfinite(value) or abs(value) > limit:
        raise ValueError(error)
    return value


def _det3(a: Sequence[float], b: Sequence[float], c: Sequence[float]) -> float:
    return (
        a[0] * (b[1] * c[2] - b[2] * c[1])
        - a[1] * (b[0] * c[2] - b[2] * c[0])
        + a[2] * (b[0] * c[1] - b[1] * c[0])
    )


def resolve_rw_geometry(
    values: Mapping[str, Any],
    default_axes: Sequence[Sequence[float]],
) -> tuple[tuple[tuple[float, float, float], ...],
           tuple[tuple[float, float, float], ...]]:
    """Resolve geometry for the real RW axes/count, guarding physical rank.

    Modes:
      legacy_axis_offset: same historical rWB_B=gsHat_B*rw_mount_radius_m
      per_wheel: independent position/axis; each optional axis triple must be
                 complete; omitted triples preserve original architecture.
    """
    count = len(default_axes)
    if count not in (3, 4):
        raise ValueError("NATIVE_RW_GEOMETRY_WHEEL_COUNT_INVALID")
    mode = values.get("rw_geometry_mode", "legacy_axis_offset")
    if mode not in ("legacy_axis_offset", "per_wheel"):
        raise ValueError("NATIVE_RW_GEOMETRY_MODE_INVALID")
    radius = _number(values.get("rw_mount_radius_m", 0.), limit=.25,
                     error="NATIVE_RW_GEOMETRY_RADIUS_INVALID")
    if radius < 0.:
        raise ValueError("NATIVE_RW_GEOMETRY_RADIUS_INVALID")

    if count == 3 and any(
        values.get(f"rw_{kind}_b_{axis}{suffix}_3") is not None
        for kind, suffix in (("axis", ""), ("mount", "_m"))
        for axis in ("x", "y", "z")
    ):
        raise ValueError("NATIVE_RW4_GEOMETRY_REQUIRES_PYRAMID_4")

    specified = [
        key for key, value in values.items()
        if (key.startswith("rw_axis_b_") or key.startswith("rw_mount_b_"))
        and value is not None
    ]
    if mode == "legacy_axis_offset":
        if specified:
            raise ValueError("NATIVE_RW_GEOMETRY_REQUIRES_PER_WHEEL_MODE")
        axes = tuple(tuple(float(v) for v in axis) for axis in default_axes)
        return axes, tuple(tuple(radius * v for v in axis) for axis in axes)

    if radius != 0.:
        raise ValueError("NATIVE_RW_GEOMETRY_FORBIDS_SHARED_RADIUS")
    if not specified:
        raise ValueError("NATIVE_RW_GEOMETRY_REQUIRES_OVERRIDES")

    axes: list[tuple[float, float, float]] = []
    locations: list[tuple[float, float, float]] = []
    for index in range(count):
        axis_keys = [f"rw_axis_b_{axis}_{index}" for axis in "xyz"]
        supplied = [values.get(key) is not None for key in axis_keys]
        if any(supplied) and not all(supplied):
            raise ValueError("NATIVE_RW_GEOMETRY_AXIS_TRIPLE_REQUIRED")
        if any(supplied):
            axis_values = tuple(_number(values[k], limit=1.,
                                        error="NATIVE_RW_GEOMETRY_AXIS_INVALID")
                                for k in axis_keys)
        else:
            axis_values = tuple(float(v) for v in default_axes[index])
        norm_squared = sum(v * v for v in axis_values)
        if not 0.999 <= norm_squared <= 1.001:
            raise ValueError("NATIVE_RW_GEOMETRY_AXIS_NOT_UNIT")
        axes.append(axis_values)

        location = tuple(_number(
            values.get(f"rw_mount_b_{axis}_m_{index}", 0.),
            limit=.25, error="NATIVE_RW_GEOMETRY_POSITION_INVALID",
        ) for axis in "xyz")
        locations.append(location)

    rank_max = max(
        abs(_det3(axes[i], axes[j], axes[k]))
        for i, j, k in combinations(range(count), 3)
    )
    if rank_max < 0.05:
        raise ValueError("NATIVE_RW_GEOMETRY_AXES_SINGULAR")
    return tuple(axes), tuple(locations)
