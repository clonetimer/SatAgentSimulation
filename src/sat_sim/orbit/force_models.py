"""ORB-1 orbit force-model primitives.

This module extends the HF-3 analytic/secular orbit prototype with a local,
deterministic numerical force-model interface.  The scope is intentionally
bounded: central gravity, J2 acceleration, exponential drag, and a compact SRP
proxy are implemented.  Third-body gravity, SPICE/truth ephemerides, and
calibrated flight-grade force tolerances remain outside the supported boundary.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping
import math

from sat_sim.frames import cross, dot, normalize_vector, vector_norm
from sat_sim.orbit.medium import EARTH_J2, EARTH_MU_M3_S2, EARTH_RADIUS_M, analytic_sun_vector_eci, eclipse_shadow_factor

ORB1_FORCE_MODEL_SCHEMA_VERSION = "orb1.orbit_force_models.v1"
EARTH_ROTATION_RAD_S = 7.2921150e-5
SOLAR_RADIATION_PRESSURE_N_M2 = 4.56e-6


class OrbitForceModelError(ValueError):
    """Raised when an ORB-1 force-model input is invalid."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise OrbitForceModelError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise OrbitForceModelError(f"{name} must be positive")
    return out


def _nonnegative(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out < 0.0:
        raise OrbitForceModelError(f"{name} must be non-negative")
    return out


def _vec3(value: Iterable[float], name: str) -> tuple[float, float, float]:
    items = tuple(float(x) for x in value)
    if len(items) != 3 or any(not math.isfinite(x) for x in items):
        raise OrbitForceModelError(f"{name} must contain exactly three finite numbers")
    return items  # type: ignore[return-value]


def _add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scale(v: tuple[float, float, float], s: float) -> tuple[float, float, float]:
    return (v[0] * s, v[1] * s, v[2] * s)


def _zeros() -> tuple[float, float, float]:
    return (0.0, 0.0, 0.0)


@dataclass(frozen=True)
class AtmosphereConfig:
    """Exponential atmosphere proxy for LEO drag tests."""

    enabled: bool = True
    reference_altitude_m: float = 400_000.0
    reference_density_kg_m3: float = 4.0e-12
    scale_height_m: float = 60_000.0
    min_density_kg_m3: float = 0.0
    max_density_kg_m3: float = 1.0e-8

    def __post_init__(self) -> None:
        object.__setattr__(self, "reference_altitude_m", _finite(self.reference_altitude_m, "atmosphere.reference_altitude_m"))
        object.__setattr__(self, "reference_density_kg_m3", _nonnegative(self.reference_density_kg_m3, "atmosphere.reference_density_kg_m3"))
        object.__setattr__(self, "scale_height_m", _positive(self.scale_height_m, "atmosphere.scale_height_m"))
        object.__setattr__(self, "min_density_kg_m3", _nonnegative(self.min_density_kg_m3, "atmosphere.min_density_kg_m3"))
        object.__setattr__(self, "max_density_kg_m3", _positive(self.max_density_kg_m3, "atmosphere.max_density_kg_m3"))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "AtmosphereConfig":
        data = payload if isinstance(payload, Mapping) else {}
        return cls(
            enabled=bool(data.get("enabled", True)),
            reference_altitude_m=float(data.get("reference_altitude_m", 400_000.0)),
            reference_density_kg_m3=float(data.get("reference_density_kg_m3", 4.0e-12)),
            scale_height_m=float(data.get("scale_height_m", 60_000.0)),
            min_density_kg_m3=float(data.get("min_density_kg_m3", 0.0)),
            max_density_kg_m3=float(data.get("max_density_kg_m3", 1.0e-8)),
        )

    def density_kg_m3(self, altitude_m: float) -> float:
        if not self.enabled:
            return 0.0
        alt = _finite(altitude_m, "altitude_m")
        rho = self.reference_density_kg_m3 * math.exp(-(alt - self.reference_altitude_m) / self.scale_height_m)
        return max(self.min_density_kg_m3, min(self.max_density_kg_m3, rho))

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "reference_altitude_m": self.reference_altitude_m,
            "reference_density_kg_m3": self.reference_density_kg_m3,
            "scale_height_m": self.scale_height_m,
            "min_density_kg_m3": self.min_density_kg_m3,
            "max_density_kg_m3": self.max_density_kg_m3,
        }


@dataclass(frozen=True)
class SpacecraftDragSrpProperties:
    """Compact spacecraft properties used by drag and SRP proxies."""

    mass_kg: float = 120.0
    drag_area_m2: float = 1.2
    drag_coefficient: float = 2.2
    srp_area_m2: float = 1.0
    reflectivity_coefficient: float = 1.3

    def __post_init__(self) -> None:
        object.__setattr__(self, "mass_kg", _positive(self.mass_kg, "spacecraft.mass_kg"))
        object.__setattr__(self, "drag_area_m2", _nonnegative(self.drag_area_m2, "spacecraft.drag_area_m2"))
        object.__setattr__(self, "drag_coefficient", _nonnegative(self.drag_coefficient, "spacecraft.drag_coefficient"))
        object.__setattr__(self, "srp_area_m2", _nonnegative(self.srp_area_m2, "spacecraft.srp_area_m2"))
        object.__setattr__(self, "reflectivity_coefficient", _nonnegative(self.reflectivity_coefficient, "spacecraft.reflectivity_coefficient"))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "SpacecraftDragSrpProperties":
        data = payload if isinstance(payload, Mapping) else {}
        return cls(
            mass_kg=float(data.get("mass_kg", data.get("dry_mass_kg", 120.0))),
            drag_area_m2=float(data.get("drag_area_m2", data.get("area_m2", 1.2))),
            drag_coefficient=float(data.get("drag_coefficient", data.get("cd", 2.2))),
            srp_area_m2=float(data.get("srp_area_m2", data.get("area_m2", 1.0))),
            reflectivity_coefficient=float(data.get("reflectivity_coefficient", data.get("cr", 1.3))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mass_kg": self.mass_kg,
            "drag_area_m2": self.drag_area_m2,
            "drag_coefficient": self.drag_coefficient,
            "srp_area_m2": self.srp_area_m2,
            "reflectivity_coefficient": self.reflectivity_coefficient,
        }


@dataclass(frozen=True)
class OrbitForceModelConfig:
    """ORB-1 force-model configuration."""

    earth_mu_m3_s2: float = EARTH_MU_M3_S2
    earth_radius_m: float = EARTH_RADIUS_M
    earth_j2: float = EARTH_J2
    earth_rotation_rad_s: float = EARTH_ROTATION_RAD_S
    enable_j2: bool = True
    enable_drag: bool = True
    enable_srp: bool = False
    atmosphere: AtmosphereConfig = field(default_factory=AtmosphereConfig)
    spacecraft: SpacecraftDragSrpProperties = field(default_factory=SpacecraftDragSrpProperties)
    solar_pressure_n_m2: float = SOLAR_RADIATION_PRESSURE_N_M2

    def __post_init__(self) -> None:
        object.__setattr__(self, "earth_mu_m3_s2", _positive(self.earth_mu_m3_s2, "earth_mu_m3_s2"))
        object.__setattr__(self, "earth_radius_m", _positive(self.earth_radius_m, "earth_radius_m"))
        object.__setattr__(self, "earth_j2", _nonnegative(self.earth_j2, "earth_j2"))
        object.__setattr__(self, "earth_rotation_rad_s", _finite(self.earth_rotation_rad_s, "earth_rotation_rad_s"))
        object.__setattr__(self, "solar_pressure_n_m2", _nonnegative(self.solar_pressure_n_m2, "solar_pressure_n_m2"))

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "OrbitForceModelConfig":
        orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        force = orbit.get("force_models") if isinstance(orbit.get("force_models"), Mapping) else params.get("force_models") if isinstance(params.get("force_models"), Mapping) else {}
        atmosphere = orbit.get("atmosphere") if isinstance(orbit.get("atmosphere"), Mapping) else params.get("atmosphere") if isinstance(params.get("atmosphere"), Mapping) else {}
        spacecraft = params.get("spacecraft") if isinstance(params.get("spacecraft"), Mapping) else params.get("spacecraft_properties") if isinstance(params.get("spacecraft_properties"), Mapping) else {}
        j2_value = orbit.get("j2", params.get("j2", EARTH_J2))
        if isinstance(j2_value, bool):
            j2_value = EARTH_J2
        return cls(
            earth_mu_m3_s2=float(orbit.get("earth_mu_m3_s2", params.get("earth_mu_m3_s2", EARTH_MU_M3_S2))),
            earth_radius_m=float(orbit.get("earth_radius_m", params.get("earth_radius_m", EARTH_RADIUS_M))),
            earth_j2=float(j2_value),
            earth_rotation_rad_s=float(orbit.get("earth_rotation_rad_s", force.get("earth_rotation_rad_s", EARTH_ROTATION_RAD_S))),
            enable_j2=bool(force.get("j2", orbit.get("use_j2_gravity", True))),
            enable_drag=bool(force.get("drag", True)),
            enable_srp=bool(force.get("srp", False)),
            atmosphere=AtmosphereConfig.from_mapping(atmosphere),
            spacecraft=SpacecraftDragSrpProperties.from_mapping(spacecraft),
            solar_pressure_n_m2=float(force.get("solar_pressure_n_m2", SOLAR_RADIATION_PRESSURE_N_M2)),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORB1_FORCE_MODEL_SCHEMA_VERSION,
            "earth_mu_m3_s2": self.earth_mu_m3_s2,
            "earth_radius_m": self.earth_radius_m,
            "earth_j2": self.earth_j2,
            "earth_rotation_rad_s": self.earth_rotation_rad_s,
            "enabled_models": {
                "central_gravity": True,
                "j2_acceleration": self.enable_j2,
                "exponential_drag": self.enable_drag,
                "srp_proxy": self.enable_srp,
                "third_body": False,
                "spice_ephemeris": False,
            },
            "atmosphere": self.atmosphere.to_dict(),
            "spacecraft": self.spacecraft.to_dict(),
            "solar_pressure_n_m2": self.solar_pressure_n_m2,
            "can_claim_flight_grade": False,
        }


def central_gravity_accel_m_s2(position_m: tuple[float, float, float], mu: float = EARTH_MU_M3_S2) -> tuple[float, float, float]:
    r = _vec3(position_m, "position_m")
    rn = vector_norm(r)
    if rn <= 0:
        raise OrbitForceModelError("position norm must be positive")
    return _scale(r, -mu / (rn**3))


def j2_accel_m_s2(position_m: tuple[float, float, float], *, mu: float = EARTH_MU_M3_S2, radius_m: float = EARTH_RADIUS_M, j2: float = EARTH_J2) -> tuple[float, float, float]:
    r = _vec3(position_m, "position_m")
    x, y, z = r
    rn = vector_norm(r)
    if rn <= 0:
        raise OrbitForceModelError("position norm must be positive")
    z2_r2 = (z / rn) ** 2
    factor = 1.5 * j2 * mu * radius_m**2 / rn**5
    common_xy = 5.0 * z2_r2 - 1.0
    common_z = 5.0 * z2_r2 - 3.0
    return (factor * x * common_xy, factor * y * common_xy, factor * z * common_z)


def relative_atmospheric_velocity_m_s(position_m: tuple[float, float, float], velocity_m_s: tuple[float, float, float], earth_rotation_rad_s: float = EARTH_ROTATION_RAD_S) -> tuple[float, float, float]:
    omega_cross_r = cross((0.0, 0.0, earth_rotation_rad_s), position_m)
    return _sub(_vec3(velocity_m_s, "velocity_m_s"), omega_cross_r)


def drag_accel_m_s2(position_m: tuple[float, float, float], velocity_m_s: tuple[float, float, float], *, config: OrbitForceModelConfig) -> tuple[float, float, float]:
    if not config.enable_drag:
        return _zeros()
    altitude_m = vector_norm(position_m) - config.earth_radius_m
    rho = config.atmosphere.density_kg_m3(altitude_m)
    if rho <= 0.0 or config.spacecraft.drag_area_m2 <= 0.0 or config.spacecraft.drag_coefficient <= 0.0:
        return _zeros()
    v_rel = relative_atmospheric_velocity_m_s(position_m, velocity_m_s, config.earth_rotation_rad_s)
    v_rel_norm = vector_norm(v_rel)
    if v_rel_norm <= 0.0:
        return _zeros()
    coeff = -0.5 * rho * config.spacecraft.drag_coefficient * config.spacecraft.drag_area_m2 / config.spacecraft.mass_kg * v_rel_norm
    return _scale(v_rel, coeff)


def srp_accel_m_s2(position_m: tuple[float, float, float], sun_vector_eci: tuple[float, float, float], shadow_factor: float, *, config: OrbitForceModelConfig) -> tuple[float, float, float]:
    if not config.enable_srp:
        return _zeros()
    if config.spacecraft.srp_area_m2 <= 0.0 or config.spacecraft.reflectivity_coefficient <= 0.0:
        return _zeros()
    sun = normalize_vector(sun_vector_eci, "sun_vector_eci")
    scale = max(0.0, min(1.0, float(shadow_factor))) * config.solar_pressure_n_m2 * config.spacecraft.reflectivity_coefficient * config.spacecraft.srp_area_m2 / config.spacecraft.mass_kg
    # The compact proxy accelerates along the Sun-line direction recorded by the
    # environment model.  It is deterministic and benchmarked, not a full
    # spacecraft optical model.
    return _scale(sun, scale)


def force_contributions_m_s2(
    *,
    position_m: tuple[float, float, float],
    velocity_m_s: tuple[float, float, float],
    elapsed_s: float,
    epoch_utc: str,
    config: OrbitForceModelConfig,
    sun_vector_eci: tuple[float, float, float] | None = None,
) -> dict[str, tuple[float, float, float]]:
    r = _vec3(position_m, "position_m")
    v = _vec3(velocity_m_s, "velocity_m_s")
    sun = sun_vector_eci if sun_vector_eci is not None else analytic_sun_vector_eci(epoch_utc, elapsed_s)
    shadow, _, _, _ = eclipse_shadow_factor(r, sun, earth_radius_m=config.earth_radius_m, enabled=True)
    central = central_gravity_accel_m_s2(r, config.earth_mu_m3_s2)
    j2 = j2_accel_m_s2(r, mu=config.earth_mu_m3_s2, radius_m=config.earth_radius_m, j2=config.earth_j2) if config.enable_j2 else _zeros()
    drag = drag_accel_m_s2(r, v, config=config)
    srp = srp_accel_m_s2(r, sun, shadow, config=config)
    total = _add(_add(central, j2), _add(drag, srp))
    return {"central_gravity": central, "j2": j2, "drag": drag, "srp": srp, "total": total}


def force_norms(contrib: Mapping[str, tuple[float, float, float]]) -> dict[str, float]:
    return {f"{key}_norm_m_s2": vector_norm(value) for key, value in contrib.items()}


def build_orb1_force_model_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        cfg = OrbitForceModelConfig.from_task_spec(spec) if spec else OrbitForceModelConfig()
        status = "implemented_orbit_fidelity_force_models_not_flight_grade"
        validation_status = "config_valid"
        config_payload = cfg.to_dict()
    except Exception as exc:
        status = "configured_with_validation_error"
        validation_status = "config_invalid"
        config_payload = {"error": str(exc)}
    return {
        "schema_version": ORB1_FORCE_MODEL_SCHEMA_VERSION,
        "route_version": "ORB-1",
        "status": status,
        "validation_status": validation_status,
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "implemented_force_models": [
            "central_gravity_acceleration",
            "j2_acceleration",
            "exponential_drag_proxy",
            "srp_proxy_optional",
        ],
        "unsupported_force_models": [
            "third_body_gravity",
            "finite_gravity_field",
            "SPICE_or_truth_ephemeris",
            "flight_data_calibrated_force_model_tolerances",
        ],
        "config": config_payload,
    }


__all__ = [
    "ORB1_FORCE_MODEL_SCHEMA_VERSION",
    "AtmosphereConfig",
    "SpacecraftDragSrpProperties",
    "OrbitForceModelConfig",
    "OrbitForceModelError",
    "central_gravity_accel_m_s2",
    "j2_accel_m_s2",
    "drag_accel_m_s2",
    "srp_accel_m_s2",
    "force_contributions_m_s2",
    "force_norms",
    "relative_atmospheric_velocity_m_s",
    "build_orb1_force_model_payload",
]
