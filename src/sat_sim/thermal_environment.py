"""Finite-Earth shortwave/albedo/IR irradiance for lumped engineering surfaces.

Context: spherical Earth, uniform Lambertian albedo and uniform outgoing IR.
Surface normals and positions share one Earth-centred inertial frame. Incident
fluxes are returned separately; callers apply solar absorptivity/IR emissivity.
This is a geometry/energy model, not a weather or calibrated spacecraft model.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import math
from typing import Any

import numpy as np

ASTRONOMICAL_UNIT_M = 149_597_870_700.0
EARTH_EQUATORIAL_RADIUS_M = 6_378_136.6


def _scalar(value: Any, name: str, *, positive: bool = False, upper: float | None = None) -> float:
    if isinstance(value, (bool, str, bytes)):
        raise ValueError(f'THERMAL_ENV_INVALID_{name}')
    try:
        result = float(value)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f'THERMAL_ENV_INVALID_{name}') from exc
    if not math.isfinite(result) or result < 0. or (positive and result <= 0.) or (upper is not None and result > upper):
        raise ValueError(f'THERMAL_ENV_INVALID_{name}')
    return result


def _vector(value: Any, name: str) -> np.ndarray:
    try:
        array = np.asarray(value, dtype=float)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError(f'THERMAL_ENV_INVALID_{name}') from exc
    if array.shape != (3,) or not np.all(np.isfinite(array)):
        raise ValueError(f'THERMAL_ENV_INVALID_{name}')
    return array


@lru_cache(maxsize=16)
def _quadrature(radial_order: int, azimuth_order: int):
    if (isinstance(radial_order, bool) or not isinstance(radial_order, int)
            or not 4 <= radial_order <= 128 or isinstance(azimuth_order, bool)
            or not isinstance(azimuth_order, int) or not 8 <= azimuth_order <= 512):
        raise ValueError('THERMAL_ENV_INVALID_QUADRATURE')
    nodes, weights = np.polynomial.legendre.leggauss(radial_order)
    phi = (np.arange(azimuth_order, dtype=float)+.5)*(2.*math.pi/azimuth_order)
    arrays = (nodes, weights, np.cos(phi), np.sin(phi))
    for array in arrays:
        array.setflags(write=False)
    return arrays


@dataclass(frozen=True)
class SurfaceIrradiance:
    """Irradiance per unit physical face area, before optical coefficients."""
    solar_w_m2: np.ndarray
    albedo_w_m2: np.ndarray
    earth_ir_w_m2: np.ndarray
    earth_view_factors: np.ndarray
    solar_irradiance_at_spacecraft_w_m2: float
    earth_solid_angle_sr: float
    radial_order: int
    azimuth_order: int

    def to_dict(self) -> dict[str, Any]:
        return {name: value.tolist() if isinstance(value, np.ndarray) else value
                for name, value in vars(self).items()}


def surface_irradiance(
    spacecraft_position_m: Any, sun_position_m: Any, surface_normals_n: Any, *,
    eclipse_factor: float = 1., earth_radius_m: float = EARTH_EQUATORIAL_RADIUS_M,
    earth_ir_exitance_w_m2: float = 234., earth_albedo: float = .30,
    solar_irradiance_at_au_w_m2: float = 1361., astronomical_unit_m: float = ASTRONOMICAL_UNIT_M,
    radial_order: int = 16, azimuth_order: int = 64,
    integration_backend: str = 'compiled',
) -> SurfaceIrradiance:
    """Integrate the visible planetary disk in solid angle.

    IR radiance is J_IR/pi; reflected radiance is a*S*cos(solar incidence)/pi
    on each visible Earth patch. Positive receiver incidence is integrated over
    dOmega=d(mu)*d(phi). Spacecraft eclipse attenuates only direct sunlight;
    it never switches off Earth IR or arbitrarily multiplies reflected light.
    """
    position = _vector(spacecraft_position_m, 'SPACECRAFT_POSITION')
    sun = _vector(sun_position_m, 'SUN_POSITION')
    radius = _scalar(earth_radius_m, 'EARTH_RADIUS', positive=True)
    ir = _scalar(earth_ir_exitance_w_m2, 'EARTH_IR')
    albedo = _scalar(earth_albedo, 'ALBEDO', upper=1.)
    solar_reference = _scalar(solar_irradiance_at_au_w_m2, 'SOLAR_FLUX')
    au = _scalar(astronomical_unit_m, 'AU', positive=True)
    shadow = _scalar(eclipse_factor, 'ECLIPSE', upper=1.)
    distance = math.hypot(*position)
    if not math.isfinite(distance) or distance <= radius:
        raise ValueError('THERMAL_ENV_SPACECRAFT_INSIDE_EARTH')
    try:
        normals = np.asarray(surface_normals_n, dtype=float)
    except (ValueError, TypeError, OverflowError) as exc:
        raise ValueError('THERMAL_ENV_INVALID_SURFACE_NORMALS') from exc
    if normals.ndim != 2 or normals.shape[1] != 3 or not len(normals) or not np.all(np.isfinite(normals)):
        raise ValueError('THERMAL_ENV_INVALID_SURFACE_NORMALS')
    lengths = np.linalg.norm(normals, axis=1)
    if np.any(lengths <= 0.) or not np.all(np.isfinite(lengths)):
        raise ValueError('THERMAL_ENV_INVALID_SURFACE_NORMALS')
    normals = normals/lengths[:, None]
    sun_from_spacecraft = sun-position
    sun_distance = math.hypot(*sun_from_spacecraft)
    if not math.isfinite(sun_distance) or sun_distance <= 0. or math.hypot(*sun) <= radius:
        raise ValueError('THERMAL_ENV_INVALID_SUN_GEOMETRY')
    solar_flux = solar_reference*(au/sun_distance)**2
    direct = solar_flux*shadow*np.maximum(0., normals@(sun_from_spacecraft/sun_distance))

    radial_nodes, radial_weights, cos_phi, sin_phi = _quadrature(radial_order, azimuth_order)
    if integration_backend == 'compiled':
        from .thermal_quadrature import integrate_earth_disk
        reflected, earth_ir_flux, view_factors = integrate_earth_disk(
            position, sun, normals, radius, ir, albedo, solar_reference, au,
            radial_nodes, radial_weights, cos_phi, sin_phi,
        )
        if not all(np.all(np.isfinite(array)) for array in (direct, reflected, earth_ir_flux, view_factors)):
            raise ValueError('THERMAL_ENV_NONFINITE_RESULT')
        mu_min = math.sqrt(max(0., 1.-(radius/distance)**2))
        return SurfaceIrradiance(direct, reflected, earth_ir_flux, view_factors, solar_flux,
            2.*math.pi*(1.-mu_min), radial_order, azimuth_order)
    if integration_backend != 'numpy':
        raise ValueError('THERMAL_ENV_UNKNOWN_INTEGRATION_BACKEND')
    centre = -position/distance
    basis = np.eye(3)[int(np.argmin(np.abs(centre)))]
    tangent_x = np.cross(centre, basis)
    tangent_x /= np.linalg.norm(tangent_x)
    tangent_y = np.cross(centre, tangent_x)
    mu_min = math.sqrt(max(0., 1.-(radius/distance)**2))
    mu = mu_min+(radial_nodes+1.)*((1.-mu_min)/2.)
    weight_mu = radial_weights*((1.-mu_min)/2.)
    tangents = cos_phi[:, None]*tangent_x+sin_phi[:, None]*tangent_y
    directions = (mu[:, None, None]*centre
                  + np.sqrt(np.maximum(0., 1.-mu*mu))[:, None, None]*tangents[None, :, :])
    directions = directions.reshape(-1,3)
    solid_angle_weights = np.repeat(weight_mu,azimuth_order)*(2.*math.pi/azimuth_order)
    reception = np.maximum(0., normals@directions.T)
    view_factors = reception@solid_angle_weights/math.pi
    earth_ir_flux = ir*view_factors

    # Intersect each Earth-directed ray with the near spherical surface.
    intersection_distance = distance*(mu-np.sqrt(np.maximum(0., mu*mu-mu_min*mu_min)))
    patches = position+np.repeat(intersection_distance,azimuth_order)[:, None]*directions
    patch_normals = patches/np.linalg.norm(patches,axis=1)[:, None]
    sun_from_patch = sun-patches
    patch_sun_distance = np.linalg.norm(sun_from_patch,axis=1)
    patch_sun_direction = sun_from_patch/patch_sun_distance[:, None]
    illumination = np.maximum(0., np.sum(patch_normals*patch_sun_direction,axis=1))
    patch_solar_flux = solar_reference*(au/patch_sun_distance)**2
    reflected_radiance = albedo*patch_solar_flux*illumination/math.pi
    reflected = reception@(solid_angle_weights*reflected_radiance)
    if not all(np.all(np.isfinite(array)) for array in (direct,reflected,earth_ir_flux,view_factors)):
        raise ValueError('THERMAL_ENV_NONFINITE_RESULT')
    return SurfaceIrradiance(direct,reflected,earth_ir_flux,view_factors,solar_flux,
        2.*math.pi*(1.-mu_min),radial_order,azimuth_order)
