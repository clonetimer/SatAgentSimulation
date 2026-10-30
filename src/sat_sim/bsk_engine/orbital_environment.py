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
    THIRD_PARTY_DIR, resolve_spice_assets, resolve_wmm_asset,
)

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
        self.epoch = parse_epoch(str(values.get('epoch_utc', DEFAULT_EPOCH)))
        a = float(values.get('orbit_radius_m', 7_000_000.))
        e = float(values.get('eccentricity', .001))
        if not (6_478_000. <= a <= 100_000_000. and 0. <= e < 1.) or a*(1.-e) <= 6_378_137.:
            raise ValueError('NATIVE_ORBIT_PERIGEE_INTERSECTS_EARTH_OR_INVALID_ELEMENTS')
        for key in ('raan_deg', 'arg_perigee_deg'):
            if not 0. <= float(values.get(key, 0.)) <= 360.:
                raise ValueError('NATIVE_ORBIT_ANGLE_INVALID')
        self.factory = None
        self.spice = None
        self.assets: dict[str, Any] = {}
        self._owns_spice = False
        if self.profile == 'spice_wmm':
            end = self.epoch + timedelta(seconds=duration_s)
            if self.epoch.year < 2025 or end >= datetime(2030, 1, 1, tzinfo=timezone.utc):
                raise ValueError('WMM2025_EPOCH_OUTSIDE_VALIDITY_2025_2030')
            kernels, _, _ = resolve_spice_assets(kernel_names=KERNELS, strict=True)
            wmm, _ = resolve_wmm_asset(strict=True)
            self.assets = {'spice':[item.__dict__ for item in kernels], 'wmm':wmm.__dict__}
            # No arbitrary TaskSpec filesystem paths. Deployment may select an
            # audited asset root with the same resolver environment variables.
            gravity = Path(os.getenv('SAT_SIM_ASSET_ROOT', str(THIRD_PARTY_DIR)))/'gravity'/'GGM03S-J2-only.txt'
            if not gravity.is_file():
                raise FileNotFoundError('NATIVE_J2_GRAVITY_ASSET_MISSING')
            self.gravity_path = gravity.resolve()

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
        earth.useSphericalHarmonicsGravityModel(str(self.gravity_path), 2)
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
        magnetic.wmmDataFullPath = self.assets['wmm']['path']
        year_start = datetime(self.epoch.year, 1, 1, tzinfo=timezone.utc)
        year_end = datetime(self.epoch.year+1, 1, 1, tzinfo=timezone.utc)
        magnetic.epochDateFractionalYear = self.epoch.year + (self.epoch-year_start)/(year_end-year_start)
        magnetic.planetPosInMsg.subscribeTo(earth_message)
        return magnetic

    def evidence(self):
        return {'profile':self.profile, 'epoch_utc':self.epoch.isoformat(),
                'ephemeris':'JPL_DE430_SPICE' if self.spice is not None else 'fixed_sun_reference',
                'gravity':'earth_J2_plus_sun_moon' if self.spice is not None else 'earth_point_mass',
                'magnetic_field':'WMM2025' if self.spice is not None else 'centered_dipole',
                'assets':self.assets, 'spacecraft_flight_validation':False}
