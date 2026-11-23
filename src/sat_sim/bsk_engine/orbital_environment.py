"""Explicit offline environment profiles for the unified native spacecraft.

The reference profile retains the historical simplified environment. The
spice_wmm profile requires local data and wires actual official Basilisk SPICE,
WMM and spherical-harmonic gravity models. There is no silent fallback.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import os
import threading
from typing import Any

from integrated.orbit_environment.assets import (
    THIRD_PARTY_DIR, resolve_space_weather_asset, resolve_spice_assets, resolve_wmm_asset,
)
from .space_weather import validate_space_weather_coverage
from .gravity_harmonics_asset import GRAVITY_CHOICES, inspect_ggm03s
from .igrf14_asset import verify_igrf14_assets

_SPICE_OWNER = threading.Lock()
DEFAULT_EPOCH = '2026-01-01T00:00:00Z'
KERNELS = ('de430.bsp', 'naif0012.tls', 'pck00010.tpc', 'gm_de431.tpc')


def parse_epoch(value: str) -> datetime:
    epoch = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if epoch.tzinfo is None:
        raise ValueError('ORBIT_EPOCH_REQUIRES_TIMEZONE')
    return epoch.astimezone(timezone.utc)


class NativeOrbitalEnvironment:
    def __init__(self, values: dict[str, Any], duration_s: float):
        self.profile = str(values.get('environment_profile', 'simplified'))
        if self.profile not in ('simplified', 'spice_wmm'):
            raise ValueError('NATIVE_ENVIRONMENT_PROFILE_INVALID')
        gravity_choice = values.get("earth_gravity_model", "legacy_j2_only")
        if type(gravity_choice) is not str or gravity_choice not in GRAVITY_CHOICES:
            raise ValueError("NATIVE_EARTH_GRAVITY_MODEL_UNSUPPORTED")
        self.gravity_model = gravity_choice
        self.gravity_degree = GRAVITY_CHOICES[gravity_choice]
        self.full_gravity = gravity_choice != "legacy_j2_only"
        self.gravity_asset: dict[str, Any] | None = None
        self.magnetic_field_model = values.get("magnetic_field_model", "wmm2025")
        if type(self.magnetic_field_model) is not str or self.magnetic_field_model not in ("wmm2025", "igrf14"):
            raise ValueError("NATIVE_MAGNETIC_FIELD_MODEL_UNSUPPORTED")
        if self.full_gravity and self.profile != "spice_wmm":
            raise ValueError("NATIVE_GGM03S_REQUIRES_SPICE_WMM")
        if self.magnetic_field_model == "igrf14" and self.profile != "spice_wmm":
            raise ValueError("NATIVE_IGRF14_REQUIRES_SPICE_WMM")
        self.igrf14_asset: dict[str, Any] | None = None
        self.epoch = parse_epoch(str(values.get('epoch_utc', DEFAULT_EPOCH)))
        a = float(values.get('orbit_radius_m', 7_000_000.))
        e = float(values.get('eccentricity', .001))
        if not (6_478_000. <= a <= 100_000_000. and 0. <= e < 1.) or a*(1.-e) <= 6_378_137.:
            raise ValueError('NATIVE_ORBIT_PERIGEE_INTERSECTS_EARTH_OR_INVALID_ELEMENTS')
        for key in ('raan_deg', 'arg_perigee_deg'):
            if not 0. <= float(values.get(key, 0.)) <= 360.:
                raise ValueError('NATIVE_ORBIT_ANGLE_INVALID')
        self.atmosphere_model = str(values.get('atmosphere_model', 'none')).strip().lower()
        if self.atmosphere_model not in ('none', 'nrlmsise00'):
            raise ValueError('NATIVE_ATMOSPHERE_MODEL_INVALID')
        self.drag_enabled = self.atmosphere_model == 'nrlmsise00'
        self.drag_area_m2 = float(values.get('drag_area_m2', 0.08))
        self.drag_coefficient = float(values.get('drag_coefficient', 2.2))
        if self.drag_enabled and (not (0.0 < self.drag_area_m2 <= 1000.0) or not (0.0 < self.drag_coefficient <= 10.0)):
            raise ValueError('NATIVE_DRAG_PARAMETERS_OUT_OF_RANGE')
        self.space_weather_asset = None
        self.space_weather_evidence: dict[str, Any] | None = None
        self.factory = None
        self.spice = None
        self.assets: dict[str, Any] = {}
        self._owns_spice = False
        if self.drag_enabled and self.profile != 'spice_wmm':
            raise ValueError('NRLMSISE00_REQUIRES_SPICE_WMM_ENVIRONMENT')
        if self.profile == 'spice_wmm':
            end = self.epoch + timedelta(seconds=duration_s)
            if self.epoch.year < 2025 or end >= datetime(2030, 1, 1, tzinfo=timezone.utc):
                raise ValueError('WMM2025_EPOCH_OUTSIDE_VALIDITY_2025_2030')
            kernels, _, _ = resolve_spice_assets(kernel_names=KERNELS, strict=True)
            self.assets = {'spice':[item.__dict__ for item in kernels]}
            if self.magnetic_field_model == "wmm2025":
                wmm, _ = resolve_wmm_asset(strict=True)
                self.assets["wmm"] = wmm.__dict__
            else:
                # No relabeling WMM coefficients as IGRF; verify the exact
                # official IAGA source and derived COF before physics starts.
                igrf_dir = Path(os.getenv('SAT_SIM_ASSET_ROOT', str(THIRD_PARTY_DIR))) / "igrf"
                self.igrf14_asset = verify_igrf14_assets(igrf_dir)
                self.assets["igrf14"] = dict(self.igrf14_asset)
            # Never raise degree on a sparse J2-only file; use the fixed,
            # separately verified full GGM03S release when explicitly chosen.
            gravity_dir = Path(os.getenv('SAT_SIM_ASSET_ROOT', str(THIRD_PARTY_DIR))) / "gravity"
            if self.full_gravity:
                gravity = gravity_dir / "GGM03S.txt"
                self.gravity_asset = inspect_ggm03s(gravity)
                self.assets["gravity_full"] = dict(self.gravity_asset)
            else:
                gravity = gravity_dir / "GGM03S-J2-only.txt"
                if not gravity.is_file():
                    raise FileNotFoundError("NATIVE_J2_GRAVITY_ASSET_MISSING")
            self.gravity_path = gravity.resolve()
            if self.drag_enabled:
                self.space_weather_asset, _ = resolve_space_weather_asset(strict=True)
                self.space_weather_evidence = validate_space_weather_coverage(
                    self.space_weather_asset.path, self.epoch, duration_s,
                )

    def __enter__(self):
        if self.profile == 'spice_wmm':
            # CSPICE owns process-global kernel state. Concurrent advanced
            # profiles must run in separate native worker processes.
            if not _SPICE_OWNER.acquire(blocking=False):
                raise RuntimeError('SPICE_PROCESS_ALREADY_OWNED_USE_SEPARATE_WORKER')
            self._owns_spice = True
        return self

    def __exit__(self, *_):
        try:
            if self.factory is not None:
                self.factory.unloadSpiceKernels()
        finally:
            if self._owns_spice:
                self._owns_spice = False
                _SPICE_OWNER.release()

    def configure_gravity(self, factory, earth, spacecraft, add):
        if self.profile == 'simplified':
            factory.addBodiesTo(spacecraft)
            return
        self.factory = factory
        factory.createSun()
        factory.createMoon()
        earth.useSphericalHarmonicsGravityModel(str(self.gravity_path), self.gravity_degree)
        factory.addBodiesTo(spacecraft)
        kernel_paths = [item['path'] for item in self.assets['spice']]
        self.spice = factory.createSpiceInterface(
            str(Path(kernel_paths[0]).parent)+os.sep,
            self.epoch.strftime('%Y %B %d %H:%M:%S.%f')+' (UTC)',
            spiceKernelFileNames=kernel_paths,
            epochInMsg=False,
        )
        if not self.spice.SPICELoaded:
            raise RuntimeError('NATIVE_SPICE_KERNEL_LOAD_FAILED')
        self.spice.zeroBase = 'earth'
        add(self.spice, 'spice_ephemeris', 'basilisk_official_native', 120)

    def ephemeris_messages(self):
        if self.spice is None:
            return None
        names = list(self.factory.gravBodies)
        return (self.spice.planetStateOutMsgs[names.index('sun')],
                self.spice.planetStateOutMsgs[names.index('earth')])

    def magnetic_model(self, earth_message):
        if self.profile == 'simplified':
            return None
        from Basilisk.simulation import magneticFieldWMM
        magnetic = magneticFieldWMM.MagneticFieldWMM()
        magnetic.wmmDataFullPath = (
            self.igrf14_asset["cof_path"] if self.igrf14_asset is not None
            else self.assets["wmm"]["path"]
        )
        year_start = datetime(self.epoch.year, 1, 1, tzinfo=timezone.utc)
        year_end = datetime(self.epoch.year+1, 1, 1, tzinfo=timezone.utc)
        magnetic.epochDateFractionalYear = self.epoch.year + (self.epoch-year_start)/(year_end-year_start)
        magnetic.planetPosInMsg.subscribeTo(earth_message)
        return magnetic

    def evidence(self):
        evidence = {'profile':self.profile, 'epoch_utc':self.epoch.isoformat(),
                    'ephemeris':'JPL_DE430_SPICE' if self.spice is not None else 'fixed_sun_reference',
                    'gravity':'earth_J2_plus_sun_moon' if self.spice is not None else 'earth_point_mass',
                    'magnetic_field':'WMM2025' if self.spice is not None else 'centered_dipole',
                    'atmosphere_model':'NRLMSISE-00' if self.drag_enabled else 'none',
                    'drag_enabled':self.drag_enabled,
                    'drag_model':'Basilisk_DragDynamicEffector' if self.drag_enabled else None,
                    'drag_area_m2':self.drag_area_m2 if self.drag_enabled else None,
                    'drag_coefficient':self.drag_coefficient if self.drag_enabled else None,
                    'space_weather':self.space_weather_evidence,
                    'assets':self.assets, 'spacecraft_flight_validation':False}
        if self.full_gravity:
            evidence.update({
                'gravity':f'earth_GGM03S_full_degree_{self.gravity_degree}_plus_sun_moon',
                'gravity_degree':self.gravity_degree,
                'gravity_model':self.gravity_model,
                'gravity_source':self.gravity_asset,
                'altitude_definition':'geocentric_radius_minus_gravity_source_reference_radius_m',
                'gravity_reference_frame':'Earth_planet_fixed_from_SPICE',
                'spherical_harmonic_scope':'full_degree_and_order_includes_tesseral_terms',
                'tide_evolution_modeled':False,
            })
        if self.igrf14_asset is not None:
            evidence.update({
                'magnetic_field':'IGRF14_2025_2030',
                'magnetic_field_model_id':'IGRF14_2025_2030',
                'magnetic_model_engine':'Basilisk.magneticFieldWMM_NOAA_MAG_with_IGRF14_COF',
                'magnetic_field_epoch_base_year':2025,
                'magnetic_field_valid_through_exclusive_year':2030,
                'magnetic_source_sha256':self.igrf14_asset['source_sha256'],
                'magnetic_derived_cof_sha256':self.igrf14_asset['cof_sha256'],
                'magnetic_main_degree':13,
                'magnetic_secular_variation_degree':8,
                'magnetic_source_is_WMM2025':False,
            })
        return evidence
