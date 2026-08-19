"""HF-1 unit normalization primitives.

The registry is intentionally small and explicit.  It covers the units already
used by the current source-native package plus the physical units needed by the
first high-fidelity foundations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any
import math

HF_UNIT_SCHEMA_VERSION = "hf1.units.v1"

CANONICAL_UNITS: dict[str, str] = {
    "time": "s",
    "distance": "m",
    "mass": "kg",
    "angle": "rad",
    "temperature": "K",
    "power": "W",
    "energy": "J",
    "torque": "N*m",
    "data": "bit",
    "data_rate": "bit/s",
    "pressure": "Pa",
    "angular_rate": "rad/s",
    "velocity": "m/s",
}

# Multiplicative units: canonical = value * scale.
_SCALE_TO_CANONICAL: dict[str, tuple[str, float]] = {
    "s": ("time", 1.0),
    "sec": ("time", 1.0),
    "min": ("time", 60.0),
    "h": ("time", 3600.0),
    "hr": ("time", 3600.0),
    "m": ("distance", 1.0),
    "km": ("distance", 1000.0),
    "cm": ("distance", 0.01),
    "mm": ("distance", 0.001),
    "m/s": ("velocity", 1.0),
    "km/s": ("velocity", 1000.0),
    "kg": ("mass", 1.0),
    "g": ("mass", 0.001),
    "rad": ("angle", 1.0),
    "deg": ("angle", math.pi / 180.0),
    "rad/s": ("angular_rate", 1.0),
    "deg/s": ("angular_rate", math.pi / 180.0),
    "W": ("power", 1.0),
    "kW": ("power", 1000.0),
    "J": ("energy", 1.0),
    "Wh": ("energy", 3600.0),
    "kWh": ("energy", 3_600_000.0),
    "N": ("force", 1.0),
    "N*m": ("torque", 1.0),
    "Nm": ("torque", 1.0),
    "bit": ("data", 1.0),
    "kbit": ("data", 1.0e3),
    "Mbit": ("data", 1.0e6),
    "Gbit": ("data", 1.0e9),
    "byte": ("data", 8.0),
    "B": ("data", 8.0),
    "bit/s": ("data_rate", 1.0),
    "bps": ("data_rate", 1.0),
    "kbit/s": ("data_rate", 1.0e3),
    "kbps": ("data_rate", 1.0e3),
    "Mbit/s": ("data_rate", 1.0e6),
    "Mbps": ("data_rate", 1.0e6),
    "Pa": ("pressure", 1.0),
    "kPa": ("pressure", 1000.0),
}

_TEMPERATURE_UNITS = {"K", "C", "degC"}


class UnitNormalizationError(ValueError):
    """Raised when a unit cannot be normalized or converted."""


def _finite_number(value: Any, name: str = "value") -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise UnitNormalizationError(f"{name} must be a finite number")
    return float(value)


def _temperature_to_kelvin(value: float, unit: str) -> float:
    if unit == "K":
        if value < 0:
            raise UnitNormalizationError("temperature in K must be non-negative")
        return value
    if unit in {"C", "degC"}:
        kelvin = value + 273.15
        if kelvin < 0:
            raise UnitNormalizationError("temperature below absolute zero")
        return kelvin
    raise UnitNormalizationError(f"unsupported temperature unit {unit!r}")


def _kelvin_to_unit(value_k: float, unit: str) -> float:
    if unit == "K":
        return value_k
    if unit in {"C", "degC"}:
        return value_k - 273.15
    raise UnitNormalizationError(f"unsupported temperature unit {unit!r}")


def _canonical_quantity(unit: str) -> str:
    if unit in _TEMPERATURE_UNITS:
        return "temperature"
    if unit not in _SCALE_TO_CANONICAL:
        raise UnitNormalizationError(f"unknown unit {unit!r}")
    return _SCALE_TO_CANONICAL[unit][0]


def normalize_unit_value(value: float, unit: str, *, quantity: str | None = None) -> "UnitValue":
    """Normalize ``value`` to the canonical unit for its quantity."""

    val = _finite_number(value)
    u = str(unit or "").strip()
    if not u:
        raise UnitNormalizationError("unit is required")
    inferred_quantity = _canonical_quantity(u)
    if quantity and quantity != inferred_quantity:
        raise UnitNormalizationError(f"unit {u!r} belongs to {inferred_quantity!r}, not requested quantity {quantity!r}")
    if inferred_quantity == "temperature":
        canonical_value = _temperature_to_kelvin(val, u)
        canonical_unit = "K"
    else:
        _, scale = _SCALE_TO_CANONICAL[u]
        canonical_value = val * scale
        canonical_unit = CANONICAL_UNITS.get(inferred_quantity, u)
    return UnitValue(
        value=canonical_value,
        unit=canonical_unit,
        quantity=inferred_quantity,
        source_value=val,
        source_unit=u,
    )


def convert_unit(value: float, from_unit: str, to_unit: str, *, quantity: str | None = None) -> float:
    """Convert a scalar value between supported units."""

    normalized = normalize_unit_value(value, from_unit, quantity=quantity)
    target = str(to_unit or "").strip()
    if not target:
        raise UnitNormalizationError("to_unit is required")
    target_quantity = _canonical_quantity(target)
    if target_quantity != normalized.quantity:
        raise UnitNormalizationError(f"cannot convert {normalized.quantity!r} to {target_quantity!r}")
    if normalized.quantity == "temperature":
        return _kelvin_to_unit(normalized.value, target)
    _, target_scale = _SCALE_TO_CANONICAL[target]
    return normalized.value / target_scale


@dataclass(frozen=True)
class UnitValue:
    """A scalar unit conversion record."""

    value: float
    unit: str
    quantity: str
    source_value: float
    source_unit: str
    schema_version: str = HF_UNIT_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "quantity": self.quantity,
            "value": self.value,
            "unit": self.unit,
            "source_value": self.source_value,
            "source_unit": self.source_unit,
            "canonical_unit": self.unit,
        }


def normalize_unit_contract() -> dict[str, Any]:
    """Return HF-1 unit registry metadata for manifests/readiness reports."""

    return {
        "schema_version": HF_UNIT_SCHEMA_VERSION,
        "contract_status": "implemented_hf1",
        "canonical_units": dict(CANONICAL_UNITS),
        "accepted_input_units": sorted(set(_SCALE_TO_CANONICAL) | _TEMPERATURE_UNITS),
        "normalization_policy": "normalize_at_task_boundary_and_record_source_unit",
        "strict_unknown_unit_policy": "reject_unknown_unit_in_high_fidelity_mode",
        "round_trip_evidence": {
            "distance_angle_energy_temperature": "implemented",
            "unknown_unit_rejection": "implemented",
        },
    }


__all__ = [
    "HF_UNIT_SCHEMA_VERSION",
    "CANONICAL_UNITS",
    "UnitNormalizationError",
    "UnitValue",
    "convert_unit",
    "normalize_unit_contract",
    "normalize_unit_value",
]
