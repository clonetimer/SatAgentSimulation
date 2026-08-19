"""Synthetic/reference orbit and environment service.

This module deliberately stays deterministic and supportData-free.  It is the
common environment interface that later Basilisk modules can replace or feed.
"""
from __future__ import annotations

from math import asin, cos, pi, radians, sin, sqrt
from typing import Iterable, Sequence

from .schemas import (
    GroundStationConfig,
    OrbitEnvironmentConfig,
    OrbitEnvironmentProfile,
    OrbitEnvironmentSample,
    OrbitEnvironmentSummary,
)


def _vec3(values: Sequence[float]) -> tuple[float, float, float]:
    out = [float(x) for x in list(values)[:3]]
    while len(out) < 3:
        out.append(0.0)
    return (out[0], out[1], out[2])


def _norm(v: Sequence[float]) -> float:
    return sqrt(sum(float(x) ** 2 for x in v))


def _normalize(v: Sequence[float], fallback: Sequence[float] = (1.0, 0.0, 0.0)) -> tuple[float, float, float]:
    vec = _vec3(v)
    n = _norm(vec)
    if n <= 0.0:
        vec = _vec3(fallback)
        n = max(_norm(vec), 1e-15)
    return (vec[0] / n, vec[1] / n, vec[2] / n)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _sub(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2]))


def _scale(v: Sequence[float], k: float) -> tuple[float, float, float]:
    return (float(k) * float(v[0]), float(k) * float(v[1]), float(k) * float(v[2]))


def _rotate_z(v: Sequence[float], angle_rad: float) -> tuple[float, float, float]:
    c = cos(angle_rad)
    s = sin(angle_rad)
    x, y, z = _vec3(v)
    return (c * x - s * y, s * x + c * y, z)


def _rotate_x(v: Sequence[float], angle_rad: float) -> tuple[float, float, float]:
    c = cos(angle_rad)
    s = sin(angle_rad)
    x, y, z = _vec3(v)
    return (x, c * y - s * z, s * y + c * z)


def circular_orbit_state(cfg: OrbitEnvironmentConfig, time_s: float) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return inertial position/velocity for a simple circular orbit."""

    radius = cfg.earth_radius_m + cfg.altitude_m
    mean_motion = sqrt(cfg.earth_mu_m3_s2 / radius**3)
    u = radians(cfg.arg_lat0_deg) + mean_motion * float(time_s)
    r_orb = (radius * cos(u), radius * sin(u), 0.0)
    v_orb = (-radius * mean_motion * sin(u), radius * mean_motion * cos(u), 0.0)
    inc = radians(cfg.inclination_deg)
    raan = radians(cfg.raan_deg)
    r_n = _rotate_z(_rotate_x(r_orb, inc), raan)
    v_n = _rotate_z(_rotate_x(v_orb, inc), raan)
    return r_n, v_n


def cylindrical_shadow_factor(r_bn_n_m: Sequence[float], sun_vector_n: Sequence[float], earth_radius_m: float) -> float:
    """Return a simple umbra-only shadow factor, 0 in eclipse and 1 in sunlight."""

    r = _vec3(r_bn_n_m)
    s_hat = _normalize(sun_vector_n)
    along_sun = _dot(r, s_hat)
    # Spacecraft is behind Earth relative to the Sun when r dot s_hat < 0.
    if along_sun >= 0.0:
        return 1.0
    perp = _sub(r, _scale(s_hat, along_sun))
    return 0.0 if _norm(perp) < earth_radius_m else 1.0


def dipole_magnetic_field_n(cfg: OrbitEnvironmentConfig, r_bn_n_m: Sequence[float]) -> tuple[float, float, float]:
    """Return a deterministic centered-dipole magnetic field approximation.

    The field is intended as a mission-integration reference input, not a WMM
    replacement.  It preserves the important property that the magnetic field
    direction and magnitude vary with orbital position.
    """

    r = _vec3(r_bn_n_m)
    r_norm = max(_norm(r), 1e-15)
    r_hat = _scale(r, 1.0 / r_norm)
    m_hat = _normalize(cfg.magnetic_dipole_axis_n, (0.0, 0.0, 1.0))
    scale = cfg.magnetic_equator_strength_t * (cfg.earth_radius_m / r_norm) ** 3
    m_dot_r = _dot(m_hat, r_hat)
    field = tuple(scale * (3.0 * m_dot_r * r_hat[i] - m_hat[i]) for i in range(3))
    return (field[0], field[1], field[2])


def ground_station_position_n(cfg: OrbitEnvironmentConfig, station: GroundStationConfig, time_s: float) -> tuple[float, float, float]:
    radius = cfg.earth_radius_m + station.altitude_m
    lat = radians(station.latitude_deg)
    lon = radians(station.longitude_deg) + cfg.earth_rotation_rad_s * float(time_s)
    return (radius * cos(lat) * cos(lon), radius * cos(lat) * sin(lon), radius * sin(lat))


def ground_access(cfg: OrbitEnvironmentConfig, r_bn_n_m: Sequence[float], time_s: float) -> tuple[float, float, bool]:
    station = cfg.ground_station
    r_gs = ground_station_position_n(cfg, station, time_s)
    rho = _sub(r_bn_n_m, r_gs)
    rho_norm = max(_norm(rho), 1e-15)
    zenith = _normalize(r_gs)
    elevation_rad = asin(max(-1.0, min(1.0, _dot(rho, zenith) / rho_norm)))
    elevation_deg = elevation_rad * 180.0 / pi
    has_access = elevation_deg >= station.min_elevation_deg and rho_norm <= station.max_range_m
    return rho_norm, elevation_deg, bool(has_access)


def environment_sample(cfg: OrbitEnvironmentConfig, time_s: float) -> OrbitEnvironmentSample:
    sun = _normalize(cfg.sun_vector_n)
    r_n, v_n = circular_orbit_state(cfg, time_s)
    shadow = cylindrical_shadow_factor(r_n, sun, cfg.earth_radius_m)
    b_n = dipole_magnetic_field_n(cfg, r_n)
    b_norm = _norm(b_n)
    ground_range, ground_elev, access = ground_access(cfg, r_n, time_s)
    return OrbitEnvironmentSample(
        time_s=float(time_s),
        r_bn_n_m=r_n,
        v_bn_n_m_s=v_n,
        orbit_radius_m=_norm(r_n),
        sun_vector_n=sun,
        shadow_factor=shadow,
        magnetic_field_n_t=b_n,
        magnetic_field_norm_t=b_norm,
        ground_range_m=ground_range,
        ground_elevation_deg=ground_elev,
        ground_has_access=access,
    )


def generate_environment_profile(cfg: OrbitEnvironmentConfig | None = None) -> OrbitEnvironmentProfile:
    cfg = cfg or OrbitEnvironmentConfig()
    if cfg.dt_s <= 0.0:
        raise ValueError("dt_s must be positive")
    if cfg.duration_s < 0.0:
        raise ValueError("duration_s must be non-negative")
    n_steps = int(round(cfg.duration_s / cfg.dt_s))
    samples = tuple(environment_sample(cfg, idx * cfg.dt_s) for idx in range(n_steps + 1))
    return OrbitEnvironmentProfile(samples)


def _access_windows(samples: Sequence[OrbitEnvironmentSample]) -> int:
    count = 0
    was_access = False
    for sample in samples:
        if sample.ground_has_access and not was_access:
            count += 1
        was_access = sample.ground_has_access
    return count


def summarize_profile(profile: OrbitEnvironmentProfile) -> OrbitEnvironmentSummary:
    samples = list(profile.samples)
    if not samples:
        return OrbitEnvironmentSummary(0.0, 0, 0.0, 0.0, 0.0, 0.0, 0, 0.0, 0.0, 0.0, 0.0)
    radii = [s.orbit_radius_m for s in samples]
    shadows = [s.shadow_factor for s in samples]
    accesses = [1.0 if s.ground_has_access else 0.0 for s in samples]
    b_norms = [s.magnetic_field_norm_t for s in samples]
    duration = samples[-1].time_s - samples[0].time_s
    return OrbitEnvironmentSummary(
        duration_s=duration,
        num_samples=len(samples),
        orbit_radius_mean_m=sum(radii) / len(radii),
        orbit_radius_span_m=max(radii) - min(radii),
        eclipse_fraction=sum(1.0 for x in shadows if x <= 0.0) / len(shadows),
        access_fraction=sum(accesses) / len(accesses),
        access_windows=_access_windows(samples),
        magnetic_field_norm_min_t=min(b_norms),
        magnetic_field_norm_max_t=max(b_norms),
        shadow_min=min(shadows),
        shadow_max=max(shadows),
    )
