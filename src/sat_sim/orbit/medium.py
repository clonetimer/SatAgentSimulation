"""HF-3 medium-fidelity orbit/environment primitives.

This module is deliberately self-contained and deterministic.  It builds on
HF-1/HF-2 time, frame, unit, state, and solver foundations, but it does not
claim full engineering high fidelity.  The model envelope is medium-fidelity:
Keplerian two-body motion with optional J2 secular precession, analytic Sun
vector, cylindrical/linear-band eclipse approximation, dipole magnetic-field
proxy, and geometric ground-station access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable, Mapping
import math

from sat_sim.frames import ECI, FrameMetadata, cross, eci_to_ecef, normalize_vector, vector_norm
from sat_sim.solvers import FixedStepSolverConfig
from sat_sim.state import CartesianState
from sat_sim.time_systems import TimeGrid, build_time_grid, datetime_to_julian_date, parse_utc

HF3_ORBIT_ENVIRONMENT_SCHEMA_VERSION = "hf3.orbit_environment.medium.v1"
EARTH_RADIUS_M = 6_378_137.0
EARTH_MU_M3_S2 = 3.986004418e14
EARTH_J2 = 1.08262668e-3
DEFAULT_EPOCH_UTC = "2026-07-05T00:00:00Z"


class OrbitEnvironmentMediumError(ValueError):
    """Raised when HF-3 orbit/environment inputs are invalid."""


def _finite_number(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise OrbitEnvironmentMediumError(f"{name} must be a finite number")
    return float(value)


def _positive_number(value: Any, name: str) -> float:
    val = _finite_number(value, name)
    if val <= 0:
        raise OrbitEnvironmentMediumError(f"{name} must be positive")
    return val


def _deg_to_rad(value: float) -> float:
    return math.radians(float(value))


def _rad_to_deg(value: float) -> float:
    return math.degrees(float(value))


def _wrap_rad(value: float) -> float:
    return float(value % (2.0 * math.pi))


def _vec3(value: Iterable[float], name: str) -> tuple[float, float, float]:
    items = tuple(float(x) for x in value)
    if len(items) != 3 or any(not math.isfinite(x) for x in items):
        raise OrbitEnvironmentMediumError(f"{name} must contain exactly three finite numbers")
    return items  # type: ignore[return-value]


def _dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return float(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])


def _sub(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _scalar(v: tuple[float, float, float], scale: float) -> tuple[float, float, float]:
    return (scale * v[0], scale * v[1], scale * v[2])


def _add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


@dataclass(frozen=True)
class GroundStation:
    """Ground station definition used by the HF-3 access geometry."""

    latitude_deg: float = 0.0
    longitude_deg: float = 0.0
    altitude_m: float = 0.0
    min_elevation_deg: float = 10.0
    max_range_m: float | None = None
    name: str = "ground_station"

    def __post_init__(self) -> None:
        lat = _finite_number(self.latitude_deg, "ground_station.latitude_deg")
        lon = _finite_number(self.longitude_deg, "ground_station.longitude_deg")
        alt = _finite_number(self.altitude_m, "ground_station.altitude_m")
        mine = _finite_number(self.min_elevation_deg, "ground_station.min_elevation_deg")
        if not -90.0 <= lat <= 90.0:
            raise OrbitEnvironmentMediumError("ground_station.latitude_deg must be within [-90, 90]")
        if not -360.0 <= lon <= 360.0:
            raise OrbitEnvironmentMediumError("ground_station.longitude_deg must be within [-360, 360]")
        if alt < -500.0:
            raise OrbitEnvironmentMediumError("ground_station.altitude_m must be above -500 m")
        if not -90.0 <= mine <= 90.0:
            raise OrbitEnvironmentMediumError("ground_station.min_elevation_deg must be within [-90, 90]")
        object.__setattr__(self, "latitude_deg", lat)
        object.__setattr__(self, "longitude_deg", lon)
        object.__setattr__(self, "altitude_m", alt)
        object.__setattr__(self, "min_elevation_deg", mine)
        if self.max_range_m is not None:
            object.__setattr__(self, "max_range_m", _positive_number(self.max_range_m, "ground_station.max_range_m"))

    @classmethod
    def from_mapping(cls, payload: Mapping[str, Any] | None) -> "GroundStation | None":
        if not isinstance(payload, Mapping) or not payload:
            return None
        return cls(
            latitude_deg=float(payload.get("latitude_deg", payload.get("lat_deg", 0.0))),
            longitude_deg=float(payload.get("longitude_deg", payload.get("lon_deg", 0.0))),
            altitude_m=float(payload.get("altitude_m", 0.0)),
            min_elevation_deg=float(payload.get("min_elevation_deg", 10.0)),
            max_range_m=float(payload["max_range_m"]) if payload.get("max_range_m") is not None else None,
            name=str(payload.get("name", "ground_station")),
        )

    def ecef_position_m(self, earth_radius_m: float = EARTH_RADIUS_M) -> tuple[float, float, float]:
        lat = _deg_to_rad(self.latitude_deg)
        lon = _deg_to_rad(self.longitude_deg)
        r = earth_radius_m + self.altitude_m
        clat = math.cos(lat)
        return (r * clat * math.cos(lon), r * clat * math.sin(lon), r * math.sin(lat))

    def enu_basis(self) -> dict[str, tuple[float, float, float]]:
        lat = _deg_to_rad(self.latitude_deg)
        lon = _deg_to_rad(self.longitude_deg)
        east = (-math.sin(lon), math.cos(lon), 0.0)
        north = (-math.sin(lat) * math.cos(lon), -math.sin(lat) * math.sin(lon), math.cos(lat))
        up = (math.cos(lat) * math.cos(lon), math.cos(lat) * math.sin(lon), math.sin(lat))
        return {"east": east, "north": north, "up": up}

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "latitude_deg": self.latitude_deg,
            "longitude_deg": self.longitude_deg,
            "altitude_m": self.altitude_m,
            "min_elevation_deg": self.min_elevation_deg,
            "max_range_m": self.max_range_m,
        }


@dataclass(frozen=True)
class MediumOrbitConfig:
    """Medium-fidelity orbit/environment configuration."""

    semi_major_axis_m: float
    eccentricity: float = 0.0
    inclination_rad: float = math.radians(51.6)
    raan_rad: float = 0.0
    arg_perigee_rad: float = 0.0
    mean_anomaly_rad: float = 0.0
    epoch_utc: str = DEFAULT_EPOCH_UTC
    earth_radius_m: float = EARTH_RADIUS_M
    earth_mu_m3_s2: float = EARTH_MU_M3_S2
    j2: float = EARTH_J2
    use_j2_gravity: bool = True
    sun_model: str = "analytic"
    sun_vector_eci: tuple[float, float, float] = (1.0, 0.0, 0.0)
    enable_eclipse: bool = True
    eclipse_penumbra_band_m: float = 100_000.0
    magnetic_field_model: str = "dipole"
    magnetic_dipole_axis_eci: tuple[float, float, float] = (0.0, 0.0, 1.0)
    magnetic_equator_strength_t: float = 3.12e-5
    ground_station: GroundStation | None = None
    solver: FixedStepSolverConfig | None = None

    def __post_init__(self) -> None:
        a = _positive_number(self.semi_major_axis_m, "semi_major_axis_m")
        re = _positive_number(self.earth_radius_m, "earth_radius_m")
        mu = _positive_number(self.earth_mu_m3_s2, "earth_mu_m3_s2")
        ecc = _finite_number(self.eccentricity, "eccentricity")
        if not 0.0 <= ecc < 0.2:
            raise OrbitEnvironmentMediumError("eccentricity must be within [0, 0.2) for HF-3 medium-fidelity LEO envelope")
        if a * (1.0 - ecc) <= re + 100_000.0:
            raise OrbitEnvironmentMediumError("perigee altitude must exceed 100 km")
        if a * (1.0 + ecc) >= re + 3_000_000.0:
            raise OrbitEnvironmentMediumError("apogee altitude must remain below 3000 km for HF-3 medium-fidelity LEO envelope")
        inc = _finite_number(self.inclination_rad, "inclination_rad")
        if not 0.0 <= inc <= math.pi:
            raise OrbitEnvironmentMediumError("inclination_rad must be within [0, pi]")
        object.__setattr__(self, "semi_major_axis_m", a)
        object.__setattr__(self, "earth_radius_m", re)
        object.__setattr__(self, "earth_mu_m3_s2", mu)
        object.__setattr__(self, "eccentricity", ecc)
        object.__setattr__(self, "inclination_rad", inc)
        object.__setattr__(self, "raan_rad", _wrap_rad(_finite_number(self.raan_rad, "raan_rad")))
        object.__setattr__(self, "arg_perigee_rad", _wrap_rad(_finite_number(self.arg_perigee_rad, "arg_perigee_rad")))
        object.__setattr__(self, "mean_anomaly_rad", _wrap_rad(_finite_number(self.mean_anomaly_rad, "mean_anomaly_rad")))
        parse_utc(self.epoch_utc)
        sun_model = str(self.sun_model or "analytic").strip().lower()
        if sun_model not in {"analytic", "constant"}:
            raise OrbitEnvironmentMediumError("sun_model must be 'analytic' or 'constant' for HF-3")
        object.__setattr__(self, "sun_model", sun_model)
        object.__setattr__(self, "sun_vector_eci", normalize_vector(self.sun_vector_eci, "sun_vector_eci"))
        mag_model = str(self.magnetic_field_model or "dipole").strip().lower()
        if mag_model not in {"dipole", "none"}:
            raise OrbitEnvironmentMediumError("magnetic_field_model must be 'dipole' or 'none' for HF-3")
        object.__setattr__(self, "magnetic_field_model", mag_model)
        object.__setattr__(self, "magnetic_dipole_axis_eci", normalize_vector(self.magnetic_dipole_axis_eci, "magnetic_dipole_axis_eci"))
        object.__setattr__(self, "magnetic_equator_strength_t", _positive_number(self.magnetic_equator_strength_t, "magnetic_equator_strength_t"))
        object.__setattr__(self, "eclipse_penumbra_band_m", _positive_number(self.eclipse_penumbra_band_m, "eclipse_penumbra_band_m"))

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "MediumOrbitConfig":
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        epoch = str(sim.get("epoch_utc") or sim.get("epoch") or DEFAULT_EPOCH_UTC)
        earth_radius = float(orbit.get("earth_radius_m", params.get("earth_radius_m", EARTH_RADIUS_M)))
        if orbit.get("semi_major_axis_m") is not None:
            semi_major_axis = float(orbit["semi_major_axis_m"])
        else:
            altitude_m = float(orbit.get("altitude_m", params.get("altitude_m", 500_000.0)))
            semi_major_axis = earth_radius + altitude_m
        ecc = float(orbit.get("eccentricity", params.get("eccentricity", 0.0)))
        inc_deg = float(orbit.get("inclination_deg", params.get("inclination_deg", 51.6)))
        raan_deg = float(orbit.get("raan_deg", params.get("raan_deg", 0.0)))
        argp_deg = float(orbit.get("arg_perigee_deg", orbit.get("argument_of_perigee_deg", params.get("arg_perigee_deg", 0.0))))
        if orbit.get("mean_anomaly_deg") is not None:
            mean_anomaly_rad = _deg_to_rad(float(orbit["mean_anomaly_deg"]))
        else:
            true_deg = float(orbit.get("true_anomaly_deg", orbit.get("arg_lat0_deg", params.get("true_anomaly_deg", 0.0))))
            mean_anomaly_rad = true_anomaly_to_mean_anomaly(_deg_to_rad(true_deg), ecc)
        gs_payload = None
        if isinstance(params.get("ground_station"), Mapping):
            gs_payload = params.get("ground_station")
        elif isinstance(orbit.get("ground_station"), Mapping):
            gs_payload = orbit.get("ground_station")
        solver = FixedStepSolverConfig.from_simulation(dict(sim))
        return cls(
            semi_major_axis_m=semi_major_axis,
            eccentricity=ecc,
            inclination_rad=_deg_to_rad(inc_deg),
            raan_rad=_deg_to_rad(raan_deg),
            arg_perigee_rad=_deg_to_rad(argp_deg),
            mean_anomaly_rad=mean_anomaly_rad,
            epoch_utc=epoch,
            earth_radius_m=earth_radius,
            earth_mu_m3_s2=float(orbit.get("earth_mu_m3_s2", params.get("earth_mu_m3_s2", EARTH_MU_M3_S2))),
            j2=float(orbit.get("j2", params.get("j2", EARTH_J2))),
            use_j2_gravity=bool(orbit.get("use_j2_gravity", params.get("use_j2_gravity", True))),
            sun_model=str(orbit.get("sun_model", params.get("sun_model", "analytic"))),
            sun_vector_eci=_vec3(orbit.get("sun_vector_n", orbit.get("sun_vector_eci", params.get("sun_vector_eci", (1.0, 0.0, 0.0)))), "sun_vector_eci"),
            enable_eclipse=bool(orbit.get("enable_eclipse", params.get("enable_eclipse", True))),
            eclipse_penumbra_band_m=float(orbit.get("eclipse_penumbra_band_m", params.get("eclipse_penumbra_band_m", 100_000.0))),
            magnetic_field_model=str(orbit.get("magnetic_field_model", params.get("magnetic_field_model", "dipole"))),
            magnetic_dipole_axis_eci=_vec3(orbit.get("magnetic_dipole_axis_n", params.get("magnetic_dipole_axis_eci", (0.0, 0.0, 1.0))), "magnetic_dipole_axis_eci"),
            magnetic_equator_strength_t=float(orbit.get("magnetic_equator_strength_t", params.get("magnetic_equator_strength_t", 3.12e-5))),
            ground_station=GroundStation.from_mapping(gs_payload),
            solver=solver,
        )

    @property
    def mean_motion_rad_s(self) -> float:
        return math.sqrt(self.earth_mu_m3_s2 / self.semi_major_axis_m**3)

    @property
    def semi_latus_rectum_m(self) -> float:
        return self.semi_major_axis_m * (1.0 - self.eccentricity**2)

    def j2_rates_rad_s(self) -> dict[str, float]:
        if not self.use_j2_gravity:
            return {"raan_dot_rad_s": 0.0, "arg_perigee_dot_rad_s": 0.0, "mean_motion_correction_rad_s": 0.0}
        p = self.semi_latus_rectum_m
        n = self.mean_motion_rad_s
        factor = self.j2 * (self.earth_radius_m / p) ** 2
        cos_i = math.cos(self.inclination_rad)
        sqrt_one_minus_e2 = math.sqrt(max(0.0, 1.0 - self.eccentricity**2))
        raan_dot = -1.5 * factor * n * cos_i
        argp_dot = 0.75 * factor * n * (5.0 * cos_i * cos_i - 1.0)
        mean_corr = 0.75 * factor * n * sqrt_one_minus_e2 * (3.0 * cos_i * cos_i - 1.0)
        return {"raan_dot_rad_s": raan_dot, "arg_perigee_dot_rad_s": argp_dot, "mean_motion_correction_rad_s": mean_corr}

    def to_dict(self) -> dict[str, Any]:
        rates = self.j2_rates_rad_s()
        return {
            "schema_version": HF3_ORBIT_ENVIRONMENT_SCHEMA_VERSION,
            "semi_major_axis_m": self.semi_major_axis_m,
            "eccentricity": self.eccentricity,
            "inclination_deg": _rad_to_deg(self.inclination_rad),
            "raan_deg": _rad_to_deg(self.raan_rad),
            "arg_perigee_deg": _rad_to_deg(self.arg_perigee_rad),
            "mean_anomaly_deg": _rad_to_deg(self.mean_anomaly_rad),
            "epoch_utc": self.epoch_utc,
            "earth_radius_m": self.earth_radius_m,
            "earth_mu_m3_s2": self.earth_mu_m3_s2,
            "j2": self.j2,
            "use_j2_gravity": self.use_j2_gravity,
            "j2_rates_rad_s": rates,
            "sun_model": self.sun_model,
            "sun_vector_eci": list(self.sun_vector_eci),
            "enable_eclipse": self.enable_eclipse,
            "eclipse_penumbra_band_m": self.eclipse_penumbra_band_m,
            "magnetic_field_model": self.magnetic_field_model,
            "magnetic_dipole_axis_eci": list(self.magnetic_dipole_axis_eci),
            "magnetic_equator_strength_t": self.magnetic_equator_strength_t,
            "ground_station": self.ground_station.to_dict() if self.ground_station else None,
            "solver": self.solver.to_dict() if self.solver else None,
            "fidelity_level": "medium",
            "can_claim_high_fidelity": False,
        }


@dataclass(frozen=True)
class OrbitEnvironmentMediumSample:
    time_s: float
    sample_index: int
    utc: str
    cartesian: CartesianState
    raan_rad: float
    arg_perigee_rad: float
    mean_anomaly_rad: float
    sun_vector_eci: tuple[float, float, float]
    shadow_factor: float
    eclipse_flag: bool
    umbra_flag: bool
    penumbra_flag: bool
    magnetic_field_eci_t: tuple[float, float, float]
    ground_range_m: float | None = None
    ground_elevation_deg: float | None = None
    ground_has_access: bool | None = None
    frame: FrameMetadata = field(default_factory=lambda: FrameMetadata(frame_id=ECI))

    def to_trace_row(self, *, task_id: str, case_id: str, capability_id: str, mode: str, target_level: str = "integrated", target_name: str = "orbit_environment", earth_radius_m: float = EARTH_RADIUS_M) -> dict[str, Any]:
        pos = self.cartesian.position_m
        vel = self.cartesian.velocity_m_s
        mag_norm = vector_norm(self.magnetic_field_eci_t)
        return {
            "task_id": task_id,
            "case_id": case_id,
            "time_s": self.time_s,
            "sample_index": self.sample_index,
            "utc": self.utc,
            "target_level": target_level,
            "target_name": target_name,
            "mode": mode,
            "capability_id": capability_id,
            "fidelity_level": "medium",
            "frame.orbit_state": self.frame.frame_id,
            "orbit.model": "keplerian_two_body_with_optional_j2_secular",
            "orbit.r_bn_n_m_x": pos[0],
            "orbit.r_bn_n_m_y": pos[1],
            "orbit.r_bn_n_m_z": pos[2],
            "orbit.v_bn_n_m_s_x": vel[0],
            "orbit.v_bn_n_m_s_y": vel[1],
            "orbit.v_bn_n_m_s_z": vel[2],
            "orbit.radius_m": self.cartesian.radius_m,
            "orbit.altitude_m": self.cartesian.radius_m - earth_radius_m,
            "orbit.speed_m_s": self.cartesian.speed_m_s,
            "orbit.raan_deg": _rad_to_deg(self.raan_rad),
            "orbit.arg_perigee_deg": _rad_to_deg(self.arg_perigee_rad),
            "orbit.mean_anomaly_deg": _rad_to_deg(self.mean_anomaly_rad),
            "environment.sun_vector_n_x": self.sun_vector_eci[0],
            "environment.sun_vector_n_y": self.sun_vector_eci[1],
            "environment.sun_vector_n_z": self.sun_vector_eci[2],
            "environment.sun_vector_frame": ECI,
            "environment.shadow_factor": self.shadow_factor,
            "environment.eclipse_flag": self.eclipse_flag,
            "environment.umbra_flag": self.umbra_flag,
            "environment.penumbra_flag": self.penumbra_flag,
            "environment.magnetic_field_n_t_x": self.magnetic_field_eci_t[0],
            "environment.magnetic_field_n_t_y": self.magnetic_field_eci_t[1],
            "environment.magnetic_field_n_t_z": self.magnetic_field_eci_t[2],
            "environment.magnetic_field_norm_t": mag_norm,
            "ground.range_m": self.ground_range_m,
            "ground.elevation_deg": self.ground_elevation_deg,
            "ground.has_access": self.ground_has_access,
        }


def true_anomaly_to_mean_anomaly(true_anomaly_rad: float, eccentricity: float) -> float:
    """Convert true anomaly to mean anomaly."""

    e = float(eccentricity)
    nu = float(true_anomaly_rad)
    if e <= 1e-12:
        return _wrap_rad(nu)
    # Numerically stable elliptical relation.
    E = math.atan2(math.sqrt(1.0 - e * e) * math.sin(nu), e + math.cos(nu))
    return _wrap_rad(E - e * math.sin(E))


def solve_kepler(mean_anomaly_rad: float, eccentricity: float, *, max_iter: int = 20, tol: float = 1e-13) -> float:
    """Solve M = E - e sin(E) for elliptical eccentric anomaly."""

    m = _wrap_rad(mean_anomaly_rad)
    e = float(eccentricity)
    if e <= 1e-12:
        return m
    E = m if e < 0.8 else math.pi
    for _ in range(max_iter):
        f = E - e * math.sin(E) - m
        fp = 1.0 - e * math.cos(E)
        delta = f / fp
        E -= delta
        if abs(delta) < tol:
            break
    return E


def orbital_elements_to_cartesian(
    *,
    semi_major_axis_m: float,
    eccentricity: float,
    inclination_rad: float,
    raan_rad: float,
    arg_perigee_rad: float,
    mean_anomaly_rad: float,
    earth_mu_m3_s2: float = EARTH_MU_M3_S2,
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Convert Keplerian elements to ECI Cartesian state."""

    a = _positive_number(semi_major_axis_m, "semi_major_axis_m")
    e = float(eccentricity)
    mu = _positive_number(earth_mu_m3_s2, "earth_mu_m3_s2")
    E = solve_kepler(mean_anomaly_rad, e)
    cos_E = math.cos(E)
    sin_E = math.sin(E)
    radius = a * (1.0 - e * cos_E)
    # Perifocal position and velocity.
    x_p = a * (cos_E - e)
    y_p = a * math.sqrt(1.0 - e * e) * sin_E
    factor = math.sqrt(mu * a) / radius
    vx_p = -factor * sin_E
    vy_p = factor * math.sqrt(1.0 - e * e) * cos_E

    cos_o = math.cos(raan_rad)
    sin_o = math.sin(raan_rad)
    cos_i = math.cos(inclination_rad)
    sin_i = math.sin(inclination_rad)
    cos_w = math.cos(arg_perigee_rad)
    sin_w = math.sin(arg_perigee_rad)

    # Rotation matrix R3(raan) R1(i) R3(arg_perigee).
    r11 = cos_o * cos_w - sin_o * sin_w * cos_i
    r12 = -cos_o * sin_w - sin_o * cos_w * cos_i
    r21 = sin_o * cos_w + cos_o * sin_w * cos_i
    r22 = -sin_o * sin_w + cos_o * cos_w * cos_i
    r31 = sin_w * sin_i
    r32 = cos_w * sin_i

    r = (r11 * x_p + r12 * y_p, r21 * x_p + r22 * y_p, r31 * x_p + r32 * y_p)
    v = (r11 * vx_p + r12 * vy_p, r21 * vx_p + r22 * vy_p, r31 * vx_p + r32 * vy_p)
    return r, v


def analytic_sun_vector_eci(epoch_utc: str, elapsed_s: float = 0.0) -> tuple[float, float, float]:
    """Low-order analytic Sun direction in ECI-like J2000 axes.

    The approximation is sufficient for deterministic medium-fidelity mission
    coupling and eclipse logic.  It is not a SPICE ephemeris replacement.
    """

    epoch = parse_utc(epoch_utc)
    current = epoch + timedelta(seconds=float(elapsed_s))
    n_days = datetime_to_julian_date(current) - 2451545.0
    mean_long_deg = (280.460 + 0.9856474 * n_days) % 360.0
    mean_anom_deg = (357.528 + 0.9856003 * n_days) % 360.0
    g = _deg_to_rad(mean_anom_deg)
    ecliptic_long = _deg_to_rad((mean_long_deg + 1.915 * math.sin(g) + 0.020 * math.sin(2.0 * g)) % 360.0)
    obliquity = _deg_to_rad(23.439 - 0.0000004 * n_days)
    return normalize_vector((math.cos(ecliptic_long), math.cos(obliquity) * math.sin(ecliptic_long), math.sin(obliquity) * math.sin(ecliptic_long)), "analytic_sun_vector_eci")


def eclipse_shadow_factor(
    position_eci_m: tuple[float, float, float],
    sun_vector_eci: tuple[float, float, float],
    *,
    earth_radius_m: float = EARTH_RADIUS_M,
    penumbra_band_m: float = 100_000.0,
    enabled: bool = True,
) -> tuple[float, bool, bool, bool]:
    """Return shadow factor and eclipse flags using a deterministic shadow tube.

    ``shadow_factor`` is 1 in full sunlight, 0 in umbra, and linearly blended in
    a narrow penumbra band around the cylindrical Earth shadow boundary.
    """

    if not enabled:
        return 1.0, False, False, False
    r = _vec3(position_eci_m, "position_eci_m")
    s = normalize_vector(sun_vector_eci, "sun_vector_eci")
    along_sun = _dot(r, s)
    if along_sun >= 0.0:
        return 1.0, False, False, False
    perpendicular = vector_norm(cross(r, s))
    inner = earth_radius_m - penumbra_band_m
    outer = earth_radius_m + penumbra_band_m
    if perpendicular <= inner:
        return 0.0, True, True, False
    if perpendicular >= outer:
        return 1.0, False, False, False
    shadow = (perpendicular - inner) / (outer - inner)
    shadow = max(0.0, min(1.0, shadow))
    return shadow, True, False, True


def dipole_magnetic_field_eci_t(
    position_eci_m: tuple[float, float, float],
    *,
    earth_radius_m: float = EARTH_RADIUS_M,
    dipole_axis_eci: tuple[float, float, float] = (0.0, 0.0, 1.0),
    equator_strength_t: float = 3.12e-5,
    model: str = "dipole",
) -> tuple[float, float, float]:
    if str(model).lower() == "none":
        return (0.0, 0.0, 0.0)
    r = _vec3(position_eci_m, "position_eci_m")
    r_norm = vector_norm(r)
    if r_norm <= 0:
        raise OrbitEnvironmentMediumError("position norm must be positive for magnetic field")
    r_hat = _scalar(r, 1.0 / r_norm)
    m_hat = normalize_vector(dipole_axis_eci, "dipole_axis_eci")
    strength = equator_strength_t * (earth_radius_m / r_norm) ** 3
    mdotr = _dot(m_hat, r_hat)
    return _scalar(_sub(_scalar(r_hat, 3.0 * mdotr), m_hat), strength)


def ground_access_geometry(
    position_eci_m: tuple[float, float, float],
    *,
    ground_station: GroundStation | None,
    epoch_utc: str,
    elapsed_s: float,
    earth_radius_m: float = EARTH_RADIUS_M,
) -> dict[str, Any]:
    if ground_station is None:
        return {"range_m": None, "elevation_deg": None, "has_access": None}
    target_utc = (parse_utc(epoch_utc) + timedelta(seconds=float(elapsed_s))).isoformat().replace("+00:00", "Z")
    sat_ecef = eci_to_ecef(position_eci_m, epoch_utc=epoch_utc, target_utc=target_utc)
    gs_ecef = ground_station.ecef_position_m(earth_radius_m=earth_radius_m)
    rho = _sub(sat_ecef, gs_ecef)
    rng = vector_norm(rho)
    if rng <= 0:
        elevation = 90.0
    else:
        rho_hat = _scalar(rho, 1.0 / rng)
        up = ground_station.enu_basis()["up"]
        elevation = _rad_to_deg(math.asin(max(-1.0, min(1.0, _dot(rho_hat, up)))))
    has_access = elevation >= ground_station.min_elevation_deg
    if ground_station.max_range_m is not None:
        has_access = has_access and rng <= ground_station.max_range_m
    return {"range_m": rng, "elevation_deg": elevation, "has_access": bool(has_access)}


def propagate_medium_orbit_environment(config: MediumOrbitConfig, time_grid: TimeGrid | None = None) -> list[OrbitEnvironmentMediumSample]:
    """Propagate medium-fidelity orbit/environment samples."""

    if not isinstance(config, MediumOrbitConfig):
        raise OrbitEnvironmentMediumError("config must be MediumOrbitConfig")
    if time_grid is None:
        if config.solver is not None:
            times = config.solver.time_grid()
            time_grid = build_time_grid(duration_s=config.solver.duration_s, sample_s=config.solver.step_s, epoch_utc=config.epoch_utc, include_endpoint=config.solver.include_endpoint)
            # Defensive check so future solver changes do not silently drift.
            if tuple(times) != tuple(time_grid.times_s):
                raise OrbitEnvironmentMediumError("solver time grid and HF-1 time grid disagree")
        else:
            time_grid = build_time_grid(duration_s=300.0, sample_s=10.0, epoch_utc=config.epoch_utc)
    rates = config.j2_rates_rad_s()
    frame = FrameMetadata(frame_id=ECI, epoch_utc=config.epoch_utc, frame_orientation="J2000-like inertial axes")
    samples: list[OrbitEnvironmentMediumSample] = []
    for idx, t in enumerate(time_grid.times_s):
        raan = _wrap_rad(config.raan_rad + rates["raan_dot_rad_s"] * t)
        argp = _wrap_rad(config.arg_perigee_rad + rates["arg_perigee_dot_rad_s"] * t)
        mean_anomaly = _wrap_rad(config.mean_anomaly_rad + (config.mean_motion_rad_s + rates["mean_motion_correction_rad_s"]) * t)
        r_eci, v_eci = orbital_elements_to_cartesian(
            semi_major_axis_m=config.semi_major_axis_m,
            eccentricity=config.eccentricity,
            inclination_rad=config.inclination_rad,
            raan_rad=raan,
            arg_perigee_rad=argp,
            mean_anomaly_rad=mean_anomaly,
            earth_mu_m3_s2=config.earth_mu_m3_s2,
        )
        sun = analytic_sun_vector_eci(config.epoch_utc, t) if config.sun_model == "analytic" else config.sun_vector_eci
        shadow, eclipse, umbra, penumbra = eclipse_shadow_factor(
            r_eci,
            sun,
            earth_radius_m=config.earth_radius_m,
            penumbra_band_m=config.eclipse_penumbra_band_m,
            enabled=config.enable_eclipse,
        )
        mag = dipole_magnetic_field_eci_t(
            r_eci,
            earth_radius_m=config.earth_radius_m,
            dipole_axis_eci=config.magnetic_dipole_axis_eci,
            equator_strength_t=config.magnetic_equator_strength_t,
            model=config.magnetic_field_model,
        )
        ground = ground_access_geometry(
            r_eci,
            ground_station=config.ground_station,
            epoch_utc=config.epoch_utc,
            elapsed_s=t,
            earth_radius_m=config.earth_radius_m,
        )
        utc = time_grid.utc_at(t).isoformat().replace("+00:00", "Z")
        cart = CartesianState(position_m=r_eci, velocity_m_s=v_eci, frame=frame, epoch_utc=utc)
        samples.append(
            OrbitEnvironmentMediumSample(
                time_s=float(t),
                sample_index=idx,
                utc=utc,
                cartesian=cart,
                raan_rad=raan,
                arg_perigee_rad=argp,
                mean_anomaly_rad=mean_anomaly,
                sun_vector_eci=sun,
                shadow_factor=shadow,
                eclipse_flag=eclipse,
                umbra_flag=umbra,
                penumbra_flag=penumbra,
                magnetic_field_eci_t=mag,
                ground_range_m=ground["range_m"],
                ground_elevation_deg=ground["elevation_deg"],
                ground_has_access=ground["has_access"],
                frame=frame,
            )
        )
    return samples


def summarize_medium_orbit_environment(samples: Iterable[OrbitEnvironmentMediumSample], config: MediumOrbitConfig) -> dict[str, Any]:
    rows = list(samples)
    if not rows:
        raise OrbitEnvironmentMediumError("cannot summarize empty sample list")
    radii = [s.cartesian.radius_m for s in rows]
    speeds = [s.cartesian.speed_m_s for s in rows]
    altitudes = [r - config.earth_radius_m for r in radii]
    shadows = [s.shadow_factor for s in rows]
    mag_norms = [vector_norm(s.magnetic_field_eci_t) for s in rows]
    access_values = [s.ground_has_access for s in rows if s.ground_has_access is not None]
    eclipse_count = sum(1 for s in rows if s.eclipse_flag)
    umbra_count = sum(1 for s in rows if s.umbra_flag)
    penumbra_count = sum(1 for s in rows if s.penumbra_flag)
    access_fraction = (sum(1 for v in access_values if v) / len(access_values)) if access_values else None
    return {
        "schema_version": HF3_ORBIT_ENVIRONMENT_SCHEMA_VERSION,
        "status": "complete",
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "model_family": "keplerian_two_body_with_optional_j2_secular",
        "sample_count": len(rows),
        "duration_s": rows[-1].time_s,
        "sample_s": rows[1].time_s - rows[0].time_s if len(rows) > 1 else None,
        "qoi": {
            "orbit.radius_mean_m": sum(radii) / len(radii),
            "orbit.radius_min_m": min(radii),
            "orbit.radius_max_m": max(radii),
            "orbit.radius_span_m": max(radii) - min(radii),
            "orbit.altitude_min_m": min(altitudes),
            "orbit.altitude_max_m": max(altitudes),
            "orbit.speed_mean_m_s": sum(speeds) / len(speeds),
            "orbit.j2_enabled": config.use_j2_gravity,
            "environment.eclipse_fraction": eclipse_count / len(rows),
            "environment.umbra_fraction": umbra_count / len(rows),
            "environment.penumbra_fraction": penumbra_count / len(rows),
            "environment.shadow_min": min(shadows),
            "environment.shadow_max": max(shadows),
            "environment.magnetic_field_norm_min_t": min(mag_norms),
            "environment.magnetic_field_norm_max_t": max(mag_norms),
            "ground.access_fraction": access_fraction,
        },
        "config": config.to_dict(),
        "known_physics_limits": [
            "J2 is represented as secular element precession, not numerical osculating-state acceleration.",
            "Atmospheric drag, SRP, third-body gravity, finite Earth oblateness field, and SPICE ephemerides are not included.",
            "Eclipse uses a deterministic cylindrical/linear-band approximation, not a full umbra/penumbra cone model.",
            "Magnetic field is a dipole proxy unless disabled; WMM/IGRF is not included.",
        ],
    }


def build_hf3_orbit_environment_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return serializable HF-3 metadata for manifests/readiness reports."""

    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        config = MediumOrbitConfig.from_task_spec(spec) if spec else None
        config_payload = config.to_dict() if config else None
        status = "implemented_medium_fidelity_not_high_fidelity"
        validation_status = "config_valid" if config else "metadata_only"
    except Exception as exc:
        config_payload = {"error": str(exc)}
        status = "configured_with_validation_error"
        validation_status = "config_invalid"
    return {
        "schema_version": HF3_ORBIT_ENVIRONMENT_SCHEMA_VERSION,
        "route_b_version": "B-2/HF-3",
        "foundation_dependency": "B-1/HF-1+HF-2",
        "status": status,
        "validation_status": validation_status,
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "implemented_models": [
            "Keplerian two-body Cartesian state generation",
            "Optional J2 secular RAAN/argument-of-perigee/mean-anomaly correction",
            "Analytic Sun vector or constant Sun vector",
            "Cylindrical/linear-band eclipse approximation",
            "Dipole magnetic-field proxy",
            "ECEF ground-station access geometry",
        ],
        "uses_hf_foundations": [
            "hf1.time_systems.TimeGrid",
            "hf2.frames.FrameMetadata and ECI/ECEF transform",
            "hf2.state.CartesianState",
            "hf2.solvers.FixedStepSolverConfig metadata",
        ],
        "config": config_payload,
        "remaining_route_b_dependencies": [
            "HF-4 ADCS closed-loop basic model",
            "HF-5/HF-6 coupled spacecraft mission models",
            "HF-7/HF-8 physical validation gates",
            "HF-9 benchmark scenarios and tolerance envelopes",
        ],
    }


__all__ = [
    "HF3_ORBIT_ENVIRONMENT_SCHEMA_VERSION",
    "EARTH_RADIUS_M",
    "EARTH_MU_M3_S2",
    "EARTH_J2",
    "GroundStation",
    "MediumOrbitConfig",
    "OrbitEnvironmentMediumError",
    "OrbitEnvironmentMediumSample",
    "analytic_sun_vector_eci",
    "build_hf3_orbit_environment_payload",
    "dipole_magnetic_field_eci_t",
    "eclipse_shadow_factor",
    "ground_access_geometry",
    "orbital_elements_to_cartesian",
    "propagate_medium_orbit_environment",
    "solve_kepler",
    "summarize_medium_orbit_environment",
    "true_anomaly_to_mean_anomaly",
]
