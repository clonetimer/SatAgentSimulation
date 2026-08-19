"""Basilisk implementation for the v4.3 orbit/environment service layer.

This module provides:
1. Build functions that create Basilisk environment modules (sun, eclipse, magnetic field, gravity)
2. Graph container classes that hold created objects and messages
3. Attach functions that mount modules to Basilisk tasks

The reference service remains supportData-free; these builders create Basilisk-native
environment modules that can be used by other subsystems (ADCS, EPS, thermal).
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .schemas import BasiliskOrbEnvConfig


def basilisk_available() -> bool:
    try:
        import Basilisk  # noqa: F401
        return True
    except Exception:
        return False


from .assets import resolve_spice_assets, resolve_wmm_asset


def find_wmm_data_file(path: str | None = None) -> str | None:
    asset, _ = resolve_wmm_asset(path, strict=False)
    return asset.path if asset else None


def find_spice_data_files(path: str | None = None) -> tuple[str | None, str | None, str | None]:
    assets, _, _ = resolve_spice_assets(path, strict=False)
    by_name = {Path(asset.path).name: asset.path for asset in assets}
    de430 = next((value for name, value in by_name.items() if name.startswith("de43") and name.endswith(".bsp")), None)
    naif = next((value for name, value in by_name.items() if name.startswith("naif") and name.endswith(".tls")), None)
    pck = next((value for name, value in by_name.items() if name.startswith("pck") and name.endswith(".tpc")), None)
    return (de430, naif, pck)


@dataclass(frozen=True)
class OrbEnvBasiliskGraph:
    """Container for Basilisk orbit environment graph objects.

    Attributes
    ----------
    sun_msg : Any
        SpicePlanetStateMsg for sun position
    eclipse_msg : Any
        EclipseMsg for eclipse shadow factor
    mag_field_msg : Any
        MagneticFieldMsg for Earth magnetic field
    grav_factory : Any
        gravBodyFactory instance with Earth, Sun, Moon bodies
    eclipse_module : Any
        Basilisk eclipse.Eclipse module instance
    magnetic_field_module : Any
        Basilisk magneticFieldCenteredDipole or magneticFieldWMM instance
    spice_module : Any | None
        Basilisk spiceInterface.SpiceInterface instance (if SPICE model is used)
    """

    sun_msg: Any
    eclipse_msg: Any
    mag_field_msg: Any
    grav_factory: Any
    eclipse_module: Any
    magnetic_field_module: Any
    spice_module: Any | None = None
    planet_msgs: tuple[Any, ...] = field(default_factory=tuple)
    asset_manifest: dict[str, Any] = field(default_factory=dict)
    strict_resource_loading: bool = False


def _normalize_vector(v: tuple[float, float, float]) -> tuple[float, float, float]:
    from math import sqrt
    norm = sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)
    if norm <= 0.0:
        return (1.0, 0.0, 0.0)
    return (v[0] / norm, v[1] / norm, v[2] / norm)


def _build_spice_sun(sim: Any, cfg: "BasiliskOrbEnvConfig") -> tuple[Any, Any | None, dict[str, Any]]:
    """Build a SPICE-backed sun message or explicitly fall back to fixed sun.

    The Basilisk ``SpiceInterface`` constructor is called with
    ``auto_configure_kernels=False`` so the project never attempts runtime
    downloads.  All kernels must resolve from explicit paths or packaged
    ``third_party/spice`` assets when strict mode is enabled.
    """
    from Basilisk.architecture import messaging

    manifest: dict[str, Any] = {
        "sun_model": "spice",
        "strict_resource_loading": bool(cfg.strict_resource_loading),
        "spice_assets": [],
        "spice_missing": [],
        "spice_searched": [],
        "spice_loaded": False,
        "fallback": None,
    }

    try:
        assets, missing, searched = resolve_spice_assets(
            cfg.spice_data_path,
            cfg.spice_kernel_names,
            strict=bool(cfg.strict_resource_loading),
        )
        manifest["spice_assets"] = [asset.__dict__ for asset in assets]
        manifest["spice_missing"] = list(missing)
        manifest["spice_searched"] = list(searched)
        if missing:
            raise FileNotFoundError(f"missing SPICE kernels: {missing}")

        from Basilisk.simulation import spiceInterface

        spice_module = spiceInterface.SpiceInterface(auto_configure_kernels=False)
        spice_module.ModelTag = "orbitEnvironmentSpice"
        # Basilisk 2.11.0 still checks SPICEDataPath during Reset(), even
        # when individual kernels have been loaded explicitly.  Set it to the
        # common kernel directory so strict local runs do not fail at simulation
        # initialisation with "SPICE data path was not set".
        first_asset_dir = str(Path(assets[0].path).parent) + "/" if assets else ""
        spice_module.SPICEDataPath = first_asset_dir
        spice_module.addPlanetNames(list(cfg.spice_planet_names))
        spice_module.UTCCalInit = str(cfg.spice_epoch_utc)
        spice_module.zeroBase = str(cfg.spice_zero_base)
        spice_module.SPICELoaded = True
        for asset in assets:
            asset_path = Path(asset.path)
            load_failed = spice_module.loadSpiceKernel(asset_path.name, str(asset_path.parent) + "/")
            if load_failed:
                spice_module.SPICELoaded = False
                raise RuntimeError(f"SPICE kernel load failed: {asset_path}")
        if len(spice_module.planetStateOutMsgs) == 0:
            raise RuntimeError("SpiceInterface produced no planetStateOutMsgs")
        manifest["spice_loaded"] = True
        manifest["spice_planet_names"] = list(cfg.spice_planet_names)
        manifest["spice_zero_base"] = str(cfg.spice_zero_base)
        manifest["spice_epoch_utc"] = str(cfg.spice_epoch_utc)
        return spice_module.planetStateOutMsgs[0], spice_module, manifest

    except Exception as exc:
        if cfg.strict_resource_loading:
            raise RuntimeError(f"strict SPICE resource loading failed: {exc}") from exc
        sun_dir = _normalize_vector(cfg.sun_vector_n)
        sun_payload = messaging.SpicePlanetStateMsgPayload()
        sun_payload.PositionVector = [
            cfg.sun_distance_m * sun_dir[0],
            cfg.sun_distance_m * sun_dir[1],
            cfg.sun_distance_m * sun_dir[2],
        ]
        sun_payload.VelocityVector = [0.0, 0.0, 0.0]
        sun_payload.PlanetName = "sun"
        manifest["fallback"] = f"fixed_sun: {type(exc).__name__}: {exc}"
        return messaging.SpicePlanetStateMsg().write(sun_payload), None, manifest

def _build_fixed_sun(cfg: "BasiliskOrbEnvConfig") -> Any:
    from Basilisk.architecture import messaging

    sun_dir = _normalize_vector(cfg.sun_vector_n)
    sun_payload = messaging.SpicePlanetStateMsgPayload()
    sun_payload.PositionVector = [
        cfg.sun_distance_m * sun_dir[0],
        cfg.sun_distance_m * sun_dir[1],
        cfg.sun_distance_m * sun_dir[2],
    ]
    sun_payload.VelocityVector = [0.0, 0.0, 0.0]
    sun_payload.PlanetName = "sun"
    return messaging.SpicePlanetStateMsg().write(sun_payload)


def _build_eclipse(sim: Any, sun_msg: Any, sc_state_in_msg: Any, cfg: "BasiliskOrbEnvConfig") -> tuple[Any, Any]:
    from Basilisk.simulation import eclipse
    from Basilisk.architecture import messaging

    eclipse_module = eclipse.Eclipse()
    eclipse_module.ModelTag = "orbitEnvironmentEclipse"
    eclipse_module.sunInMsg.subscribeTo(sun_msg)
    eclipse_module.addSpacecraftToModel(sc_state_in_msg)

    earth_payload = messaging.SpicePlanetStateMsgPayload()
    earth_payload.PositionVector = [0.0, 0.0, 0.0]
    earth_payload.VelocityVector = [0.0, 0.0, 0.0]
    earth_payload.PlanetName = "earth"
    earth_msg = messaging.SpicePlanetStateMsg().write(earth_payload)
    eclipse_module.addPlanetToModel(earth_msg)
    # The returned graph keeps this static planet message alive for the lifetime
    # of the SWIG reader.  Attaching arbitrary attributes to Basilisk SWIG
    # modules is not portable across Basilisk versions.
    eclipse_module.rEqCustom = cfg.earth_radius_m

    return eclipse_module, earth_msg


def _build_magnetic_field(sc_state_in_msg: Any, cfg: "BasiliskOrbEnvConfig") -> tuple[Any, dict[str, Any]]:
    manifest: dict[str, Any] = {
        "magnetic_field_model": str(cfg.magnetic_field_model),
        "strict_resource_loading": bool(cfg.strict_resource_loading),
        "wmm_asset": None,
        "wmm_searched": [],
        "fallback": None,
    }
    if cfg.magnetic_field_model == "wmm":
        try:
            wmm_asset, searched = resolve_wmm_asset(
                cfg.wmm_data_path,
                strict=bool(cfg.strict_resource_loading),
            )
            manifest["wmm_asset"] = None if wmm_asset is None else wmm_asset.__dict__
            manifest["wmm_searched"] = list(searched)
            if wmm_asset is None:
                raise FileNotFoundError("WMM coefficient file not found")

            from Basilisk.simulation import magneticFieldWMM

            mag_field_module = magneticFieldWMM.MagneticFieldWMM()
            mag_field_module.ModelTag = "orbitEnvironmentMagWMM"
            mag_field_module.wmmDataFullPath = wmm_asset.path
            mag_field_module.addSpacecraftToModel(sc_state_in_msg)
            return mag_field_module, manifest

        except Exception as exc:
            if cfg.strict_resource_loading:
                raise RuntimeError(f"strict WMM resource loading failed: {exc}") from exc
            manifest["fallback"] = f"dipole: {type(exc).__name__}: {exc}"

    from Basilisk.simulation import magneticFieldCenteredDipole

    mag_field_module = magneticFieldCenteredDipole.MagneticFieldCenteredDipole()
    mag_field_module.ModelTag = "orbitEnvironmentMagDipole"

    dipole_moment_mag = cfg.magnetic_equator_strength_t * (cfg.earth_radius_m ** 3)
    mag_field_module.g10 = -dipole_moment_mag * cfg.magnetic_dipole_axis_n[2]
    mag_field_module.g11 = -dipole_moment_mag * cfg.magnetic_dipole_axis_n[0]
    mag_field_module.h11 = -dipole_moment_mag * cfg.magnetic_dipole_axis_n[1]
    mag_field_module.addSpacecraftToModel(sc_state_in_msg)

    return mag_field_module, manifest

def _build_gravity_field(cfg: "BasiliskOrbEnvConfig") -> Any:
    """Build gravity field using gravBodyFactory."""
    from Basilisk.utilities import simIncludeGravBody

    gravFactory = simIncludeGravBody.gravBodyFactory()

    earth = gravFactory.createEarth()
    earth.isCentralBody = True

    if cfg.use_j2_gravity:
        try:
            earth.createDefaultSphericalHarmonicGravityModel()
        except Exception as exc:
            record_runtime_diagnostic(
                code='GRAVITY_NATIVE_MAPPING_FAILED',
                category=DiagnosticCategory.NATIVE_MAPPING_FAILURE,
                location='src/integrated/orbit_environment/basilisk_impl.py:_build_gravity_field:01',
                exception=exc,
                strict=None,
            )

    # Do not add Sun/Moon gravity bodies in the support-data-free baseline.
    # In Basilisk, bodies created without valid SPICE/state ephemerides can
    # remain at the origin and introduce singular accelerations, which then
    # propagate NaN through the spacecraft and ADCS telemetry.  The baseline
    # integrated validation uses Earth-only gravity; third-body gravity should
    # be enabled only in a SPICE-backed profile with finite ephemerides.

    return gravFactory


def attach_gravity_factory_to_spacecraft(
    spacecraft: Any,
    gravFactory: Any,
) -> None:
    """Attach gravity factory bodies to a spacecraft.

    Parameters
    ----------
    spacecraft : Spacecraft
        Basilisk spacecraft instance
    gravFactory : gravBodyFactory
        Gravity body factory created by _build_gravity_field
    """
    gravFactory.addBodiesTo(spacecraft)


def build_orbital_environment_graph(
    sim: Any,
    sc_state_in_msg: Any,
    cfg: "BasiliskOrbEnvConfig | None" = None,
) -> OrbEnvBasiliskGraph:
    """Build Basilisk orbit environment modules and return the graph.

    Creates:
    1. Sun position message (SPICE or fixed)
    2. Eclipse module (eclipse.Eclipse)
    3. Magnetic field module (dipole or WMM)
    4. Gravity field (Earth with J2, Sun, Moon)

    Parameters
    ----------
    sim : SimulationBaseClass
        Basilisk simulation instance
    sc_state_in_msg : Any
        SCStatesMsg from the spacecraft module
    cfg : BasiliskOrbEnvConfig, optional
        Configuration for the environment modules

    Returns
    -------
    OrbEnvBasiliskGraph
        Container with all created objects and messages
    """
    if not basilisk_available():
        raise RuntimeError("Basilisk is not available")

    cfg = cfg or BasiliskOrbEnvConfig()

    if cfg.sun_model == "spice":
        sun_msg, spice_module, spice_manifest = _build_spice_sun(sim, cfg)
    else:
        sun_msg = _build_fixed_sun(cfg)
        spice_module = None
        spice_manifest = {
            "sun_model": "fixed",
            "strict_resource_loading": bool(cfg.strict_resource_loading),
            "spice_loaded": False,
            "fallback": None,
        }

    if cfg.enable_eclipse:
        eclipse_module, earth_msg = _build_eclipse(sim, sun_msg, sc_state_in_msg, cfg)
        eclipse_msg = eclipse_module.eclipseOutMsgs[0]
        eclipse_planet_msgs = (earth_msg,)
    else:
        eclipse_module = None
        eclipse_msg = None
        eclipse_planet_msgs = ()

    magnetic_field_module, magnetic_manifest = _build_magnetic_field(sc_state_in_msg, cfg)
    gravity_bodies = _build_gravity_field(cfg)
    asset_manifest = {
        "schema_version": "orbit-environment-assets-v1",
        "strict_resource_loading": bool(cfg.strict_resource_loading),
        "spice": spice_manifest,
        "magnetic_field": magnetic_manifest,
    }

    return OrbEnvBasiliskGraph(
        sun_msg=sun_msg,
        eclipse_msg=eclipse_msg,
        mag_field_msg=magnetic_field_module.envOutMsgs[0],
        grav_factory=gravity_bodies,
        eclipse_module=eclipse_module,
        magnetic_field_module=magnetic_field_module,
        spice_module=spice_module,
        planet_msgs=eclipse_planet_msgs,
        asset_manifest=asset_manifest,
        strict_resource_loading=bool(cfg.strict_resource_loading),
    )


def attach_orbital_environment_graph_to_task(
    sim: Any,
    task_name: str,
    graph: OrbEnvBasiliskGraph,
) -> None:
    """Attach environment modules to a Basilisk task.

    Parameters
    ----------
    sim : SimulationBaseClass
        Basilisk simulation instance
    task_name : str
        Name of the task to attach to
    graph : OrbEnvBasiliskGraph
        Environment graph created by build_orbital_environment_graph
    """
    if graph.spice_module is not None:
        sim.AddModelToTask(task_name, graph.spice_module)
    if graph.eclipse_module is not None:
        sim.AddModelToTask(task_name, graph.eclipse_module)
    sim.AddModelToTask(task_name, graph.magnetic_field_module)


def attach_gravity_bodies_to_spacecraft(
    spacecraft: Any,
    gravity_bodies: list[Any],
) -> None:
    """Attach gravity bodies to a spacecraft's GravityEffector.

    Parameters
    ----------
    spacecraft : Spacecraft
        Basilisk spacecraft instance
    gravity_bodies : list[GravBodyData]
        List of gravity bodies to attach
    """
    for body in gravity_bodies:
        spacecraft.gravField.addGravBody(body)


def run_basilisk_environment_smoke() -> dict:
    """Check key Basilisk orbit/environment modules are importable/constructible."""

    if not basilisk_available():
        return {
            "backend": "basilisk",
            "available": False,
            "status": "skipped",
            "reason": "Basilisk is not available",
        }
    modules: list[str] = []
    messages: list[str] = []
    errors: list[str] = []
    try:
        from Basilisk.simulation import spacecraft

        _ = spacecraft.Spacecraft()
        modules.append("spacecraft.Spacecraft")
    except Exception as exc:  # pragma: no cover - version-specific
        errors.append(f"spacecraft.Spacecraft: {exc}")
    try:
        from Basilisk.simulation import groundLocation

        _ = groundLocation.GroundLocation()
        modules.append("groundLocation.GroundLocation")
    except Exception as exc:  # pragma: no cover
        errors.append(f"groundLocation.GroundLocation: {exc}")
    try:
        from Basilisk.simulation import eclipse

        _ = eclipse.Eclipse()
        modules.append("eclipse.Eclipse")
    except Exception as exc:  # pragma: no cover
        errors.append(f"eclipse.Eclipse: {exc}")
    try:
        from Basilisk.simulation import magneticFieldWMM

        _ = magneticFieldWMM.MagneticFieldWMM()
        modules.append("magneticFieldWMM.MagneticFieldWMM")
    except Exception as exc:  # pragma: no cover
        errors.append(f"magneticFieldWMM.MagneticFieldWMM: {exc}")
    try:
        from Basilisk.simulation import magneticFieldCenteredDipole

        _ = magneticFieldCenteredDipole.MagneticFieldCenteredDipole()
        modules.append("magneticFieldCenteredDipole.MagneticFieldCenteredDipole")
    except Exception as exc:  # pragma: no cover
        errors.append(f"magneticFieldCenteredDipole.MagneticFieldCenteredDipole: {exc}")
    try:
        from Basilisk.simulation import gravityEffector

        _ = gravityEffector.GravBodyData()
        modules.append("gravityEffector.GravBodyData")
    except Exception as exc:  # pragma: no cover
        errors.append(f"gravityEffector: {exc}")
    try:
        from Basilisk.simulation import spiceInterface

        _ = spiceInterface.SpiceInterface()
        modules.append("spiceInterface.SpiceInterface")
    except Exception as exc:  # pragma: no cover
        errors.append(f"spiceInterface.SpiceInterface: {exc}")
    try:
        from Basilisk.architecture import messaging

        for name in ("SpicePlanetStateMsg", "SCStatesMsg", "EclipseMsg", "MagneticFieldMsg", "AccessMsg"):
            if hasattr(messaging, name):
                messages.append(name)
    except Exception as exc:  # pragma: no cover
        errors.append(f"messaging: {exc}")

    wmm_file = find_wmm_data_file()
    spice_files = find_spice_data_files()
    data_info = {
        "wmm_file_available": wmm_file is not None,
        "spice_de430_available": spice_files[0] is not None,
        "spice_naif_available": spice_files[1] is not None,
        "spice_pck_available": spice_files[2] is not None,
    }

    return {
        "backend": "basilisk",
        "available": True,
        "status": "smoke_complete" if not errors else "smoke_with_errors",
        "modules": modules,
        "messages": messages,
        "errors": errors,
        "data_files": data_info,
    }
