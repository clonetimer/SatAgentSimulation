"""HF-2 reference frame metadata and deterministic vector transforms.

This is a foundation layer.  It implements enough ECI/ECEF/LVLH math to support
round-trip tests and future orbit/environment modules, while BODY frame support
is deliberately metadata-first until ADCS HF-4 introduces attitude-driven body
transforms.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence
import math

from sat_sim.time_systems import J2000_UTC, parse_utc

HF_FRAME_SCHEMA_VERSION = "hf2.frames.v1"
ECI = "ECI"
ECEF = "ECEF"
LVLH = "LVLH"
BODY = "BODY"
SUPPORTED_FRAMES = (ECI, ECEF, LVLH, BODY)
EARTH_ROTATION_RAD_S = 7.2921150e-5


class FrameError(ValueError):
    """Raised when a frame transform or metadata value is invalid."""


def _vec3(value: Iterable[float], name: str = "vector") -> tuple[float, float, float]:
    items = tuple(float(x) for x in value)
    if len(items) != 3 or any(not math.isfinite(x) for x in items):
        raise FrameError(f"{name} must contain exactly three finite numbers")
    return items  # type: ignore[return-value]


def dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


def cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (
        float(a[1] * b[2] - a[2] * b[1]),
        float(a[2] * b[0] - a[0] * b[2]),
        float(a[0] * b[1] - a[1] * b[0]),
    )


def vector_norm(v: Sequence[float]) -> float:
    return math.sqrt(dot(v, v))


def normalize_vector(v: Sequence[float], name: str = "vector") -> tuple[float, float, float]:
    vec = _vec3(v, name)
    norm = vector_norm(vec)
    if norm <= 0:
        raise FrameError(f"{name} must be non-zero")
    return (vec[0] / norm, vec[1] / norm, vec[2] / norm)


def _rotate_z(v: Sequence[float], theta_rad: float) -> tuple[float, float, float]:
    c = math.cos(theta_rad)
    s = math.sin(theta_rad)
    return (c * v[0] - s * v[1], s * v[0] + c * v[1], float(v[2]))


def _elapsed_seconds(epoch_utc: str | None, target_utc: str | None) -> float:
    epoch = parse_utc(epoch_utc or J2000_UTC)
    target = parse_utc(target_utc or epoch)
    return (target - epoch).total_seconds()


def _earth_rotation_angle(epoch_utc: str | None = None, target_utc: str | None = None, theta_rad: float | None = None) -> float:
    if theta_rad is not None:
        if isinstance(theta_rad, bool) or not isinstance(theta_rad, (int, float)) or not math.isfinite(float(theta_rad)):
            raise FrameError("theta_rad must be finite")
        return float(theta_rad)
    return EARTH_ROTATION_RAD_S * _elapsed_seconds(epoch_utc, target_utc)


def eci_to_ecef(
    vector_eci: Iterable[float],
    *,
    epoch_utc: str | None = None,
    target_utc: str | None = None,
    theta_rad: float | None = None,
) -> tuple[float, float, float]:
    """Rotate a vector from ECI to ECEF using a deterministic Earth-rate model."""

    v = _vec3(vector_eci, "vector_eci")
    theta = _earth_rotation_angle(epoch_utc=epoch_utc, target_utc=target_utc, theta_rad=theta_rad)
    # ECEF axes rotate eastward relative to inertial axes; use -theta for ECI->ECEF.
    return _rotate_z(v, -theta)


def ecef_to_eci(
    vector_ecef: Iterable[float],
    *,
    epoch_utc: str | None = None,
    target_utc: str | None = None,
    theta_rad: float | None = None,
) -> tuple[float, float, float]:
    """Rotate a vector from ECEF to ECI using the inverse transform."""

    v = _vec3(vector_ecef, "vector_ecef")
    theta = _earth_rotation_angle(epoch_utc=epoch_utc, target_utc=target_utc, theta_rad=theta_rad)
    return _rotate_z(v, theta)


def build_lvlh_basis(position_eci_m: Iterable[float], velocity_eci_m_s: Iterable[float]) -> dict[str, tuple[float, float, float]]:
    """Build an LVLH basis expressed in ECI coordinates.

    The convention here uses x along the radial direction, z along orbit angular
    momentum, and y = z x x.  The convention is recorded explicitly in metadata
    so later models do not silently mix LVLH definitions.
    """

    r_hat = normalize_vector(position_eci_m, "position_eci_m")
    v = _vec3(velocity_eci_m_s, "velocity_eci_m_s")
    h = cross(r_hat, v)
    z_hat = normalize_vector(h, "orbit_angular_momentum")
    y_hat = normalize_vector(cross(z_hat, r_hat), "lvlh_y")
    # Recompute z to reduce numerical skew after normalizing y.
    z_hat = normalize_vector(cross(r_hat, y_hat), "lvlh_z")
    return {"x_radial": r_hat, "y_along_track": y_hat, "z_cross_track": z_hat}


def eci_vector_to_lvlh(
    vector_eci: Iterable[float],
    *,
    position_eci_m: Iterable[float],
    velocity_eci_m_s: Iterable[float],
) -> tuple[float, float, float]:
    """Express an ECI vector in the local LVLH basis."""

    v = _vec3(vector_eci, "vector_eci")
    basis = build_lvlh_basis(position_eci_m, velocity_eci_m_s)
    return (
        dot(v, basis["x_radial"]),
        dot(v, basis["y_along_track"]),
        dot(v, basis["z_cross_track"]),
    )


def lvlh_vector_to_eci(
    vector_lvlh: Iterable[float],
    *,
    position_eci_m: Iterable[float],
    velocity_eci_m_s: Iterable[float],
) -> tuple[float, float, float]:
    """Express an LVLH vector in ECI coordinates."""

    v = _vec3(vector_lvlh, "vector_lvlh")
    basis = build_lvlh_basis(position_eci_m, velocity_eci_m_s)
    x = basis["x_radial"]
    y = basis["y_along_track"]
    z = basis["z_cross_track"]
    return (
        v[0] * x[0] + v[1] * y[0] + v[2] * z[0],
        v[0] * x[1] + v[1] * y[1] + v[2] * z[1],
        v[0] * x[2] + v[1] * y[2] + v[2] * z[2],
    )


@dataclass(frozen=True)
class FrameMetadata:
    """Serializable state-vector frame metadata."""

    frame_id: str
    frame_origin: str = "Earth center"
    frame_orientation: str = "inertial axes"
    epoch_utc: str = J2000_UTC
    units: dict[str, str] = field(default_factory=lambda: {"position": "m", "velocity": "m/s", "angle": "rad"})
    parent_frame: str | None = None
    transform_status: str = "implemented_for_ECI_ECEF_LVLH_vectors; BODY_metadata_only"
    schema_version: str = HF_FRAME_SCHEMA_VERSION

    def __post_init__(self) -> None:
        frame = str(self.frame_id).upper()
        if frame not in SUPPORTED_FRAMES:
            raise FrameError(f"unsupported frame_id {self.frame_id!r}")
        parse_utc(self.epoch_utc)
        object.__setattr__(self, "frame_id", frame)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "frame_id": self.frame_id,
            "frame_origin": self.frame_origin,
            "frame_orientation": self.frame_orientation,
            "epoch_utc": self.epoch_utc,
            "units": dict(self.units),
            "parent_frame": self.parent_frame,
            "transform_status": self.transform_status,
        }


@dataclass(frozen=True)
class FrameTransformRecord:
    """Serializable provenance record for a frame transform."""

    source_frame: str
    target_frame: str
    epoch_utc: str
    method: str
    parameters: dict[str, Any] = field(default_factory=dict)
    schema_version: str = HF_FRAME_SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "source_frame": self.source_frame,
            "target_frame": self.target_frame,
            "epoch_utc": self.epoch_utc,
            "method": self.method,
            "parameters": dict(self.parameters),
        }


def build_frame_contract() -> dict[str, Any]:
    """Return HF-2 frame contract metadata for manifests/readiness reports."""

    return {
        "schema_version": HF_FRAME_SCHEMA_VERSION,
        "contract_status": "implemented_hf2_foundation",
        "supported_frames": list(SUPPORTED_FRAMES),
        "implemented_transforms": ["ECI<->ECEF vector rotation", "ECI<->LVLH vector projection"],
        "metadata_only_frames": [BODY],
        "default_inertial_frame": ECI,
        "default_body_frame": BODY,
        "lvlh_convention": "x_radial_y_along_track_z_cross_track",
        "required_state_metadata": ["frame_id", "frame_origin", "frame_orientation", "epoch_utc", "units"],
        "known_limits": [
            "ECEF transform uses deterministic Earth-rate rotation, not full IAU precession/nutation/polar motion.",
            "BODY frame transforms require HF-4 attitude state/controller implementation.",
        ],
    }


__all__ = [
    "HF_FRAME_SCHEMA_VERSION",
    "ECI",
    "ECEF",
    "LVLH",
    "BODY",
    "SUPPORTED_FRAMES",
    "EARTH_ROTATION_RAD_S",
    "FrameError",
    "FrameMetadata",
    "FrameTransformRecord",
    "build_frame_contract",
    "build_lvlh_basis",
    "cross",
    "dot",
    "ecef_to_eci",
    "eci_to_ecef",
    "eci_vector_to_lvlh",
    "lvlh_vector_to_eci",
    "normalize_vector",
    "vector_norm",
]
