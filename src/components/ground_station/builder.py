"""ground_station component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import GroundStationConfig, GroundAccessNativeConfig
from .faults import apply_ground_station_faults
from .faults import FaultSpec

import csv
import json
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable

from ..dynamic_models import v3, dot, norm, unit


_messaging = None
_sysModel = None

try:
    from Basilisk.architecture import messaging, sysModel
    _messaging = messaging
    _sysModel = sysModel
except ImportError as exc:
    record_runtime_diagnostic(
        code='OPTIONAL_DEPENDENCY_IMPORT_UNAVAILABLE',
        category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
        location='src/components/ground_station/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class GroundAccessResult:
    has_access: bool
    elevation_deg: float
    slant_range_m: float


@dataclass(frozen=True)
class GroundAccessProfileResult:
    has_access: tuple
    elevation_deg: tuple


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def compute_ground_access(station, sc, c):
    st = v3(station)
    sv = v3(sc)
    rho = [sv[i] - st[i] for i in range(3)]
    r = norm(rho)
    up = unit(st)
    from math import asin, pi
    elev = asin(max(-1, min(1, dot(rho, up) / max(r, 1e-12)))) * 180 / pi
    return GroundAccessResult(elev >= c.min_elevation_deg and r <= c.max_range_m, elev, r)


def simulate_access_profile(station, profile, c):
    vals = [compute_ground_access(station, x, c) for x in profile]
    return GroundAccessProfileResult(tuple(v.has_access for v in vals), tuple(v.elevation_deg for v in vals))


def _build_nominal_ground_station_config_base_impl(min_elevation_deg: float = 0.0, max_range_m: float = 2_000_000.0, fault_specs: list[FaultSpec] | None = None) -> GroundStationConfig:
    """Build a nominal GroundStationConfig with default values.

    Args:
        min_elevation_deg: Minimum elevation angle in degrees
        max_range_m: Maximum range in meters

    Returns:
        GroundStationConfig: Nominal ground station configuration
    """
    return GroundStationConfig(
        min_elevation_deg=min_elevation_deg,
        max_range_m=max_range_m,
    )




@dataclass(frozen=True)
class GroundAccessTraceRow:
    time_s: float
    has_access: int
    slant_range_m: float
    range_rate_m_s: float


@dataclass(frozen=True)
class GroundAccessSummary:
    backend: str
    component: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    native_modules: tuple[str, ...]
    sample_count: int
    access_count: int
    min_slant_range_m: float
    max_slant_range_m: float
    status: str


def require_basilisk_ground_location() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
        from Basilisk.simulation import groundLocation  # noqa: F401
        from Basilisk.architecture import messaging  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk ground location modules are unavailable: {exc}") from exc


def build_ground_location(
    model_tag: str = "componentGroundLocation",
    config: GroundAccessNativeConfig | None = None,
):
    require_basilisk_ground_location()
    from Basilisk.simulation import groundLocation

    cfg = config or GroundAccessNativeConfig()
    gs = groundLocation.GroundLocation()
    gs.ModelTag = model_tag
    gs.planetRadius = float(cfg.planet_radius_m)
    gs.maximumRange = float(cfg.maximum_range_m)
    gs.minimumElevation = float(cfg.minimum_elevation_rad)
    gs.specifyLocation(float(cfg.ground_lat_rad), float(cfg.ground_lon_rad), float(cfg.ground_alt_m))
    return gs


def run_ground_access_native_case(config: GroundAccessNativeConfig | None = None) -> tuple[GroundAccessSummary, tuple[GroundAccessTraceRow, ...]]:
    cfg = config or GroundAccessNativeConfig()
    if cfg.duration_s <= 0:
        raise ValueError("duration_s must be positive")
    if cfg.step_s <= 0:
        raise ValueError("step_s must be positive")
    if cfg.planet_radius_m <= 0:
        raise ValueError("planet_radius_m must be positive")
    if cfg.spacecraft_radius_m <= cfg.planet_radius_m:
        raise ValueError("spacecraft_radius_m must be greater than planet_radius_m")
    if cfg.minimum_elevation_rad < -1.57 or cfg.minimum_elevation_rad > 1.57:
        raise ValueError("minimum_elevation_rad must be between -pi/2 and pi/2")
    if cfg.maximum_range_m <= 0:
        raise ValueError("maximum_range_m must be positive")
    require_basilisk_ground_location()
    from Basilisk.utilities import SimulationBaseClass, macros
    from Basilisk.architecture import messaging

    sim = SimulationBaseClass.SimBaseClass()
    proc = sim.CreateNewProcess("groundAccessComponentNativeProcess")
    task_name = "groundAccessComponentNativeTask"
    proc.addTask(sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s))))

    gs = build_ground_location("componentGroundAccess", cfg)
    sc = messaging.SCStatesMsgPayload()
    sc.r_BN_N = [float(cfg.spacecraft_radius_m), 0.0, 0.0]
    orbital_speed = (3.986e14 / cfg.spacecraft_radius_m) ** 0.5
    sc.v_BN_N = [0.0, orbital_speed, 0.0]
    sc.sigma_BN = [0.0, 0.0, 0.0]
    sc.omega_BN_B = [0.0, 0.0, 0.0]
    sc_msg = messaging.SCStatesMsg().write(sc)
    gs.addSpacecraftToModel(sc_msg)
    sim.AddModelToTask(task_name, gs)
    rec = gs.accessOutMsgs[0].recorder(macros.sec2nano(float(cfg.step_s)))
    sim.AddModelToTask(task_name, rec)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    rows = []
    for idx, t_ns in enumerate(list(rec.times())):
        rows.append(GroundAccessTraceRow(
            time_s=float(t_ns) * macros.NANO2SEC,
            has_access=int(rec.hasAccess[idx]),
            slant_range_m=float(rec.slantRange[idx]),
            range_rate_m_s=float(rec.range_dot[idx]),
        ))
    ranges = [r.slant_range_m for r in rows]
    summary = GroundAccessSummary(
        backend="basilisk_native_component",
        component="ground_station",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        native_modules=("groundLocation.GroundLocation", "SCStatesMsg", "AccessMsg"),
        sample_count=len(rows),
        access_count=sum(r.has_access for r in rows),
        min_slant_range_m=min(ranges) if ranges else 0.0,
        max_slant_range_m=max(ranges) if ranges else 0.0,
        status="PASS" if rows else "FAIL",
    )
    return summary, tuple(rows)


def write_ground_access_native_dataset(output_dir: str | Path, config: GroundAccessNativeConfig | None = None) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows = run_ground_access_native_case(config)
    summary_path = output_dir / "ground_access_native_summary.json"
    trace_path = output_dir / "ground_access_native_trace.csv"
    manifest_path = output_dir / "ground_access_native_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(GroundAccessTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "basilisk_native_component_ground_access",
        "backend_truth": "Basilisk groundLocation component runner",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}

# Component fault/degradation compatibility wrappers
from .degradation import GroundStationDegradation, GroundStationDegradationRate
from .degradation import apply_ground_station_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_ground_station_config_base = _build_nominal_ground_station_config_base_impl

def build_nominal_ground_station_config(
    *args,
    degradation: GroundStationDegradation | None = None,
    degradation_rate: GroundStationDegradationRate | None = None,
    years_elapsed: float = 0.0,
    fault_specs: list[_ComponentFaultSpec] | None = None,
    **kwargs,
):
    """Build config with canonical degradation-rate, degradation-state and fault support."""
    if "degradation" in kwargs:
        degradation = kwargs.pop("degradation")
    if "degradation_rate" in kwargs:
        degradation_rate = kwargs.pop("degradation_rate")
    if "years_elapsed" in kwargs:
        years_elapsed = kwargs.pop("years_elapsed")
    if "fault_specs" in kwargs:
        fault_specs = kwargs.pop("fault_specs")
    config = _build_nominal_ground_station_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_ground_station_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_ground_station_faults(config, fault_specs)
    return config

