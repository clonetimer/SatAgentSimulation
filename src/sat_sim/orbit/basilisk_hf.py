"""Basilisk-native orbit adapter support for BSK-ORB-1.

This module is intentionally split into two layers:

* deterministic TaskSpec parsing / configuration-blueprint construction that is
  testable without Basilisk installed;
* optional Basilisk execution helpers that import ``Basilisk`` only at runtime.

BSK-ORB-1 does not replace the existing local ORB-1/ORB-2 numerical proxy.  It
adds an explicit Basilisk-required orbit backend so the Agent catalog can tell
``Basilisk-native`` apart from ``local_physics_proxy``.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping
import math
import os
from pathlib import Path

from sat_sim.frames import vector_norm
from sat_sim.orbit.medium import EARTH_MU_M3_S2, EARTH_RADIUS_M

BASILISK_ORBIT_HF_SCHEMA_VERSION = "bsk_orb1.basilisk_orbit_hf.v1"
BASILISK_REQUIRED_VERSION_NOTE = "Accepted runtime identities are official bsk==2.11.0 or project metadata-only bsk==2.11.0+satfix1; runtime import and distribution identity are verified separately from offline Wheel availability"
SUPPORTED_CENTRAL_BODIES = {"earth", "mars"}
DEFAULT_EPOCH_UTC = "2026-01-01T00:00:00Z"
DEFAULT_J2_COEFFICIENT_RELATIVE_PATH = "supportData/LocalGravData/GGM03S-J2-only.csv"
DEFAULT_SPICE_EPHEMERIS_RELATIVE_PATH = "supportData/EphemerisData"
DEFAULT_SPICE_KERNELS = ("de430.bsp", "naif0012.tls", "de-403-masses.tpc", "pck00010.tpc")
ALTERNATE_SPICE_KERNELS = ("de430.bsp", "naif0012.tls", "gm_de431.tpc", "pck00010.tpc")
SPICE_KERNEL_ALTERNATIVES = {
    "gm_de431.tpc": ("gm_de431.tpc", "de-403-masses.tpc", "gm_de440.tpc"),
    "de-403-masses.tpc": ("de-403-masses.tpc", "gm_de431.tpc", "gm_de440.tpc"),
    "gm_de440.tpc": ("gm_de440.tpc", "gm_de431.tpc", "de-403-masses.tpc"),
}
DEFAULT_THIRD_BODIES = ("sun", "moon")
DEFAULT_BSK_RUN4_REFERENCE_ALTITUDE_M = 300_000.0
DEFAULT_BSK_RUN4_REFERENCE_DENSITY_KG_M3 = 2.0e-10
DEFAULT_BSK_RUN4_SCALE_HEIGHT_M = 50_000.0
DEFAULT_SUN_POSITION_ECI_M = (149_597_870_700.0, 0.0, 0.0)
SPICE_BODY_NAME_ALIASES = {
    "earth": "earth",
    "sun": "sun",
    "moon": "moon",
    "mars": "mars barycenter",
    "mars barycenter": "mars barycenter",
    "jupiter": "jupiter barycenter",
    "jupiter barycenter": "jupiter barycenter",
}


class BasiliskOrbitHfError(ValueError):
    """Raised when a BSK-ORB-1 configuration is invalid."""


class BasiliskOrbitHfRuntimeUnavailable(RuntimeError):
    """Raised when a requested Basilisk runtime path is not executable in this environment."""

    def __init__(self, reason: str, details: Mapping[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


@dataclass(frozen=True)
class BasiliskAvailability:
    """Runtime availability report for the optional Basilisk dependency."""

    available: bool
    checked_modules: tuple[str, ...]
    missing_modules: tuple[str, ...] = ()
    package_path: str | None = None
    package_version: str | None = None
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "checked_modules": list(self.checked_modules),
            "missing_modules": list(self.missing_modules),
            "package_path": self.package_path,
            "package_version": self.package_version,
            "error": self.error,
            "version_note": BASILISK_REQUIRED_VERSION_NOTE,
        }


@dataclass(frozen=True)
class SpiceKernelBundle:
    """Resolved SPICE ephemeris data directory and kernel file availability."""

    available: bool
    data_path: str | None
    requested_kernels: tuple[str, ...]
    resolved_kernels: tuple[str, ...] = ()
    missing_kernels: tuple[str, ...] = ()
    substituted_kernels: Mapping[str, str] = field(default_factory=dict)
    searched_paths: tuple[str, ...] = ()
    source: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "data_path": self.data_path,
            "requested_kernels": list(self.requested_kernels),
            "resolved_kernels": list(self.resolved_kernels),
            "missing_kernels": list(self.missing_kernels),
            "substituted_kernels": dict(self.substituted_kernels),
            "searched_paths": list(self.searched_paths),
            "source": self.source,
        }


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise BasiliskOrbitHfError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise BasiliskOrbitHfError(f"{name} must be positive")
    return out


def _nonnegative(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out < 0.0:
        raise BasiliskOrbitHfError(f"{name} must be non-negative")
    return out


def _deg(value: Any, name: str, default: float) -> float:
    raw = default if value is None else value
    out = _finite(raw, name)
    return out


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _string_list(value: Any, *, default: Iterable[str] = ()) -> tuple[str, ...]:
    if value is None:
        return tuple(str(x) for x in default)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (bytes, bytearray, Mapping)):
        out = tuple(str(x) for x in value if str(x).strip())
        return out
    raise BasiliskOrbitHfError("expected a string or list of strings")


def _repo_root() -> Path:
    """Return the repository root for bundled runtime support files."""

    return Path(__file__).resolve().parents[3]


def _candidate_coefficient_paths(coefficient_file: str | None) -> tuple[Path, ...]:
    """Return candidate paths for a spherical-harmonics coefficient file."""

    candidates: list[Path] = []
    if coefficient_file:
        raw = Path(coefficient_file).expanduser()
        candidates.append(raw if raw.is_absolute() else Path.cwd() / raw)
        candidates.append(raw if raw.is_absolute() else _repo_root() / raw)
    else:
        candidates.append(_repo_root() / DEFAULT_J2_COEFFICIENT_RELATIVE_PATH)
        try:
            import Basilisk  # type: ignore

            if getattr(Basilisk, "__path__", None):
                bsk_root = Path(str(Basilisk.__path__[0]))
                candidates.extend([
                    bsk_root / "supportData" / "LocalGravData" / "GGM03S-J2-only.txt",
                    bsk_root / "supportData" / "LocalGravData" / "GGM03S-J2-only.csv",
                ])
        except Exception as exc:
            record_runtime_diagnostic(
                code='BASILISK_SUPPORT_DATA_PATH_PROBE_FAILED',
                category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
                location='src/sat_sim/orbit/basilisk_hf.py:_candidate_coefficient_paths:01',
                exception=exc,
                strict=False,
            )
    unique: list[Path] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key not in seen:
            unique.append(candidate)
            seen.add(key)
    return tuple(unique)


def resolve_spherical_harmonics_coefficient_file(config: "SphericalHarmonicsConfig", *, central_body: str = "earth") -> str:
    """Resolve a Basilisk JPL-format spherical-harmonics coefficient file.

    A project-owned or Basilisk support-data coefficient file is required when
    spherical harmonics are explicitly enabled.  The default 6-DOF recovery
    task uses central gravity so an absent coefficient file cannot be silently
    misrepresented as a J2 run.
    """

    if not config.enabled:
        raise BasiliskOrbitHfError("spherical harmonics are not enabled")
    body = str(central_body).strip().lower()
    if not config.coefficient_file and body != "earth":
        raise BasiliskOrbitHfError(
            "no bundled default spherical-harmonics coefficient file is available "
            f"for central_body={central_body!r}; provide spherical_harmonics.coefficient_file explicitly"
        )
    candidates = _candidate_coefficient_paths(config.coefficient_file)
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return str(candidate.resolve())
    searched = ", ".join(str(path) for path in candidates)
    raise BasiliskOrbitHfError(
        "spherical_harmonics.coefficient_file could not be resolved; "
        f"searched: {searched}"
    )



def _candidate_spice_data_paths(explicit_path: str | None) -> tuple[tuple[Path, str], ...]:
    """Return candidate SPICE ephemeris directories with source labels."""

    candidates: list[tuple[Path, str]] = []
    if explicit_path:
        raw = Path(explicit_path).expanduser()
        candidates.append((raw if raw.is_absolute() else Path.cwd() / raw, "task_spec"))
        candidates.append((raw if raw.is_absolute() else _repo_root() / raw, "task_spec_repo_relative"))
    env_path = os.environ.get("BASILISK_SPICE_DATA_PATH") or os.environ.get("BSK_SPICE_DATA_PATH")
    if env_path:
        candidates.append((Path(env_path).expanduser(), "environment"))
    candidates.append((_repo_root() / DEFAULT_SPICE_EPHEMERIS_RELATIVE_PATH, "repo_support_data"))
    try:
        import Basilisk  # type: ignore

        if getattr(Basilisk, "__path__", None):
            bsk_root = Path(str(Basilisk.__path__[0]))
            candidates.append((bsk_root / "supportData" / "EphemerisData", "basilisk_support_data"))
    except Exception as exc:
        record_runtime_diagnostic(
            code='BASILISK_SUPPORT_DATA_PATH_PROBE_FAILED',
            category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
            location='src/sat_sim/orbit/basilisk_hf.py:_candidate_spice_data_paths:01',
            exception=exc,
            strict=False,
        )

    unique: list[tuple[Path, str]] = []
    seen: set[str] = set()
    for path, source in candidates:
        key = str(path)
        if key not in seen:
            unique.append((path, source))
            seen.add(key)
    return tuple(unique)


def _requested_spice_kernels(config: "BasiliskOrbitForceModelConfig") -> tuple[str, ...]:
    kernels = tuple(str(item).strip() for item in config.spice_kernels if str(item).strip())
    return kernels or DEFAULT_SPICE_KERNELS


def resolve_spice_kernel_bundle(config: "BasiliskOrbitHfConfig | BasiliskOrbitForceModelConfig") -> SpiceKernelBundle:
    """Resolve SPICE kernel availability for BSK-RUN-3 third-body runtime.

    The repository intentionally does not fabricate or download ephemeris kernels.
    A runtime pass requires real files under either ``force_models.spice_data_path``,
    an environment-provided path, the repository supportData tree, or Basilisk's
    own supportData tree if present.
    """

    force = config.force_models if isinstance(config, BasiliskOrbitHfConfig) else config
    requested = _requested_spice_kernels(force)
    candidates = _candidate_spice_data_paths(force.spice_data_path)
    # If the TaskSpec explicitly names a SPICE data path, treat that path as
    # authoritative.  This preserves the no-implicit-fallback guardrail: a missing
    # or incomplete explicit kernel directory must not be silently satisfied by
    # repository or Basilisk supportData discovered later on the search path.
    if force.spice_data_path:
        candidates = tuple((path, source) for path, source in candidates if source.startswith("task_spec"))
    searched: list[str] = []
    for data_path, source in candidates:
        searched.append(str(data_path))
        if not data_path.exists() or not data_path.is_dir():
            continue
        resolved: list[str] = []
        missing: list[str] = []
        substituted: dict[str, str] = {}
        for kernel in requested:
            kernel_path = Path(kernel).expanduser()
            names_to_try: tuple[str, ...]
            if kernel_path.is_absolute():
                candidates_for_kernel = (kernel_path,)
            else:
                names_to_try = SPICE_KERNEL_ALTERNATIVES.get(kernel, (kernel,))
                candidates_for_kernel = tuple(data_path / name for name in names_to_try)
            match = next((candidate for candidate in candidates_for_kernel if candidate.exists() and candidate.is_file()), None)
            if match is not None:
                resolved.append(str(match.resolve()))
                if not kernel_path.is_absolute() and match.name != kernel:
                    substituted[kernel] = match.name
            else:
                missing.append(kernel)
        if not missing:
            return SpiceKernelBundle(
                available=True,
                data_path=str(data_path.resolve()),
                requested_kernels=requested,
                resolved_kernels=tuple(resolved),
                missing_kernels=(),
                substituted_kernels=substituted,
                searched_paths=tuple(searched),
                source=source,
            )
    return SpiceKernelBundle(
        available=False,
        data_path=None,
        requested_kernels=requested,
        resolved_kernels=(),
        missing_kernels=requested,
        substituted_kernels={},
        searched_paths=tuple(searched),
        source=None,
    )


def _spice_body_name(name: str) -> str:
    body = str(name).strip().lower()
    return SPICE_BODY_NAME_ALIASES.get(body, body)


def _basilisk_spice_time_string(epoch_utc: str) -> str:
    """Convert ISO-like UTC input into a Basilisk/SPICE-friendly time string."""

    raw = str(epoch_utc or DEFAULT_EPOCH_UTC).strip()
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        # Allow already-SPICE-like strings from advanced users.
        return raw
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    dt = dt.astimezone(timezone.utc)
    millis = int(round(dt.microsecond / 1000.0))
    if millis >= 1000:
        dt = dt.replace(microsecond=0)
        millis = 0
    return dt.strftime("%Y %B %d %H:%M:%S") + f".{millis:03d} (UTC)"


def _add_model_to_task(sc_sim: Any, task_name: str, model: Any, priority: int | None = None) -> None:
    """Add a Basilisk model while tolerating minor AddModelToTask signature drift."""

    if priority is None:
        sc_sim.AddModelToTask(task_name, model)
        return
    try:
        sc_sim.AddModelToTask(task_name, model, priority)
    except TypeError:
        sc_sim.AddModelToTask(task_name, model, None, priority)


@dataclass(frozen=True)
class SphericalHarmonicsConfig:
    """Basilisk spherical-harmonics gravity model options."""

    enabled: bool = False
    degree: int = 2
    coefficient_file: str | None = None

    @classmethod
    def from_mapping(cls, payload: Any) -> "SphericalHarmonicsConfig":
        if isinstance(payload, bool):
            return cls(enabled=payload, degree=2, coefficient_file=None)
        data = payload if isinstance(payload, Mapping) else {}
        enabled = _bool(data.get("enabled"), False)
        degree = int(data.get("degree", data.get("max_degree", 2)))
        if degree < 2 or degree > 100:
            raise BasiliskOrbitHfError("spherical_harmonics.degree must be within [2, 100] for BSK-ORB-1")
        coeff = data.get("coefficient_file") or data.get("gravity_file")
        return cls(enabled=enabled, degree=degree, coefficient_file=str(coeff) if coeff else None)

    def to_dict(self) -> dict[str, Any]:
        return {"enabled": self.enabled, "degree": self.degree, "coefficient_file": self.coefficient_file}


@dataclass(frozen=True)
class BasiliskOrbitForceModelConfig:
    """Basilisk-native force/environment configuration requested by a TaskSpec."""

    central_body: str = "earth"
    spherical_harmonics: SphericalHarmonicsConfig = field(default_factory=SphericalHarmonicsConfig)
    third_bodies: tuple[str, ...] = ()
    spice_ephemeris: bool = False
    spice_kernels: tuple[str, ...] = ()
    spice_data_path: str | None = None
    spice_zero_base: str = "Earth"
    atmosphere_model: str | None = None
    drag_enabled: bool = False
    srp_enabled: bool = False
    gravity_only_execution: bool = True

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskOrbitForceModelConfig":
        orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
        force = orbit.get("force_models") if isinstance(orbit.get("force_models"), Mapping) else {}
        central_body = str(orbit.get("central_body", force.get("central_body", "earth"))).strip().lower()
        if central_body not in SUPPORTED_CENTRAL_BODIES:
            raise BasiliskOrbitHfError(f"central_body must be one of {sorted(SUPPORTED_CENTRAL_BODIES)}")
        sph = SphericalHarmonicsConfig.from_mapping(force.get("spherical_harmonics"))
        if not sph.enabled and _bool(force.get("high_order_gravity"), False):
            sph = SphericalHarmonicsConfig(enabled=True, degree=int(force.get("gravity_degree", 2)), coefficient_file=None)
        third_bodies = _string_list(force.get("third_bodies"), default=())
        if _bool(force.get("third_body"), False) and not third_bodies:
            third_bodies = ("sun", "moon")
        third_bodies = tuple(x.strip().lower() for x in third_bodies if x.strip())
        spice = _bool(force.get("spice_ephemeris"), False) or bool(third_bodies)
        spice_kernels = _string_list(force.get("spice_kernels"), default=())
        spice_data_path_raw = force.get("spice_data_path") or force.get("spice_kernel_dir") or force.get("ephemeris_data_path")
        spice_zero_base = str(force.get("spice_zero_base") or ("Earth" if central_body == "earth" else central_body)).strip() or "Earth"
        atmosphere = force.get("atmosphere_model") or force.get("atmosphere") or orbit.get("atmosphere_model")
        atmosphere_model = str(atmosphere).strip().lower() if atmosphere else None
        drag_enabled = _bool(force.get("drag"), False) or atmosphere_model is not None
        srp_enabled = _bool(force.get("srp"), False) or _bool(force.get("solar_radiation_pressure"), False)
        gravity_only = not (third_bodies or spice or drag_enabled or srp_enabled)
        return cls(
            central_body=central_body,
            spherical_harmonics=sph,
            third_bodies=third_bodies,
            spice_ephemeris=spice,
            spice_kernels=spice_kernels,
            spice_data_path=str(spice_data_path_raw).strip() if spice_data_path_raw else None,
            spice_zero_base=spice_zero_base,
            atmosphere_model=atmosphere_model,
            drag_enabled=drag_enabled,
            srp_enabled=srp_enabled,
            gravity_only_execution=gravity_only,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "central_body": self.central_body,
            "spherical_harmonics": self.spherical_harmonics.to_dict(),
            "third_bodies": list(self.third_bodies),
            "spice_ephemeris": self.spice_ephemeris,
            "spice_kernels": list(self.spice_kernels),
            "spice_data_path": self.spice_data_path,
            "spice_zero_base": self.spice_zero_base,
            "atmosphere_model": self.atmosphere_model,
            "drag_enabled": self.drag_enabled,
            "srp_enabled": self.srp_enabled,
            "gravity_only_execution": self.gravity_only_execution,
        }


@dataclass(frozen=True)
class BasiliskExponentialAtmosphereConfig:
    """Exponential atmosphere settings used by the BSK-RUN-4 drag smoke.

    Basilisk's ``ExponentialAtmosphere`` stores ``baseDensity`` at the planet
    radius, while TaskSpecs usually reason about a reference density at a LEO
    reference altitude.  ``base_density_kg_m3`` performs that conversion without
    hiding the approximation.
    """

    reference_altitude_m: float = DEFAULT_BSK_RUN4_REFERENCE_ALTITUDE_M
    reference_density_kg_m3: float = DEFAULT_BSK_RUN4_REFERENCE_DENSITY_KG_M3
    scale_height_m: float = DEFAULT_BSK_RUN4_SCALE_HEIGHT_M
    min_reach_m: float = 100_000.0
    max_reach_m: float = 1_000_000.0
    local_temp_k: float = 1000.0

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskExponentialAtmosphereConfig":
        orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        data: Mapping[str, Any] = {}
        if isinstance(orbit.get("atmosphere"), Mapping):
            data = orbit["atmosphere"]  # type: ignore[assignment]
        elif isinstance(params.get("atmosphere"), Mapping):
            data = params["atmosphere"]  # type: ignore[assignment]
        return cls(
            reference_altitude_m=_nonnegative(data.get("reference_altitude_m", DEFAULT_BSK_RUN4_REFERENCE_ALTITUDE_M), "atmosphere.reference_altitude_m"),
            reference_density_kg_m3=_nonnegative(data.get("reference_density_kg_m3", DEFAULT_BSK_RUN4_REFERENCE_DENSITY_KG_M3), "atmosphere.reference_density_kg_m3"),
            scale_height_m=_positive(data.get("scale_height_m", DEFAULT_BSK_RUN4_SCALE_HEIGHT_M), "atmosphere.scale_height_m"),
            min_reach_m=_nonnegative(data.get("min_reach_m", data.get("env_min_reach_m", 100_000.0)), "atmosphere.min_reach_m"),
            max_reach_m=_positive(data.get("max_reach_m", data.get("env_max_reach_m", 1_000_000.0)), "atmosphere.max_reach_m"),
            local_temp_k=_positive(data.get("local_temp_k", 1000.0), "atmosphere.local_temp_k"),
        )

    @property
    def base_density_kg_m3(self) -> float:
        if self.reference_density_kg_m3 <= 0.0:
            return 0.0
        return self.reference_density_kg_m3 * math.exp(self.reference_altitude_m / self.scale_height_m)

    def to_dict(self) -> dict[str, float]:
        return {
            "reference_altitude_m": self.reference_altitude_m,
            "reference_density_kg_m3": self.reference_density_kg_m3,
            "scale_height_m": self.scale_height_m,
            "base_density_kg_m3": self.base_density_kg_m3,
            "min_reach_m": self.min_reach_m,
            "max_reach_m": self.max_reach_m,
            "local_temp_k": self.local_temp_k,
        }


@dataclass(frozen=True)
class BasiliskOrbitInitialState:
    """Classical-orbital-elements initial state for Basilisk setup."""

    semi_major_axis_m: float
    eccentricity: float = 0.0
    inclination_deg: float = 51.6
    raan_deg: float = 0.0
    arg_perigee_deg: float = 0.0
    true_anomaly_deg: float = 0.0

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskOrbitInitialState":
        orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
        altitude_m = _finite(orbit.get("altitude_m", 500_000.0), "orbit_environment.altitude_m")
        if altitude_m < 120_000.0 or altitude_m > 36_000_000.0:
            raise BasiliskOrbitHfError("orbit_environment.altitude_m must be within [120000, 36000000] m for BSK-ORB-1")
        semi = orbit.get("semi_major_axis_m")
        a = _positive(semi, "orbit_environment.semi_major_axis_m") if semi is not None else EARTH_RADIUS_M + altitude_m
        ecc = _finite(orbit.get("eccentricity", 0.0), "orbit_environment.eccentricity")
        if ecc < 0.0 or ecc >= 0.95:
            raise BasiliskOrbitHfError("orbit_environment.eccentricity must be within [0, 0.95)")
        perigee = a * (1.0 - ecc)
        if perigee < EARTH_RADIUS_M + 100_000.0:
            raise BasiliskOrbitHfError("orbit perigee altitude must exceed 100 km")
        inc = _deg(orbit.get("inclination_deg"), "orbit_environment.inclination_deg", 51.6)
        if inc < 0.0 or inc > 180.0:
            raise BasiliskOrbitHfError("orbit_environment.inclination_deg must be within [0, 180]")
        return cls(
            semi_major_axis_m=a,
            eccentricity=ecc,
            inclination_deg=inc,
            raan_deg=_deg(orbit.get("raan_deg"), "orbit_environment.raan_deg", 0.0),
            arg_perigee_deg=_deg(orbit.get("arg_perigee_deg"), "orbit_environment.arg_perigee_deg", 0.0),
            true_anomaly_deg=_deg(orbit.get("true_anomaly_deg"), "orbit_environment.true_anomaly_deg", 0.0),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "semi_major_axis_m": self.semi_major_axis_m,
            "eccentricity": self.eccentricity,
            "inclination_deg": self.inclination_deg,
            "raan_deg": self.raan_deg,
            "arg_perigee_deg": self.arg_perigee_deg,
            "true_anomaly_deg": self.true_anomaly_deg,
        }


@dataclass(frozen=True)
class BasiliskSpacecraftOrbitProperties:
    """Spacecraft properties relevant to orbit environment effectors."""

    mass_kg: float = 12.0
    drag_area_m2: float = 0.08
    drag_coefficient: float = 2.2
    srp_area_m2: float = 0.08
    srp_coefficient: float = 1.3

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskSpacecraftOrbitProperties":
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        sc = params.get("spacecraft") if isinstance(params.get("spacecraft"), Mapping) else {}
        return cls(
            mass_kg=_positive(sc.get("mass_kg", 12.0), "parameters.spacecraft.mass_kg"),
            drag_area_m2=_nonnegative(sc.get("drag_area_m2", sc.get("area_m2", 0.08)), "parameters.spacecraft.drag_area_m2"),
            drag_coefficient=_nonnegative(sc.get("drag_coefficient", 2.2), "parameters.spacecraft.drag_coefficient"),
            srp_area_m2=_nonnegative(sc.get("srp_area_m2", sc.get("area_m2", 0.08)), "parameters.spacecraft.srp_area_m2"),
            srp_coefficient=_nonnegative(sc.get("srp_coefficient", 1.3), "parameters.spacecraft.srp_coefficient"),
        )

    def to_dict(self) -> dict[str, float]:
        return {
            "mass_kg": self.mass_kg,
            "drag_area_m2": self.drag_area_m2,
            "drag_coefficient": self.drag_coefficient,
            "srp_area_m2": self.srp_area_m2,
            "srp_coefficient": self.srp_coefficient,
        }


@dataclass(frozen=True)
class BasiliskOrbitHfConfig:
    """Complete BSK-ORB-1 orbit adapter configuration."""

    duration_s: float
    sample_s: float
    epoch_utc: str
    initial_state: BasiliskOrbitInitialState
    force_models: BasiliskOrbitForceModelConfig
    spacecraft: BasiliskSpacecraftOrbitProperties
    atmosphere: BasiliskExponentialAtmosphereConfig = field(default_factory=BasiliskExponentialAtmosphereConfig)
    task_id: str = "orbit_environment_basilisk_hf_task"

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskOrbitHfConfig":
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        duration_s = _positive(sim.get("duration_s", 600.0), "simulation.duration_s")
        sample_s = _positive(sim.get("sample_s", 10.0), "simulation.sample_s")
        if sample_s > duration_s:
            raise BasiliskOrbitHfError("simulation.sample_s must not exceed simulation.duration_s")
        return cls(
            duration_s=duration_s,
            sample_s=sample_s,
            epoch_utc=str(sim.get("epoch_utc") or DEFAULT_EPOCH_UTC),
            initial_state=BasiliskOrbitInitialState.from_task_spec(spec),
            force_models=BasiliskOrbitForceModelConfig.from_task_spec(spec),
            spacecraft=BasiliskSpacecraftOrbitProperties.from_task_spec(spec),
            atmosphere=BasiliskExponentialAtmosphereConfig.from_task_spec(spec),
            task_id=str(spec.get("task_id") or "orbit_environment_basilisk_hf_task"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BASILISK_ORBIT_HF_SCHEMA_VERSION,
            "task_id": self.task_id,
            "duration_s": self.duration_s,
            "sample_s": self.sample_s,
            "epoch_utc": self.epoch_utc,
            "initial_state": self.initial_state.to_dict(),
            "force_models": self.force_models.to_dict(),
            "spacecraft": self.spacecraft.to_dict(),
            "atmosphere": self.atmosphere.to_dict(),
        }


def check_basilisk_availability(config: BasiliskOrbitHfConfig | None = None) -> BasiliskAvailability:
    """Return optional Basilisk dependency availability without raising."""

    checked = [
        "Basilisk",
        "Basilisk.simulation.spacecraft",
        "Basilisk.utilities.SimulationBaseClass",
        "Basilisk.utilities.macros",
        "Basilisk.utilities.orbitalMotion",
        "Basilisk.utilities.simIncludeGravBody",
    ]
    optional: list[str] = []
    if config and config.force_models.spice_ephemeris:
        optional.extend(["Basilisk.simulation.spiceInterface", "Basilisk.topLevelModules.pyswice"])
    if config and config.force_models.drag_enabled:
        optional.extend(["Basilisk.simulation.exponentialAtmosphere", "Basilisk.simulation.dragDynamicEffector"])
    if config and config.force_models.srp_enabled:
        optional.append("Basilisk.simulation.radiationPressure")
    checked.extend(optional)

    try:
        import Basilisk  # type: ignore
        from Basilisk.simulation import spacecraft  # type: ignore  # noqa: F401
        from Basilisk.utilities import SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody  # type: ignore  # noqa: F401
        for name in optional:
            __import__(name)
        package_path = None
        if getattr(Basilisk, "__path__", None):
            package_path = str(Basilisk.__path__[0])
        version = getattr(Basilisk, "__version__", None)
        return BasiliskAvailability(True, checked_modules=tuple(checked), package_path=package_path, package_version=str(version) if version else None)
    except Exception as exc:
        missing = tuple(name for name in checked if name.startswith("Basilisk"))
        return BasiliskAvailability(False, checked_modules=tuple(checked), missing_modules=missing, error=f"{type(exc).__name__}: {exc}")


def build_basilisk_orbit_blueprint(config: BasiliskOrbitHfConfig) -> dict[str, Any]:
    """Build an auditable Basilisk configuration blueprint for BSK-ORB-1."""

    force = config.force_models
    modules = [
        "SimulationBaseClass.SimBaseClass",
        "spacecraft.Spacecraft",
        "simIncludeGravBody.gravBodyFactory",
        f"gravFactory.create{force.central_body.capitalize() if force.central_body != 'earth' else 'Earth'}",
    ]
    if force.spherical_harmonics.enabled:
        modules.append("GravBodyData.useSphericalHarmonicsGravityModel")
    if force.spice_ephemeris:
        modules.extend(["gravBodyFactory.createBodies", "gravBodyFactory.createSpiceInterface", "spiceInterface.SpiceInterface", "pyswice.furnsh_c"])
    if force.drag_enabled:
        modules.extend(["exponentialAtmosphere.ExponentialAtmosphere", "dragDynamicEffector.DragDynamicEffector"])
    if force.srp_enabled:
        modules.extend(["radiationPressure.RadiationPressure", "messaging.SpicePlanetStateMsg", "messaging.EclipseMsg"])
    modules.extend(["orbitalMotion.ClassicElements", "orbitalMotion.elem2rv", "scStateOutMsg.recorder"])
    spice_resolution: dict[str, Any] | None = None
    if force.spice_ephemeris:
        spice_resolution = resolve_spice_kernel_bundle(config).to_dict()
    if force.gravity_only_execution:
        execution_scope = "spherical_harmonics_runtime_supported" if force.spherical_harmonics.enabled else "central_gravity_runtime_supported"
    elif force.spice_ephemeris and not force.drag_enabled and not force.srp_enabled:
        if spice_resolution and spice_resolution.get("available"):
            execution_scope = "spice_third_body_runtime_supported"
        else:
            execution_scope = "spice_third_body_runtime_unavailable_missing_kernels"
    elif force.spice_ephemeris and (force.drag_enabled or force.srp_enabled):
        execution_scope = "combined_spice_drag_srp_runtime_unavailable_bsk_run4_scope"
    elif force.drag_enabled or force.srp_enabled:
        scopes: list[str] = []
        if force.drag_enabled:
            scopes.append("exponential_atmosphere_drag_runtime_supported")
        if force.srp_enabled:
            scopes.append("cannonball_srp_runtime_supported")
        execution_scope = "+".join(scopes)
    else:
        execution_scope = "configured_native_extension_pending_runtime_benchmark"
    coefficient_resolution: dict[str, Any] | None = None
    if force.spherical_harmonics.enabled:
        try:
            coefficient_resolution = {
                "status": "resolved",
                "path": resolve_spherical_harmonics_coefficient_file(force.spherical_harmonics, central_body=force.central_body),
            }
        except Exception as exc:
            coefficient_resolution = {"status": "unresolved", "error": str(exc)}
    return {
        "schema_version": BASILISK_ORBIT_HF_SCHEMA_VERSION,
        "backend_type": "basilisk_native",
        "basilisk_required": True,
        "execution_scope": execution_scope,
        "configured_modules": modules,
        "initial_state_source": "classical_orbital_elements",
        "state_logging": {
            "message": "spacecraft.scStateOutMsg",
            "fields": ["r_BN_N", "v_BN_N", "times", "drag.forceExternal_B", "srp.forceExternal_N", "atmosphere.neutralDensity"],
            "sample_s": config.sample_s,
        },
        "force_models": force.to_dict(),
        "atmosphere": config.atmosphere.to_dict(),
        "spherical_harmonics_coefficient_resolution": coefficient_resolution,
        "spice_kernel_resolution": spice_resolution,
        "spacecraft": config.spacecraft.to_dict(),
        "claim_guardrail": "BSK-RUN-4 can execute atmosphere/drag/SRP smoke paths when Basilisk is present; this is still not flight validation, external truth correlation, or package-level high-fidelity certification.",
        "fallback_policy": "Do not silently fall back to orbit_environment.orbit_fidelity.v1 for this capability. Use the local proxy explicitly for comparison baselines.",
    }


def build_bsk_orb1_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return the normalized BSK-ORB-1 configuration payload."""

    config = BasiliskOrbitHfConfig.from_task_spec(spec)
    return {
        "schema_version": BASILISK_ORBIT_HF_SCHEMA_VERSION,
        "config": config.to_dict(),
        "availability": check_basilisk_availability(config).to_dict(),
        "blueprint": build_basilisk_orbit_blueprint(config),
    }


def run_basilisk_orbit_hf_if_available(config: BasiliskOrbitHfConfig) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Execute supported Basilisk orbit paths when Basilisk is installed.

    BSK-RUN-1/2 support central-body gravity plus optional Earth J2 spherical
    harmonics.  BSK-RUN-3 adds an executable SPICE/third-body path for Sun/Moon
    point-mass gravity, but only when a real SPICE kernel bundle is present.  If
    kernels or required modules are missing, the function raises
    ``BasiliskOrbitHfRuntimeUnavailable`` rather than fabricating a pass.
    BSK-RUN-4 adds an Earth-centered LEO smoke path for Basilisk's exponential
    atmosphere, drag dynamic effector, and cannonball SRP dynamic effector.
    """

    availability = check_basilisk_availability(config)
    if not availability.available:
        raise BasiliskOrbitHfRuntimeUnavailable("Basilisk is not available", availability.to_dict())
    if (config.force_models.drag_enabled or config.force_models.srp_enabled) and config.force_models.spice_ephemeris:
        raise BasiliskOrbitHfRuntimeUnavailable(
            "combined SPICE third-body plus drag/SRP runtime is outside BSK-RUN-4 smoke scope",
            {"force_models": config.force_models.to_dict()},
        )
    if (config.force_models.drag_enabled or config.force_models.srp_enabled) and config.force_models.central_body != "earth":
        raise BasiliskOrbitHfRuntimeUnavailable(
            "BSK-RUN-4 drag/SRP smoke currently supports Earth-centered cases only",
            {"central_body": config.force_models.central_body},
        )
    if config.force_models.central_body != "earth" and config.force_models.spice_ephemeris:
        raise RuntimeError("BSK-RUN-3 SPICE third-body runtime currently supports Earth-centered cases only")

    # Imports are intentionally local so importing sat_sim remains dependency-light.
    from Basilisk.simulation import spacecraft  # type: ignore
    from Basilisk.utilities import SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody  # type: ignore
    if config.force_models.drag_enabled:
        from Basilisk.simulation import dragDynamicEffector, exponentialAtmosphere  # type: ignore
    else:
        dragDynamicEffector = None  # type: ignore[assignment]
        exponentialAtmosphere = None  # type: ignore[assignment]
    if config.force_models.srp_enabled:
        from Basilisk.architecture import messaging  # type: ignore
        from Basilisk.simulation import radiationPressure  # type: ignore
    else:
        messaging = None  # type: ignore[assignment]
        radiationPressure = None  # type: ignore[assignment]

    sim_task_name = "bsk_run3_orbit_task" if config.force_models.spice_ephemeris else "bsk_orb1_orbit_task"
    sim_process_name = "bsk_run3_orbit_process" if config.force_models.spice_ephemeris else "bsk_orb1_orbit_process"
    sc_sim = SimulationBaseClass.SimBaseClass()
    dyn_process = sc_sim.CreateNewProcess(sim_process_name)
    step_ns = macros.sec2nano(config.sample_s)
    dyn_process.addTask(sc_sim.CreateNewTask(sim_task_name, step_ns))

    sc_object = spacecraft.Spacecraft()
    sc_object.ModelTag = "bsk-run3-spice-third-body-sat" if config.force_models.spice_ephemeris else "bsk-orb1-sat"
    sc_object.hub.mHub = config.spacecraft.mass_kg
    inertia_scale = max(config.spacecraft.mass_kg, 1.0)
    sc_object.hub.IHubPntBc_B = [[inertia_scale, 0.0, 0.0], [0.0, inertia_scale, 0.0], [0.0, 0.0, inertia_scale]]
    _add_model_to_task(sc_sim, sim_task_name, sc_object, priority=0 if config.force_models.spice_ephemeris else None)

    grav_factory = simIncludeGravBody.gravBodyFactory()
    planet: Any
    spice_bundle: SpiceKernelBundle | None = None
    spice_time = _basilisk_spice_time_string(config.epoch_utc)
    spice_loaded_kernel_paths: list[str] = []
    spice_body_names: tuple[str, ...] = ()
    spice_unload = None
    resolved_spherical_harmonics_file: str | None = None

    if config.force_models.spice_ephemeris:
        spice_bundle = resolve_spice_kernel_bundle(config)
        if not spice_bundle.available:
            raise BasiliskOrbitHfRuntimeUnavailable("SPICE kernel bundle is unavailable", spice_bundle.to_dict())
        requested_bodies = [config.force_models.central_body, *config.force_models.third_bodies]
        spice_body_names = tuple(dict.fromkeys(_spice_body_name(body) for body in requested_bodies))
        if "earth" not in spice_body_names:
            spice_body_names = ("earth", *spice_body_names)
        grav_bodies = grav_factory.createBodies(list(spice_body_names))
        planet = grav_bodies["earth"]
        planet.isCentralBody = True
        if config.force_models.spherical_harmonics.enabled:
            resolved_spherical_harmonics_file = resolve_spherical_harmonics_coefficient_file(config.force_models.spherical_harmonics, central_body=config.force_models.central_body)
            planet.useSphericalHarmonicsGravityModel(resolved_spherical_harmonics_file, config.force_models.spherical_harmonics.degree)
        grav_factory.addBodiesTo(sc_object)
        # Pass absolute resolved kernel paths so Basilisk does not fall back to its
        # built-in/default kernel discovery path.
        # BSK-RUN-6 accepts gm_de431.tpc as a GM constants substitute when de-403-masses.tpc is absent.
        spice_object = grav_factory.createSpiceInterface(
            str(Path(spice_bundle.data_path or ".").resolve()) + os.sep,
            spice_time,
            spiceKernelFileNames=list(spice_bundle.resolved_kernels),
            epochInMsg=False,
        )
        spice_object.zeroBase = config.force_models.spice_zero_base
        _add_model_to_task(sc_sim, sim_task_name, spice_object, priority=1)
        try:
            from Basilisk.topLevelModules import pyswice  # type: ignore
            for kernel_path in spice_bundle.resolved_kernels:
                pyswice.furnsh_c(str(kernel_path))
                spice_loaded_kernel_paths.append(str(kernel_path))
            spice_unload = getattr(pyswice, "unload_c", None)
        except Exception as exc:
            raise BasiliskOrbitHfRuntimeUnavailable("pyswice kernel load failed", {"error": f"{type(exc).__name__}: {exc}", "spice_kernel_resolution": spice_bundle.to_dict()}) from exc
    else:
        if config.force_models.central_body == "mars":
            planet = grav_factory.createMarsBarycenter()
        else:
            planet = grav_factory.createEarth()
        planet.isCentralBody = True
        if config.force_models.spherical_harmonics.enabled:
            resolved_spherical_harmonics_file = resolve_spherical_harmonics_coefficient_file(config.force_models.spherical_harmonics, central_body=config.force_models.central_body)
            planet.useSphericalHarmonicsGravityModel(resolved_spherical_harmonics_file, config.force_models.spherical_harmonics.degree)
        grav_factory.addBodiesTo(sc_object)

    oe = orbitalMotion.ClassicElements()
    oe.a = config.initial_state.semi_major_axis_m
    oe.e = config.initial_state.eccentricity
    oe.i = config.initial_state.inclination_deg * macros.D2R
    oe.Omega = config.initial_state.raan_deg * macros.D2R
    oe.omega = config.initial_state.arg_perigee_deg * macros.D2R
    oe.f = config.initial_state.true_anomaly_deg * macros.D2R
    r_n, v_n = orbitalMotion.elem2rv(planet.mu, oe)
    sc_object.hub.r_CN_NInit = r_n
    sc_object.hub.v_CN_NInit = v_n

    atmo_model: Any | None = None
    atmo_rec: Any | None = None
    drag_effector: Any | None = None
    drag_log: Any | None = None
    srp_effector: Any | None = None
    srp_log: Any | None = None
    sun_msg: Any | None = None
    eclipse_msg: Any | None = None

    if config.force_models.drag_enabled:
        atmo_cfg = config.atmosphere
        atmo_model = exponentialAtmosphere.ExponentialAtmosphere()  # type: ignore[union-attr]
        atmo_model.ModelTag = "bsk-run4-exponential-atmosphere"
        atmo_model.planetRadius = EARTH_RADIUS_M
        atmo_model.baseDensity = atmo_cfg.base_density_kg_m3
        atmo_model.scaleHeight = atmo_cfg.scale_height_m
        atmo_model.localTemp = atmo_cfg.local_temp_k
        atmo_model.envMinReach = atmo_cfg.min_reach_m
        atmo_model.envMaxReach = atmo_cfg.max_reach_m
        atmo_model.addSpacecraftToModel(sc_object.scStateOutMsg)
        _add_model_to_task(sc_sim, sim_task_name, atmo_model)

        drag_effector = dragDynamicEffector.DragDynamicEffector()  # type: ignore[union-attr]
        drag_effector.ModelTag = "bsk-run4-drag-effector"
        drag_params = dragDynamicEffector.DragBaseData()  # type: ignore[union-attr]
        drag_params.projectedArea = config.spacecraft.drag_area_m2
        drag_params.dragCoeff = config.spacecraft.drag_coefficient
        drag_params.comOffset = [0.0, 0.0, 0.0]
        drag_effector.coreParams = drag_params
        drag_effector.atmoDensInMsg.subscribeTo(atmo_model.envOutMsgs[0])
        sc_object.addDynamicEffector(drag_effector)
        _add_model_to_task(sc_sim, sim_task_name, drag_effector)

    if config.force_models.srp_enabled:
        srp_effector = radiationPressure.RadiationPressure()  # type: ignore[union-attr]
        srp_effector.ModelTag = "bsk-run4-cannonball-srp"
        srp_effector.area = config.spacecraft.srp_area_m2
        srp_effector.coefficientReflection = config.spacecraft.srp_coefficient
        srp_effector.setUseCannonballModel()
        sun_payload = messaging.SpicePlanetStateMsgPayload()  # type: ignore[union-attr]
        sun_payload.PositionVector = list(DEFAULT_SUN_POSITION_ECI_M)
        sun_payload.VelocityVector = [0.0, 0.0, 0.0]
        sun_payload.PlanetName = "sun"
        sun_msg = messaging.SpicePlanetStateMsg().write(sun_payload)  # type: ignore[union-attr]
        srp_effector.sunEphmInMsg.subscribeTo(sun_msg)
        eclipse_payload = messaging.EclipseMsgPayload()  # type: ignore[union-attr]
        eclipse_payload.illuminationFactor = 1.0
        eclipse_msg = messaging.EclipseMsg().write(eclipse_payload)  # type: ignore[union-attr]
        srp_effector.sunEclipseInMsg.subscribeTo(eclipse_msg)
        sc_object.addDynamicEffector(srp_effector)
        _add_model_to_task(sc_sim, sim_task_name, srp_effector)

    data_rec = sc_object.scStateOutMsg.recorder(step_ns)
    _add_model_to_task(sc_sim, sim_task_name, data_rec)
    if atmo_model is not None:
        atmo_rec = atmo_model.envOutMsgs[0].recorder(step_ns)
        _add_model_to_task(sc_sim, sim_task_name, atmo_rec)
    if drag_effector is not None:
        drag_log = drag_effector.logger(["forceExternal_B", "forceExternal_N", "v_B", "v_hat_B"], step_ns)
        _add_model_to_task(sc_sim, sim_task_name, drag_log)
    if srp_effector is not None:
        srp_log = srp_effector.logger(["forceExternal_N"], step_ns)
        _add_model_to_task(sc_sim, sim_task_name, srp_log)
    try:
        sc_sim.InitializeSimulation()
        sc_sim.ConfigureStopTime(macros.sec2nano(config.duration_s))
        sc_sim.ExecuteSimulation()
    finally:
        if config.force_models.spice_ephemeris:
            try:
                grav_factory.unloadSpiceKernels()
            except Exception as exc:
                record_runtime_diagnostic(
                    code='SPICE_KERNEL_CLEANUP_FAILED',
                    category=DiagnosticCategory.BEST_EFFORT_CLEANUP,
                    location='src/sat_sim/orbit/basilisk_hf.py:run_basilisk_orbit_hf_if_available:01',
                    exception=exc,
                    strict=False,
                )
            if spice_unload is not None:
                for kernel_path in spice_loaded_kernel_paths:
                    try:
                        spice_unload(kernel_path)
                    except Exception as exc:
                        record_runtime_diagnostic(
                            code='SPICE_KERNEL_CLEANUP_FAILED',
                            category=DiagnosticCategory.BEST_EFFORT_CLEANUP,
                            location='src/sat_sim/orbit/basilisk_hf.py:run_basilisk_orbit_hf_if_available:02',
                            exception=exc,
                            strict=False,
                        )

    rows: list[dict[str, Any]] = []
    times = data_rec.times() * macros.NANO2SEC
    pos = data_rec.r_BN_N
    vel = data_rec.v_BN_N
    for idx, t_s in enumerate(times):
        r = tuple(float(x) for x in pos[idx])
        v = tuple(float(x) for x in vel[idx])
        row: dict[str, Any] = {
            "time_s": float(t_s),
            "orbit.r_bn_n_m_x": r[0],
            "orbit.r_bn_n_m_y": r[1],
            "orbit.r_bn_n_m_z": r[2],
            "orbit.v_bn_n_m_s_x": v[0],
            "orbit.v_bn_n_m_s_y": v[1],
            "orbit.v_bn_n_m_s_z": v[2],
            "orbit.radius_m": vector_norm(r),
            "orbit.speed_m_s": vector_norm(v),
            "backend_type": "basilisk_native",
            "spice_ephemeris_enabled": bool(config.force_models.spice_ephemeris),
            "environment.drag_enabled": bool(config.force_models.drag_enabled),
            "environment.srp_enabled": bool(config.force_models.srp_enabled),
        }
        if spice_body_names:
            row["environment.third_bodies"] = ";".join(body for body in spice_body_names if body != "earth")
        if atmo_rec is not None:
            density = float(atmo_rec.neutralDensity[idx])
            row["environment.atmosphere_density_kg_m3"] = density
        if drag_log is not None:
            drag_force_b = tuple(float(x) for x in drag_log.forceExternal_B[idx])
            drag_force_n = tuple(float(x) for x in drag_log.forceExternal_N[idx])
            row.update({
                "environment.drag_force_b_n_x": drag_force_b[0],
                "environment.drag_force_b_n_y": drag_force_b[1],
                "environment.drag_force_b_n_z": drag_force_b[2],
                "environment.drag_force_b_norm_n": vector_norm(drag_force_b),
                "environment.drag_force_n_n_x": drag_force_n[0],
                "environment.drag_force_n_n_y": drag_force_n[1],
                "environment.drag_force_n_n_z": drag_force_n[2],
                "environment.drag_force_n_norm_n": vector_norm(drag_force_n),
            })
        if srp_log is not None:
            srp_force_n = tuple(float(x) for x in srp_log.forceExternal_N[idx])
            row.update({
                "environment.srp_force_n_n_x": srp_force_n[0],
                "environment.srp_force_n_n_y": srp_force_n[1],
                "environment.srp_force_n_n_z": srp_force_n[2],
                "environment.srp_force_n_norm_n": vector_norm(srp_force_n),
            })
        rows.append(row)
    altitude_values = [row["orbit.radius_m"] - EARTH_RADIUS_M for row in rows]
    if config.force_models.spice_ephemeris:
        third_body_label = "_".join(body.replace(" ", "_") for body in config.force_models.third_bodies or DEFAULT_THIRD_BODIES)
        force_model_scope = f"spice_third_body_{third_body_label}"
    elif config.force_models.drag_enabled and config.force_models.srp_enabled:
        force_model_scope = "exponential_atmosphere_drag_plus_cannonball_srp"
    elif config.force_models.drag_enabled:
        force_model_scope = "exponential_atmosphere_drag"
    elif config.force_models.srp_enabled:
        force_model_scope = "cannonball_srp"
    elif config.force_models.spherical_harmonics.enabled:
        force_model_scope = f"spherical_harmonics_degree_{config.force_models.spherical_harmonics.degree}"
    else:
        force_model_scope = "central_gravity"
    densities = [float(row["environment.atmosphere_density_kg_m3"]) for row in rows if "environment.atmosphere_density_kg_m3" in row]
    drag_force_b_norms = [float(row["environment.drag_force_b_norm_n"]) for row in rows if "environment.drag_force_b_norm_n" in row]
    srp_force_norms = [float(row["environment.srp_force_n_norm_n"]) for row in rows if "environment.srp_force_n_norm_n" in row]
    summary = {
        "schema_version": BASILISK_ORBIT_HF_SCHEMA_VERSION,
        "basilisk_status": "executed",
        "backend_type": "basilisk_native",
        "force_model_scope": force_model_scope,
        "spherical_harmonics_coefficient_file": resolved_spherical_harmonics_file,
        "spice_kernel_resolution": spice_bundle.to_dict() if spice_bundle else None,
        "spice_epoch": spice_time if config.force_models.spice_ephemeris else None,
        "third_bodies": list(config.force_models.third_bodies),
        "spice_zero_base": config.force_models.spice_zero_base if config.force_models.spice_ephemeris else None,
        "atmosphere": config.atmosphere.to_dict() if config.force_models.drag_enabled else None,
        "drag_runtime": {
            "enabled": bool(config.force_models.drag_enabled),
            "model": "dragDynamicEffector.DragDynamicEffector" if config.force_models.drag_enabled else None,
            "force_frame_primary": "body_B",
        },
        "srp_runtime": {
            "enabled": bool(config.force_models.srp_enabled),
            "model": "radiationPressure.RadiationPressure.cannonball" if config.force_models.srp_enabled else None,
            "sun_ephemeris_source": "constant_1au_eci_smoke_message" if config.force_models.srp_enabled else None,
        },
        "trace_rows": len(rows),
        "qoi": {
            "orbit.altitude_min_m": min(altitude_values) if altitude_values else None,
            "orbit.altitude_max_m": max(altitude_values) if altitude_values else None,
            "orbit.altitude_delta_m": (altitude_values[-1] - altitude_values[0]) if len(altitude_values) >= 2 else 0.0,
            "environment.atmosphere_density_min_kg_m3": min(densities) if densities else None,
            "environment.atmosphere_density_max_kg_m3": max(densities) if densities else None,
            "environment.drag_force_b_norm_max_n": max(drag_force_b_norms) if drag_force_b_norms else None,
            "environment.srp_force_n_norm_max_n": max(srp_force_norms) if srp_force_norms else None,
            "environment.drag_accel_b_norm_max_m_s2": (max(drag_force_b_norms) / config.spacecraft.mass_kg) if drag_force_b_norms else None,
            "environment.srp_accel_n_norm_max_m_s2": (max(srp_force_norms) / config.spacecraft.mass_kg) if srp_force_norms else None,
        },
        "physical_validation_status": "not_flight_validated",
        "can_claim_high_fidelity": False,
    }
    return summary, tuple(rows)


__all__ = [
    "BASILISK_ORBIT_HF_SCHEMA_VERSION",
    "DEFAULT_SPICE_EPHEMERIS_RELATIVE_PATH",
    "DEFAULT_SPICE_KERNELS",
    "ALTERNATE_SPICE_KERNELS",
    "SPICE_KERNEL_ALTERNATIVES",
    "BasiliskAvailability",
    "BasiliskOrbitHfConfig",
    "BasiliskOrbitHfError",
    "BasiliskOrbitHfRuntimeUnavailable",
    "BasiliskOrbitForceModelConfig",
    "BasiliskOrbitInitialState",
    "BasiliskExponentialAtmosphereConfig",
    "BasiliskSpacecraftOrbitProperties",
    "SphericalHarmonicsConfig",
    "SpiceKernelBundle",
    "check_basilisk_availability",
    "build_basilisk_orbit_blueprint",
    "build_bsk_orb1_payload",
    "resolve_spherical_harmonics_coefficient_file",
    "resolve_spice_kernel_bundle",
    "run_basilisk_orbit_hf_if_available",
]
