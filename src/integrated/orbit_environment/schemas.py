"""Schemas for the v4.3 orbit/environment service layer."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class GroundStationConfig:
    latitude_deg: float = 0.0
    longitude_deg: float = 0.0
    altitude_m: float = 0.0
    min_elevation_deg: float = 10.0
    max_range_m: float = 2_500_000.0


@dataclass(frozen=True)
class OrbitEnvironmentConfig:
    duration_s: float = 6000.0
    dt_s: float = 30.0
    earth_radius_m: float = 6_378_137.0
    earth_mu_m3_s2: float = 3.986004418e14
    earth_rotation_rad_s: float = 7.2921159e-5
    altitude_m: float = 500_000.0
    inclination_deg: float = 51.6
    raan_deg: float = 0.0
    arg_lat0_deg: float = -110.0
    sun_vector_n: tuple[float, float, float] = (1.0, 0.0, 0.0)
    magnetic_dipole_axis_n: tuple[float, float, float] = (0.0, 0.0, 1.0)
    magnetic_equator_strength_t: float = 3.12e-5
    ground_station: GroundStationConfig = GroundStationConfig()


@dataclass(frozen=True)
class BasiliskOrbEnvConfig:
    """Configuration for Basilisk orbit environment builder."""

    step_s: float = 1.0
    earth_radius_m: float = 6_378_137.0
    earth_mu_m3_s2: float = 3.986004418e14

    sun_model: str = "spice"
    sun_distance_m: float = 1.495978707e11
    sun_vector_n: tuple[float, float, float] = (1.0, 0.0, 0.0)

    magnetic_field_model: str = "dipole"
    magnetic_dipole_axis_n: tuple[float, float, float] = (0.0, 0.0, 1.0)
    magnetic_equator_strength_t: float = 3.12e-5

    gravity_model: str = "point_mass"
    use_j2_gravity: bool = True
    j2_coefficient: float = 1.08263e-3

    spice_data_path: str | None = None
    wmm_data_path: str | None = None
    strict_resource_loading: bool = False
    spice_kernel_names: tuple[str, ...] = (
        "naif0012.tls",
        "de430.bsp",
        "pck00010.tpc",
        "gm_de431.tpc",
        "de-403-masses.tpc",
    )
    spice_planet_names: tuple[str, ...] = ("sun", "earth")
    spice_zero_base: str = "earth"
    spice_epoch_utc: str = "2025 JAN 01 00:00:00.000"

    epoch_j2000_s: float = 0.0

    enable_eclipse: bool = False


@dataclass(frozen=True)
class OrbitEnvironmentSample:
    time_s: float
    r_bn_n_m: tuple[float, float, float]
    v_bn_n_m_s: tuple[float, float, float]
    orbit_radius_m: float
    sun_vector_n: tuple[float, float, float]
    shadow_factor: float
    magnetic_field_n_t: tuple[float, float, float]
    magnetic_field_norm_t: float
    ground_range_m: float
    ground_elevation_deg: float
    ground_has_access: bool


@dataclass(frozen=True)
class OrbitEnvironmentProfile:
    samples: tuple[OrbitEnvironmentSample, ...]


@dataclass(frozen=True)
class OrbitEnvironmentSummary:
    duration_s: float
    num_samples: int
    orbit_radius_mean_m: float
    orbit_radius_span_m: float
    eclipse_fraction: float
    access_fraction: float
    access_windows: int
    magnetic_field_norm_min_t: float
    magnetic_field_norm_max_t: float
    shadow_min: float
    shadow_max: float
