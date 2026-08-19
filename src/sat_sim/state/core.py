"""HF-2 serializable spacecraft state containers."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
import math

from sat_sim.frames import ECI, BODY, FrameMetadata, vector_norm
from sat_sim.time_systems import J2000_UTC, parse_utc
from sat_sim.units import convert_unit

HF_STATE_SCHEMA_VERSION = "hf2.state.v1"


class StateError(ValueError):
    """Raised when a spacecraft state vector is invalid."""


def _vec3(value: Iterable[float], name: str) -> tuple[float, float, float]:
    items = tuple(float(x) for x in value)
    if len(items) != 3 or any(not math.isfinite(x) for x in items):
        raise StateError(f"{name} must contain exactly three finite numbers")
    return items  # type: ignore[return-value]


def quaternion_norm(q: Sequence[float]) -> float:
    if len(q) != 4:
        raise StateError("quaternion must contain four values")
    values = tuple(float(x) for x in q)
    if any(not math.isfinite(x) for x in values):
        raise StateError("quaternion values must be finite")
    return math.sqrt(sum(x * x for x in values))


def normalize_quaternion(q: Iterable[float]) -> tuple[float, float, float, float]:
    values = tuple(float(x) for x in q)
    norm = quaternion_norm(values)
    if norm <= 0:
        raise StateError("quaternion norm must be positive")
    normalized = tuple(x / norm for x in values)
    return normalized  # type: ignore[return-value]


@dataclass(frozen=True)
class CartesianState:
    """Position/velocity state vector with frame and unit metadata."""

    position_m: tuple[float, float, float]
    velocity_m_s: tuple[float, float, float]
    frame: FrameMetadata = field(default_factory=lambda: FrameMetadata(frame_id=ECI))
    epoch_utc: str = J2000_UTC
    schema_version: str = HF_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "position_m", _vec3(self.position_m, "position_m"))
        object.__setattr__(self, "velocity_m_s", _vec3(self.velocity_m_s, "velocity_m_s"))
        parse_utc(self.epoch_utc)

    @classmethod
    def from_units(
        cls,
        *,
        position: Iterable[float],
        position_unit: str,
        velocity: Iterable[float],
        velocity_unit: str,
        frame: FrameMetadata | None = None,
        epoch_utc: str = J2000_UTC,
    ) -> "CartesianState":
        pos_m = tuple(convert_unit(x, position_unit, "m", quantity="distance") for x in position)
        vel_m_s = tuple(convert_unit(x, velocity_unit, "m/s", quantity="velocity") for x in velocity)
        return cls(position_m=pos_m, velocity_m_s=vel_m_s, frame=frame or FrameMetadata(frame_id=ECI, epoch_utc=epoch_utc), epoch_utc=epoch_utc)

    @property
    def radius_m(self) -> float:
        return vector_norm(self.position_m)

    @property
    def speed_m_s(self) -> float:
        return vector_norm(self.velocity_m_s)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "epoch_utc": self.epoch_utc,
            "frame": self.frame.to_dict(),
            "position_m": list(self.position_m),
            "velocity_m_s": list(self.velocity_m_s),
            "radius_m": self.radius_m,
            "speed_m_s": self.speed_m_s,
            "units": {"position": "m", "velocity": "m/s"},
        }


@dataclass(frozen=True)
class AttitudeState:
    """Quaternion attitude and angular-rate state."""

    quaternion_body_to_ref: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    angular_rate_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    reference_frame_id: str = ECI
    body_frame_id: str = BODY
    epoch_utc: str = J2000_UTC
    schema_version: str = HF_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "quaternion_body_to_ref", normalize_quaternion(self.quaternion_body_to_ref))
        object.__setattr__(self, "angular_rate_rad_s", _vec3(self.angular_rate_rad_s, "angular_rate_rad_s"))
        parse_utc(self.epoch_utc)

    @classmethod
    def from_units(
        cls,
        *,
        quaternion_body_to_ref: Iterable[float] = (1.0, 0.0, 0.0, 0.0),
        angular_rate: Iterable[float] = (0.0, 0.0, 0.0),
        angular_rate_unit: str = "rad/s",
        reference_frame_id: str = ECI,
        body_frame_id: str = BODY,
        epoch_utc: str = J2000_UTC,
    ) -> "AttitudeState":
        rate = tuple(convert_unit(x, angular_rate_unit, "rad/s", quantity="angular_rate") for x in angular_rate)
        return cls(
            quaternion_body_to_ref=tuple(float(x) for x in quaternion_body_to_ref),  # type: ignore[arg-type]
            angular_rate_rad_s=rate,  # type: ignore[arg-type]
            reference_frame_id=reference_frame_id,
            body_frame_id=body_frame_id,
            epoch_utc=epoch_utc,
        )

    @property
    def quaternion_norm(self) -> float:
        return quaternion_norm(self.quaternion_body_to_ref)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "epoch_utc": self.epoch_utc,
            "reference_frame_id": self.reference_frame_id,
            "body_frame_id": self.body_frame_id,
            "quaternion_body_to_ref": list(self.quaternion_body_to_ref),
            "quaternion_norm": self.quaternion_norm,
            "angular_rate_rad_s": list(self.angular_rate_rad_s),
            "units": {"attitude": "unit_quaternion", "angular_rate": "rad/s"},
        }


@dataclass(frozen=True)
class SpacecraftState:
    """Combined spacecraft translational/attitude state container."""

    cartesian: CartesianState
    attitude: AttitudeState = field(default_factory=AttitudeState)
    mass_kg: float | None = None
    schema_version: str = HF_STATE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.mass_kg is not None:
            if isinstance(self.mass_kg, bool) or not isinstance(self.mass_kg, (int, float)) or not math.isfinite(float(self.mass_kg)) or float(self.mass_kg) <= 0:
                raise StateError("mass_kg must be positive when provided")
            object.__setattr__(self, "mass_kg", float(self.mass_kg))

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "cartesian": self.cartesian.to_dict(),
            "attitude": self.attitude.to_dict(),
            "mass_kg": self.mass_kg,
        }


def build_state_contract() -> dict[str, Any]:
    """Return HF-2 state-vector metadata contract."""

    return {
        "schema_version": HF_STATE_SCHEMA_VERSION,
        "contract_status": "implemented_hf2_foundation",
        "state_containers": ["CartesianState", "AttitudeState", "SpacecraftState"],
        "required_fields": {
            "cartesian": ["position_m", "velocity_m_s", "frame", "epoch_utc"],
            "attitude": ["quaternion_body_to_ref", "angular_rate_rad_s", "reference_frame_id", "body_frame_id", "epoch_utc"],
        },
        "unit_policy": {
            "position": "m",
            "velocity": "m/s",
            "quaternion": "unit_norm",
            "angular_rate": "rad/s",
            "mass": "kg",
        },
        "validation_evidence": {
            "finite_vector_checks": "implemented",
            "quaternion_normalization": "implemented",
            "frame_metadata_required": "implemented",
        },
    }


__all__ = [
    "HF_STATE_SCHEMA_VERSION",
    "AttitudeState",
    "CartesianState",
    "SpacecraftState",
    "StateError",
    "build_state_contract",
    "normalize_quaternion",
    "quaternion_norm",
]
