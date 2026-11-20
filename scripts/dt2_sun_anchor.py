#!/usr/bin/env python3
"""DT-2 CROSS-LEO-001: real shared-SPK Sun/Luna frame-reference probe.

用途：在已执行 Basilisk/GMAT 的封存证据上，用同一官方 GMAT DE421 SPK、
      SPICE LSK 在 NAIF/真实 Basilisk SPICE 消息/真实 GMAT 中观测太阳、月球地心矢量。
参数：--output 已封存案例目录 --gmat-root 真实 GMAT R2026a 解包目录
      --gmat-executable 真实 GmatConsole-R2026a。
输出：真实 GMAT 太阳与月球原始矢量、NAIF/Basilisk 逐点对照、诊断和哈希凭证；
      不生成虚构的 GMAT 数据，不自动通过绝对 N 轴或飞行精度验收。
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

INDICES = (0, 45, 90)
AXES = ("X", "Y", "Z", "VX", "VY", "VZ")
FIELDS = tuple(
    [f"icrf_{k.lower()}" for k in AXES] +
    [f"mj_{k.lower()}" for k in AXES]
)


def digest(path: Path) -> str:
    h=hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda:f.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")



def verified_gmat_startup_kernels(root: Path, paths: dict[str, Path]) -> dict:
    """Verify R2026a startup file binds exactly the three packaged kernels.

    R2026a rejects legacy SolarSystem.LSKFilename script assignment. Its
    LSK_FILE, PLANETARY_SPK_FILE and PLANETARY_PCK_FILE live in the installed
    startup file; a mismatched or absent binding must fail before executing
    GMAT rather than silently falling back to another ephemeris.
    """
    startup = (root / "bin" / "gmat_startup_file.txt").resolve()
    if not startup.is_file():
        raise FileNotFoundError("DT2_SUN_ANCHOR_GMAT_STARTUP_MISSING")
    entries = {}
    for raw_line in startup.read_text(encoding="utf-8").splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key:
            entries[key] = value  # GMAT: the last active assignment wins.

    def resolved(key: str, ancestors: tuple[str, ...] = ()) -> Path:
        if key not in entries:
            raise ValueError("DT2_SUN_ANCHOR_STARTUP_FIELD_MISSING: " + key)
        if key in ancestors:
            raise ValueError("DT2_SUN_ANCHOR_STARTUP_PATH_CYCLE: " + key)
        value = entries[key].strip(" '\"")
        head, slash, tail = value.partition("/")
        if slash and head in entries:
            return (resolved(head, ancestors + (key,)) / tail).resolve()
        if slash and head.isidentifier() and head.upper() == head:
            raise ValueError("DT2_SUN_ANCHOR_STARTUP_UNRESOLVED_ALIAS: " + head)
        raw_path = Path(value)
        return (raw_path if raw_path.is_absolute()
                else startup.parent / raw_path).resolve()

    fields = {
        "spk": "PLANETARY_SPK_FILE",
        "lsk": "LSK_FILE",
        "pck": "PLANETARY_PCK_FILE",
    }
    observed = {name: resolved(key) for name, key in fields.items()}
    for name, actual in observed.items():
        if actual != paths[name].resolve():
            raise ValueError("DT2_SUN_ANCHOR_STARTUP_KERNEL_MISMATCH: " + name)
    return {
        "path": str(startup),
        "sha256": digest(startup),
        "kernel_paths": {name: str(path) for name, path in observed.items()},
    }

def sun_script(a1_mjd: list[float], *, report_path: Path,
               moon_report_path: Path | None = None) -> str:
    if len(a1_mjd)!=3 or not all(math.isfinite(t) for t in a1_mjd):
        raise ValueError("DT2_SUN_ANCHOR_REQUIRES_THREE_REAL_A1_EPOCHS")
    for a,b in zip(a1_mjd,a1_mjd[1:]):
        if b<=a:
            raise ValueError("DT2_SUN_ANCHOR_A1_EPOCH_NOT_INCREASING")
    def q(path: Path) -> str:
        name=str(path.resolve()).replace("\\","/")
        if "'" in name or not name.isascii():
            raise ValueError("DT2_SUN_ANCHOR_INVALID_GMAT_PATH")
        return name
    lines=[
        "% Real R2026a Sun geocentric ephemeris, not a fake spacecraft vector.",
        "GMAT SolarSystem.EphemerisSource = 'SPICE';",
        # R2026a configures its exact SPK/LSK/PCK via gmat_startup_file.txt;
        # assignments for these deprecated fields must NOT enter the script.
        "Create ReportFile SunAnchor;",
        f"SunAnchor.Filename = '{q(report_path)}';",
        "SunAnchor.Precision = 16;",
        "SunAnchor.WriteHeaders = false;",
        "SunAnchor.FixedWidth = false;",
        "SunAnchor.Delimiter = ',';",
        "SunAnchor.WriteReport = false;",
        "SunAnchor.AppendToExistingFile = false;",
    ]
    if moon_report_path is not None:
        # GMAT's built-in lunar CelestialBody is *Luna*, not "Moon".
        lines += [
            "Create ReportFile MoonAnchor;",
            f"MoonAnchor.Filename = '{q(moon_report_path)}';",
            "MoonAnchor.Precision = 16;",
            "MoonAnchor.WriteHeaders = false;",
            "MoonAnchor.FixedWidth = false;",
            "MoonAnchor.Delimiter = ',';",
            "MoonAnchor.WriteReport = false;",
            "MoonAnchor.AppendToExistingFile = false;",
        ]
    lines.append("BeginMissionSequence;")
    parameters=" ".join(
        [f"Sun.EarthICRF.{axis}" for axis in AXES]+
        [f"Sun.EarthMJ2000Eq.{axis}" for axis in AXES])
    moon_parameters = " ".join(
        [f"Luna.EarthICRF.{axis}" for axis in AXES]+
        [f"Luna.EarthMJ2000Eq.{axis}" for axis in AXES])
    for epoch in a1_mjd:
        lines += [
            f"Sun.Epoch.A1ModJulian = {epoch:.16g};",
            f"Report SunAnchor {parameters};",
        ]
        if moon_report_path is not None:
            lines += [
                f"Luna.Epoch.A1ModJulian = {epoch:.16g};",
                f"Report MoonAnchor {moon_parameters};",
            ]
    result="\n".join(lines)+"\n"
    if not result.isascii():
        raise ValueError("DT2_SUN_ANCHOR_NON_ASCII_GMAT_SCRIPT")
    return result


def _parse_gmat_celestial(path: Path, *, body: str,
                          minimum_km: float, maximum_km: float) -> list[list[float]]:
    """Read only an actual body ReportFile, never infer missing GMAT values."""
    prefix = "DT2_" + body.upper() + "_ANCHOR"
    result = []
    with path.open("r", encoding="utf-8-sig", newline="") as stream:
        for values in csv.reader(stream, delimiter=","):
            if not values or all(not part.strip() for part in values):
                continue
            if len(values) != 12:
                raise ValueError(prefix + "_WRONG_GMAT_WIDTH")
            try:
                numbers = [float(part.strip()) for part in values]
            except ValueError as ex:
                raise ValueError(prefix + "_BAD_GMAT_NUMBER") from ex
            if not all(math.isfinite(v) for v in numbers):
                raise ValueError(prefix + "_NONFINITE_GMAT_STATE")
            for idx in (0, 6):
                radius = math.sqrt(sum(v*v for v in numbers[idx:idx+3]))
                if not minimum_km < radius < maximum_km:
                    raise ValueError(prefix + "_IMPLAUSIBLE_" + body.upper() + "_DISTANCE_KM")
            result.append(numbers)
    if len(result) != 3:
        raise ValueError(prefix + "_REQUIRES_THREE_GMAT_EPOCHS")
    return result


def parse_gmat_sun(path: Path) -> list[list[float]]:
    return _parse_gmat_celestial(path, body="SUN",
                                 minimum_km=1.0e8, maximum_km=2.0e8)


def parse_gmat_moon(path: Path) -> list[list[float]]:
    return _parse_gmat_celestial(path, body="MOON",
                                 minimum_km=2.0e5, maximum_km=5.0e5)


def same_source_basilisk_celestials(
    kernel_root: Path, epoch: str, *,
    spacecraft_initial: dict | None = None,
    source_earth_mu_m3_s2: float | None = None,
) -> dict:
    """Execute native Basilisk 2.11.0 with only the three exact supplied kernels.

    The gravBodyFactory.createSpiceInterface(path=...) helper in v2.11.0
    currently resolves string filenames against Basilisk's POOCH cache rather
    than the supplied path; using it here can silently select DE430. The
    official SpiceInterface exposes addKernelPaths and an opt-out of automatic
    default kernels. Use both, and verify the loaded CSPICE kernel inventory.
    """
    from Basilisk.utilities import SimulationBaseClass, macros
    from Basilisk.simulation import spiceInterface, spacecraft
    import Basilisk
    if Basilisk.__version__ != "2.11.0":
        raise RuntimeError("DT2_SUN_ANCHOR_WRONG_BASILISK")
    names = (
        "SPICELeapSecondKernel.tls",
        "SPICEPlanetaryConstantsKernel.tpc",
        "DE421AllPlanets.bsp",
    )
    kernel_paths = [(kernel_root / name).resolve(strict=True) for name in names]
    if any(not path.is_file() for path in kernel_paths):
        raise FileNotFoundError("DT2_SUN_ANCHOR_BSK_KERNEL_MISSING")
    if spiceInterface.countKernelsLoaded() != 0:
        raise RuntimeError("DT2_SUN_ANCHOR_BSK_SPICE_POOL_NOT_EMPTY")

    sc_sim = SimulationBaseClass.SimBaseClass()
    process = sc_sim.CreateNewProcess("dt2_sun_anchor_process")
    task = "dt2_sun_anchor_task"
    process.addTask(sc_sim.CreateNewTask(task, macros.sec2nano(2700.0)))
    when = datetime.fromisoformat(epoch.replace("Z", "+00:00")).astimezone(timezone.utc)
    spice_time = when.strftime("%Y %B %d %H:%M:%S.%f") + " (UTC)"
    # Disable Basilisk's automatic DE430/naif0012 default kernel selection.
    spice = spiceInterface.SpiceInterface(auto_configure_kernels=False)
    spice.ModelTag = "DT2_DE421_EXPLICIT_SPICE_INTERFACE"
    spice.addKernelPaths([str(p) for p in kernel_paths])
    spice.addPlanetNames(["earth", "sun", "moon"])
    spice.zeroBase = "earth"
    spice.referenceBase = "J2000"
    spice.UTCCalInit = spice_time
    sc_sim.AddModelToTask(task, spice)
    if len(spice.planetStateOutMsgs) != 3:
        raise RuntimeError("DT2_CELESTIAL_ANCHOR_BSK_PLANET_MESSAGE_COUNT")
    recorders = {
        "sun": spice.planetStateOutMsgs[1].recorder(macros.sec2nano(2700.0)),
        "moon": spice.planetStateOutMsgs[2].recorder(macros.sec2nano(2700.0)),
    }
    for recorder in recorders.values():
        sc_sim.AddModelToTask(task, recorder)
    clock_recorder = spice.spiceTimeOutMsg.recorder(macros.sec2nano(2700.0))
    sc_sim.AddModelToTask(task, clock_recorder)

    state_recorder = None
    if spacecraft_initial is not None:
        from Basilisk.utilities import simIncludeGravBody
        if (source_earth_mu_m3_s2 is None or
                not math.isfinite(float(source_earth_mu_m3_s2)) or
                float(source_earth_mu_m3_s2) <= 0):
            raise ValueError("DT2_NATIVE_CLOCK_REPLAY_SOURCE_MU_REQUIRED")
        names = ("x_m", "y_m", "z_m", "vx_m_s", "vy_m_s", "vz_m_s")
        try:
            initial = [float(spacecraft_initial[k]) for k in names]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("DT2_NATIVE_CLOCK_REPLAY_SIX_STATE_REQUIRED") from exc
        if not all(math.isfinite(v) for v in initial):
            raise ValueError("DT2_NATIVE_CLOCK_REPLAY_NONFINITE_INITIAL_STATE")
        # This is a NEW observational replay; the original sealed BSK Run is
        # not changed. The real native spacecraft executes in this SAME
        # Basilisk Process as the 3-kernel inertial J2000 SPICE clock.
        orbit_task = "dt2_native_clock_replay_orbit_task"
        process.addTask(sc_sim.CreateNewTask(orbit_task, macros.sec2nano(5.0)))
        craft = spacecraft.Spacecraft()
        craft.ModelTag = "DT2_NATIVE_CLOCK_SAME_PROCESS_CENTRAL_EARTH"
        craft.hub.mHub = 100.0
        craft.hub.IHubPntBc_B = [
            [100.0, 0., 0.], [0., 100.0, 0.], [0., 0., 100.0]]
        craft.hub.r_CN_NInit = initial[:3]
        craft.hub.v_CN_NInit = initial[3:]
        sc_sim.AddModelToTask(orbit_task, craft)
        gravity = simIncludeGravBody.gravBodyFactory()
        earth = gravity.createEarth()
        earth.isCentralBody = True
        gravity.addBodiesTo(craft)
        if float(earth.mu) != float(source_earth_mu_m3_s2):
            raise ValueError("DT2_NATIVE_CLOCK_REPLAY_MU_NOT_IDENTICAL")
        state_recorder = craft.scStateOutMsg.recorder(macros.sec2nano(60.0))
        sc_sim.AddModelToTask(orbit_task, state_recorder)
    sc_sim.InitializeSimulation()
    # Check the genuine Basilisk C++ CSPICE pool, not the Python argument list.
    if not all(spiceInterface.isKernelLoaded(str(p)) for p in kernel_paths):
        raise RuntimeError("DT2_SUN_ANCHOR_BSK_PINNED_KERNEL_NOT_LOADED")
    if spiceInterface.countKernelsLoaded() != len(kernel_paths):
        raise RuntimeError("DT2_SUN_ANCHOR_BSK_UNEXPECTED_EXTRA_KERNELS")
    sc_sim.ConfigureStopTime(macros.sec2nano(5400.0))
    sc_sim.ExecuteSimulation()
    result = {}
    for body, recorder in recorders.items():
        seconds = recorder.times() * macros.NANO2SEC
        pos, vel = recorder.PositionVector, recorder.VelocityVector
        if len(seconds) != 3 or any(abs(float(t) - i*2700) > 1e-6
                                   for i, t in enumerate(seconds)):
            raise ValueError("DT2_CELESTIAL_ANCHOR_BSK_MESSAGE_TIME_GRID: " + body)
        rows = [[float(v)/1000.0 for v in (*pos[i], *vel[i])]
                for i in range(3)]
        if not all(all(math.isfinite(v) for v in row) for row in rows):
            raise ValueError("DT2_CELESTIAL_ANCHOR_BSK_NONFINITE: " + body)
        result[body] = rows
    clock_times = clock_recorder.times() * macros.NANO2SEC
    clock_et = clock_recorder.J2000Current
    if len(clock_times) != 3 or len(clock_et) != 3:
        raise ValueError("DT2_NATIVE_CLOCK_SPICE_TIME_MESSAGE_COUNT")
    result["native_spice_clock"] = [
        {"time_s": float(clock_times[i]), "et_s": float(clock_et[i])}
        for i in range(3)
    ]
    if not all(math.isfinite(v) for row in result["native_spice_clock"]
               for v in row.values()):
        raise ValueError("DT2_NATIVE_CLOCK_NONFINITE_ET")
    if state_recorder is not None:
        times = state_recorder.times() * macros.NANO2SEC
        position = state_recorder.r_BN_N
        velocity = state_recorder.v_BN_N
        if len(times) != 91:
            raise ValueError("DT2_NATIVE_CLOCK_REPLAY_91_STATES_REQUIRED")
        states = []
        for i, tick in enumerate(times):
            values = [float(v) for v in (*position[i], *velocity[i])]
            if not all(math.isfinite(v) for v in values):
                raise ValueError("DT2_NATIVE_CLOCK_REPLAY_NONFINITE_STATE")
            states.append({
                "time_s": float(tick),
                **dict(zip(("x_m","y_m","z_m","vx_m_s","vy_m_s","vz_m_s"),
                           values)),
            })
        result["native_spacecraft_replay"] = states
    return result


def same_source_basilisk_sun(kernel_root: Path, epoch: str) -> list[list[float]]:
    """Backward-compatible Sun-only read of the same native two-body probe."""
    return same_source_basilisk_celestials(kernel_root, epoch)["sun"]


def dist(a: list[float], b: list[float]) -> float:
    return math.sqrt(sum((x-y)**2 for x,y in zip(a,b)))


def angle_arcsec(a: list[float],b: list[float]) -> float:
    n1=math.sqrt(sum(x*x for x in a))
    n2=math.sqrt(sum(x*x for x in b))
    if min(n1,n2)<=0:
        raise ValueError("DT2_SUN_ANCHOR_ZERO_VECTOR")
    # atan2(cross,dot) is stable for sub-arcsecond differences; acos of a
    # near-unit cosine loses many significant digits to cancellation.
    x=a[1]*b[2]-a[2]*b[1]
    y=a[2]*b[0]-a[0]*b[2]
    z=a[0]*b[1]-a[1]*b[0]
    sinang=math.sqrt(x*x+y*y+z*z)/(n1*n2)
    cosang=sum(x*y for x,y in zip(a,b))/(n1*n2)
    return math.atan2(sinang,cosang)*206264.806247


def sun_triplet_condition(positions_km: list[list[float]]) -> dict:
    """Numerical *observability* of three Sun directions, not a frame approval.

    Three nearby Sun vectors constrain the frame poorly around their shared
    direction. Report conditioning rather than silently fitting a 3-D frame.
    """
    import numpy as np
    xyz = np.asarray(positions_km, dtype=np.float64)
    if xyz.shape != (3, 3) or not np.all(np.isfinite(xyz)):
        raise ValueError("DT2_SUN_ANCHOR_DIRECTION_SHAPE_OR_FINITE")
    norms = np.linalg.norm(xyz, axis=1)
    if not np.all(np.isfinite(norms)) or np.any(norms <= 0):
        raise ValueError("DT2_SUN_ANCHOR_DIRECTION_ZERO_VECTOR")
    unit = xyz / norms[:, None]
    sigma = np.linalg.svd(unit, compute_uv=False)
    maximum_separation_deg = max(
        math.degrees(math.atan2(
            float(np.linalg.norm(np.cross(unit[i], unit[j]))),
            float(np.dot(unit[i], unit[j])),
        ))
        for i, j in ((0, 1), (0, 2), (1, 2))
    )
    condition = float(sigma[0] / sigma[-1]) if sigma[-1] > 0 else None
    # A frame rotation belongs to SO(3), not the space of unrestricted 3x3
    # matrices. Its local Jacobian is the direction cross-product operator;
    # the information matrix below is J^T J, not unit's matrix condition.
    # It quantifies weak rotation about the common Sun direction without
    # pretending that a rank-2 direction set cannot constrain a rotation.
    information = sum(
        (np.eye(3) - np.outer(direction, direction) for direction in unit),
        np.zeros((3, 3), dtype=np.float64),
    )
    info_eigenvalues = np.linalg.eigvalsh(information)
    rotation_condition = (
        float(info_eigenvalues[-1] / info_eigenvalues[0])
        if info_eigenvalues[0] > 0 else None
    )
    return {
        "normalized_sun_direction_singular_values": [float(v) for v in sigma],
        "sun_triplet_condition_number": condition,
        "rotation_information_eigenvalues": [float(v) for v in info_eigenvalues],
        "rotation_information_condition_number": rotation_condition,
        "rotation_information_scope": "linearized SO(3) direction Jacobian, unit-weighted; not a certified rotation or statistical confidence interval",
        "max_pairwise_sun_direction_separation_deg": maximum_separation_deg,
        "absolute_inertial_frame_tie_approved": False,
        "description": "three geocentric Sun directions only; not an independently qualified full inertial frame tie",
    }



def two_celestial_direction_geometry(sun_km: list[list[float]],
                                     moon_km: list[list[float]]) -> dict:
    """Compare actual Sun/Luna orientation constraints; never fit an approved frame."""
    import numpy as np
    a = np.asarray(sun_km, dtype=np.float64)
    b = np.asarray(moon_km, dtype=np.float64)
    if a.shape != (3, 3) or b.shape != (3, 3) or not (
        np.all(np.isfinite(a)) and np.all(np.isfinite(b))
    ):
        raise ValueError("DT2_SUN_MOON_DIRECTION_SHAPE_OR_FINITE")
    norm_a, norm_b = np.linalg.norm(a, axis=1), np.linalg.norm(b, axis=1)
    if np.any(norm_a <= 0) or np.any(norm_b <= 0):
        raise ValueError("DT2_SUN_MOON_DIRECTION_ZERO_VECTOR")
    angles = []
    eigenvalues = []
    conditions = []
    for sun, moon in zip(a / norm_a[:, None], b / norm_b[:, None]):
        sep = math.degrees(math.atan2(
            float(np.linalg.norm(np.cross(sun, moon))),
            float(np.dot(sun, moon))))
        information = 2*np.eye(3) - np.outer(sun, sun) - np.outer(moon, moon)
        eig = np.linalg.eigvalsh(information)
        angles.append(sep)
        eigenvalues.append([float(v) for v in eig])
        conditions.append(float(eig[-1]/eig[0]) if eig[0] > 0 else None)
    return {
        "sun_moon_separation_deg_by_epoch": angles,
        "rotation_information_eigenvalues_by_epoch": eigenvalues,
        "rotation_information_condition_by_epoch": conditions,
        "uses_two_physically_distinct_ephemeris_directions": True,
        "original_pointmass_n_axes_externally_certified": False,
        "independent_frame_time_alignment_approved": False,
        "scientific_verdict": "INCONCLUSIVE",
    }


def frozen_celestial_frame_probe(gmat_sun: list[list[float]],
                                 gmat_moon: list[list[float]],
                                 naif_sun: list[list[float]],
                                 naif_moon: list[list[float]],
                                 spacecraft_frame_rows: list[dict]) -> tuple[dict, list[dict]]:
    """Read-only GMAT MJ2000Eq -> NAIF J2000 orientation probe from t=0 Sun/Luna.

    The orientation is frozen using two real celestial directions only. The
    91 GMAT spacecraft EarthICRF rows are held-out checks, NEVER fit inputs.
    This does not establish the Basilisk spacecraft N-to-ICRF physical tie.
    """
    import numpy as np
    sun, moon = np.asarray(gmat_sun, dtype=float), np.asarray(gmat_moon, dtype=float)
    naif_s, naif_m = np.asarray(naif_sun, dtype=float), np.asarray(naif_moon, dtype=float)
    if (sun.shape != (3, 12) or moon.shape != (3, 12) or
            naif_s.shape != (3, 6) or naif_m.shape != (3, 6) or
            not all(np.isfinite(a).all() for a in (sun, moon, naif_s, naif_m))):
        raise ValueError("DT2_FRAME_BRIDGE_BAD_CELESTIAL_INPUT")
    if len(spacecraft_frame_rows) != 91:
        raise ValueError("DT2_FRAME_BRIDGE_REQUIRES_91_ORIGINAL_FRAME_ROWS")

    def basis(a, b):
        na = float(np.linalg.norm(a))
        if not math.isfinite(na) or na <= 0:
            raise ValueError("DT2_FRAME_BRIDGE_DEGENERATE_REFERENCE")
        u = a / na
        perpendicular = b - float(np.dot(b, u))*u
        nb = float(np.linalg.norm(perpendicular))
        if not math.isfinite(nb) or nb <= 0:
            raise ValueError("DT2_FRAME_BRIDGE_DEGENERATE_REFERENCE")
        v = perpendicular / nb
        return np.column_stack([u, v, np.cross(u, v)])

    matrices = []
    for i in range(3):
        source = basis(sun[i, 6:9], moon[i, 6:9])
        target = basis(naif_s[i, :3], naif_m[i, :3])
        matrices.append(target @ source.T)
    frozen_rotation = matrices[0]
    orientation_change_arcsec = []
    for matrix in matrices:
        delta = matrix @ frozen_rotation.T
        skew = np.array([delta[2,1]-delta[1,2],delta[0,2]-delta[2,0],
                         delta[1,0]-delta[0,1]])
        radians = math.atan2(float(np.linalg.norm(skew))/2,
                             (float(np.trace(delta))-1)/2)
        orientation_change_arcsec.append(math.degrees(radians)*3600)

    audit_rows = []
    previous_time = None
    for index, row in enumerate(spacecraft_frame_rows):
        fields = [
            "mj_x_km", "mj_y_km", "mj_z_km",
            "mj_vx_km_s", "mj_vy_km_s", "mj_vz_km_s",
            "icrf_x_km", "icrf_y_km", "icrf_z_km",
            "icrf_vx_km_s", "icrf_vy_km_s", "icrf_vz_km_s",
        ]
        try:
            elapsed = float(row["elapsed_s"])
            values = {name: float(row[name]) for name in fields}
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("DT2_FRAME_BRIDGE_MISSING_OR_INVALID_STATE") from exc
        if not math.isfinite(elapsed) or not all(math.isfinite(v) for v in values.values()):
            raise ValueError("DT2_FRAME_BRIDGE_NONFINITE_STATE")
        if previous_time is not None and elapsed <= previous_time:
            raise ValueError("DT2_FRAME_BRIDGE_NONMONOTONE_GMAT_TIME")
        previous_time = elapsed
        mj_pos = np.array([values[n] for n in fields[:3]])
        mj_vel = np.array([values[n] for n in fields[3:6]])
        icrf_pos = np.array([values[n] for n in fields[6:9]])
        icrf_vel = np.array([values[n] for n in fields[9:12]])
        dp = (frozen_rotation @ mj_pos - icrf_pos)*1000
        dv = (frozen_rotation @ mj_vel - icrf_vel)*1000
        audit_rows.append({
            "sample_index": index, "gmat_original_elapsed_s": elapsed,
            "position_residual_norm_m": float(np.linalg.norm(dp)),
            "velocity_residual_norm_m_s": float(np.linalg.norm(dv)),
            "dx_m": float(dp[0]), "dy_m": float(dp[1]), "dz_m": float(dp[2]),
            "dvx_m_s": float(dv[0]), "dvy_m_s": float(dv[1]),
            "dvz_m_s": float(dv[2]),
        })
    diagnostic = {
        "status": "INCONCLUSIVE",
        "reference_source": "genuine GMAT EarthMJ2000Eq Sun/Luna at t=0",
        "reference_target": "independent NAIF DE421 J2000 Sun/Luna at t=0",
        "heldout_check": "91 genuine GMAT EarthICRF spacecraft states; no spacecraft fit",
        "reference_epoch_index": 0,
        "spacecraft_holdout_samples": len(audit_rows),
        "reference_rotation_matrix_mj_to_naif_j2000": frozen_rotation.tolist(),
        "reference_rotation_determinant": float(np.linalg.det(frozen_rotation)),
        "celestial_rotation_variation_arcsec_by_epoch": orientation_change_arcsec,
        "max_position_residual_m": max(r["position_residual_norm_m"] for r in audit_rows),
        "max_velocity_residual_m_s": max(r["velocity_residual_norm_m_s"] for r in audit_rows),
        "original_basilisk_N_to_ICRF_approved": False,
        "original_pointmass_orbit_frame_time_qualified_samples": 0,
        "mission_accuracy_budget_approved": False,
    }
    return diagnostic, audit_rows



def audit_native_same_process_clock(
    spacecraft_replay: list[dict], original_basilisk: list[dict],
    native_clock: list[dict], naif_et: list[float], gmat_frame_rows: list[dict],
) -> tuple[dict, list[dict]]:
    """Independent clock audit, not certification of an absolute N-frame tie.

    Original real BSK trajectory and all GMAT raw times are read-only.
    """
    if len(spacecraft_replay) != 91 or len(original_basilisk) != 91:
        raise ValueError("DT2_NATIVE_CLOCK_SOURCE_REPLAY_91_STATES_REQUIRED")
    if len(native_clock) != 3 or len(naif_et) != 3 or len(gmat_frame_rows) != 91:
        raise ValueError("DT2_NATIVE_CLOCK_THREE_CELESTIAL_EPOCHS_REQUIRED")
    axes = ("x_m","y_m","z_m","vx_m_s","vy_m_s","vz_m_s")
    max_position, max_velocity = 0.0, 0.0
    for i,(new, original) in enumerate(zip(spacecraft_replay, original_basilisk)):
        try:
            t1,t2 = float(new["time_s"]),float(original["time_s"])
            rdiff = [float(new[k])-float(original[k]) for k in axes[:3]]
            vdiff = [float(new[k])-float(original[k]) for k in axes[3:]]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("DT2_NATIVE_CLOCK_REPLAY_MISSING_VALUE") from exc
        if (not math.isfinite(t1) or not math.isfinite(t2) or
            abs(t1-i*60.0)>1e-6 or abs(t2-i*60.0)>1e-6 or
            not all(math.isfinite(v) for v in (*rdiff,*vdiff))):
            raise ValueError("DT2_NATIVE_CLOCK_REPLAY_BAD_TIME_OR_VALUE")
        max_position=max(max_position,math.sqrt(sum(v*v for v in rdiff)))
        max_velocity=max(max_velocity,math.sqrt(sum(v*v for v in vdiff)))
        if i==0 and (math.sqrt(sum(v*v for v in rdiff))>1e-6 or
                     math.sqrt(sum(v*v for v in vdiff))>1e-6):
            raise ValueError("DT2_NATIVE_CLOCK_REPLAY_ORIGINAL_INITIAL_STATE_CHANGED")

    results=[]
    for j,k in enumerate((0,45,90)):
        try:
            actual_time = float(native_clock[j]["time_s"])
            recorded_et = float(native_clock[j]["et_s"])
            independent_et = float(naif_et[j])
            gmat = gmat_frame_rows[k]
            gmat_elapsed = float(gmat["elapsed_s"])
            a1 = float(gmat["a1_mjd"])
            utc = float(gmat["utc_mjd"])
            tai = float(gmat["tai_mjd"])
            tt = float(gmat["tt_mjd"])
        except (KeyError, TypeError, ValueError, IndexError) as exc:
            raise ValueError("DT2_NATIVE_CLOCK_MISSING_TIME_SOURCE") from exc
        vals = [actual_time,recorded_et,independent_et,
                gmat_elapsed,a1,utc,tai,tt]
        if not all(math.isfinite(v) for v in vals):
            raise ValueError("DT2_NATIVE_CLOCK_NONFINITE_TIME_SOURCE")
        if abs(actual_time-k*60.0)>1e-6:
            raise ValueError("DT2_NATIVE_CLOCK_UNEXPECTED_SPICE_TASK_TICK")
        if abs(gmat_elapsed-k*60.0)>20e-6:
            # This is the existing bounded GMAT file representation guard,
            # NOT a new orbit timing acceptance threshold.
            raise ValueError("DT2_NATIVE_CLOCK_GMAT_INPUT_REPRESENTATION_BAD")
        results.append({
            "sample_index":k,
            "native_bsk_task_elapsed_s":actual_time,
            "native_bsk_spice_J2000_ET_s":recorded_et,
            "independent_naif_J2000_ET_s":independent_et,
            "native_minus_naif_ET_s":recorded_et-independent_et,
            "raw_gmat_ElapsedSecs_s":gmat_elapsed,
            "raw_gmat_A1ModJulian":a1,
            "raw_gmat_UTCModJulian":utc,
            "raw_gmat_TAIModJulian":tai,
            "raw_gmat_TTModJulian":tt,
        })
    summary={
        "status":"INCONCLUSIVE",
        "native_run":"second genuine Basilisk two-body replay, one Process with separate 5s spacecraft and 2700s J2000 SPICE tasks",
        "native_spice_referenceBase":"J2000",
        "native_spice_zeroBase":"earth",
        "pinned_native_kernels":3,
        "original_spacecraft_baseline_mutated":False,
        "native_spacecraft_replay_rows":len(spacecraft_replay),
        "native_spice_time_message_rows":len(results),
        "max_native_spacecraft_replay_position_vs_sealed_baseline_m":max_position,
        "max_native_spacecraft_replay_velocity_vs_sealed_baseline_m_s":max_velocity,
        "max_native_spice_ET_minus_naif_ET_abs_s":max(
            abs(r["native_minus_naif_ET_s"]) for r in results),
        "absolute_Basilisk_spacecraft_N_to_EarthICRF_approved":False,
        "absolute_GMAT_A1_to_Basilisk_ET_approved":False,
        "independently_qualified_cross_engine_samples":0,
        "mission_accuracy_budget_approved":False,
    }
    return summary, results



def audit_gmat_a1_tt_to_real_et(
    gmat_frame_rows: list[dict],
    native_clock: list[dict],
    naif_utc_et: list[float],
    spice_gmat_tt_et: list[float],
    spice_gmat_a1_et: list[float],
) -> tuple[dict, list[dict]]:
    """Observe, but never approve, the separate GMAT A1/TT to J2000 ET chains.

    The ET conversions are genuine CSPICE UNITIM results obtained while the
    EXACT GMAT-packaged LSK is furnished; all GMAT A1/TAI/TT rows and original
    Basilisk J2000Current messages remain unmodified and are SHA-bound.
    """
    if len(gmat_frame_rows)!=91 or any(
        len(array)!=3 for array in (
            native_clock,naif_utc_et,spice_gmat_tt_et,spice_gmat_a1_et)
    ):
        raise ValueError("DT2_ABSOLUTE_CLOCK_REQUIRES_AUTHENTIC_91_AND_THREE")
    official_a1_minus_tai_s=0.0343817  # GMAT R2026a GmatConstants.hpp
    indices=(0,45,90)
    compared=[]
    for j,index in enumerate(indices):
        row=gmat_frame_rows[index]
        try:
            a1,tai,tt,utc,elapsed=(
                float(row[key]) for key in
                ("a1_mjd","tai_mjd","tt_mjd","utc_mjd","elapsed_s")
            )
            task_elapsed=float(native_clock[j]["time_s"])
            native_et=float(native_clock[j]["et_s"])
            utc_et=float(naif_utc_et[j])
            tt_et=float(spice_gmat_tt_et[j])
            a1_et=float(spice_gmat_a1_et[j])
        except (KeyError,TypeError,ValueError) as exc:
            raise ValueError("DT2_ABSOLUTE_CLOCK_MISSING_REAL_REPORT_COLUMN") from exc
        values=(a1,tai,tt,utc,elapsed,task_elapsed,native_et,utc_et,tt_et,a1_et)
        if not all(math.isfinite(v) for v in values):
            raise ValueError("DT2_ABSOLUTE_CLOCK_NONFINITE_REAL_REPORT")
        if abs(task_elapsed-index*60.0)>1e-6:
            raise ValueError("DT2_ABSOLUTE_CLOCK_NATIVE_TASK_BAD_GRID")
        if abs(elapsed-index*60.0)>20e-6:
            raise ValueError("DT2_ABSOLUTE_CLOCK_GMAT_REPRESENTATION_BAD_GRID")
        compared.append({
            "sample_index":index,
            "gmat_raw_elapsed_s":elapsed,
            "native_bsk_task_elapsed_s":task_elapsed,
            "gmat_a1_modjulian":a1,
            "gmat_tai_modjulian":tai,
            "gmat_tt_modjulian":tt,
            "gmat_utc_modjulian":utc,
            "gmat_reported_A1_minus_TAI_s":(a1-tai)*86400.0,
            "gmat_A1_TAI_minus_official_R2026a_offset_s":
                (a1-tai)*86400.0-official_a1_minus_tai_s,
            "gmat_reported_TT_minus_TAI_s":(tt-tai)*86400.0,
            "spice_unitim_GMAT_TT_to_ET_s":tt_et,
            "spice_unitim_GMAT_A1_via_TAI_TT_to_ET_s":a1_et,
            "independent_naif_str2et_UTC_s":utc_et,
            "basilisk_same_process_native_J2000Current_s":native_et,
            "GMAT_TT_ET_minus_naif_UTC_ET_s":tt_et-utc_et,
            "GMAT_A1_ET_minus_naif_UTC_ET_s":a1_et-utc_et,
            "GMAT_TT_ET_minus_BSK_native_ET_s":tt_et-native_et,
            "GMAT_A1_ET_minus_BSK_native_ET_s":a1_et-native_et,
            "GMAT_TT_ET_minus_A1_ET_s":tt_et-a1_et,
            "native_BSK_ET_minus_independent_NAIF_UTC_ET_s":native_et-utc_et,
        })
    summary={
        "status":"INCONCLUSIVE",
        "source_case":"CROSS-LEO-001",
        "real_samples":len(compared),
        "gmat_mjd_definition":"GMAT R2026a JD minus 2430000.0",
        "gmat_A1_minus_TAI_source":"nasa/GMAT tag R2026a src/gmatutil/util/GmatConstants.hpp",
        "official_gmat_A1_minus_TAI_s":official_a1_minus_tai_s,
        "NAIF_conversion":"genuine spiceypy 8.4.0 unitim(TDT,ET) with exact packaged LSK",
        "original_GMAT_clock_and_basilisk_spice_messages_unmodified":True,
        "max_abs_reported_A1_TAI_offset_error_s":
            max(abs(x["gmat_A1_TAI_minus_official_R2026a_offset_s"]) for x in compared),
        "max_abs_GMAT_TT_to_NAIF_ET_delta_s":
            max(abs(x["GMAT_TT_ET_minus_naif_UTC_ET_s"]) for x in compared),
        "max_abs_GMAT_A1_to_NAIF_ET_delta_s":
            max(abs(x["GMAT_A1_ET_minus_naif_UTC_ET_s"]) for x in compared),
        "max_abs_GMAT_TT_to_native_BSK_ET_delta_s":
            max(abs(x["GMAT_TT_ET_minus_BSK_native_ET_s"]) for x in compared),
        "max_abs_GMAT_TT_ET_vs_A1_ET_delta_s":
            max(abs(x["GMAT_TT_ET_minus_A1_ET_s"]) for x in compared),
        "GMAT_A1_to_original_Basilisk_absolute_clock_approved":False,
        "original_Basilisk_spacecraft_N_axes_independently_qualified":False,
        "aligned_original_two_engine_state_samples":0,
        "mission_accuracy_budget_approved":False,
    }
    return summary,compared


def run(out: Path, root: Path, binary: Path) -> dict:
    out=out.resolve(strict=True)
    root=root.resolve(strict=True)
    binary=binary.resolve(strict=True)
    if not binary.is_file():
        raise ValueError("DT2_SUN_ANCHOR_REAL_GMAT_BINARY_REQUIRED")
    manifest=json.loads((out/"case_manifest.json").read_text(encoding="utf-8"))
    evidence=manifest.get("evidence",{})
    if (not evidence.get("basilisk_executed")
        or not evidence.get("gmat_executed")
        or not evidence.get("gmat_mu_control_executed")
        or not evidence.get("gmat_frame_time_probe_executed")
        or digest(binary)!=evidence.get("gmat_binary_sha256")):
        raise ValueError("DT2_SUN_ANCHOR_REQUIRES_PRIOR_REAL_ENGINE_RECEIPTS")
    old=json.loads((out/"gmat_frame_time_execution.json").read_text(encoding="utf-8"))
    frame=out/"gmat_frame_time_rows.csv"
    if old.get("status")!="GMAT_FRAME_TIME_REPORTED_UNQUALIFIED_ABSOLUTE_TIE":
        raise ValueError("DT2_SUN_ANCHOR_REQUIRES_REAL_FRAME_RECEIPT")
    if digest(frame)!=old.get("normalized_frame_time_sha256"):
        raise ValueError("DT2_SUN_ANCHOR_PRIOR_FRAME_HASH_INVALID")
    with frame.open(newline="",encoding="utf-8") as f:
        rows=list(csv.DictReader(f))
    if len(rows)!=91:
        raise ValueError("DT2_SUN_ANCHOR_BAD_PRIOR_CLOCK_GRID")
    a1_epochs=[float(rows[i]["a1_mjd"]) for i in INDICES]
    paths={
        "spk":root/"data/planetary_ephem/spk/DE421AllPlanets.bsp",
        "lsk":root/"data/time/SPICELeapSecondKernel.tls",
        "pck":root/"data/planetary_coeff/SPICEPlanetaryConstantsKernel.tpc",
    }
    for name,path in paths.items():
        if not path.is_file() or path.stat().st_size<100:
            raise FileNotFoundError("DT2_SUN_ANCHOR_GMAT_OFFICIAL_KERNEL_MISSING: "+name)
    startup_evidence = verified_gmat_startup_kernels(root, paths)
    outputs={
        "script":out/"gmat_sun_anchor_executed.script",
        "raw":out/"gmat_sun_anchor_raw.txt",
        "moon_raw":out/"gmat_moon_anchor_raw.txt",
        "stdout":out/"gmat_sun_anchor_stdout.log",
        "stderr":out/"gmat_sun_anchor_stderr.log",
        "rows":out/"sun_anchor_three_engines.csv",
        "moon_rows":out/"moon_anchor_three_engines.csv",
        "frame_bridge_rows":out/"gmat_mj_naif_frame_probe_rows.csv",
        "frame_bridge_diagnostic":out/"gmat_mj_naif_frame_probe.json",
        "native_clock_replay":out/"basilisk_same_process_spacecraft.csv",
        "native_clock_raw":out/"basilisk_same_process_clock.csv",
        "native_clock_diagnostic":out/"basilisk_same_process_clock_diagnostic.json",
        "a1_tt_et_rows":out/"gmat_a1_tt_naif_ET_rows.csv",
        "a1_tt_et_diagnostic":out/"gmat_a1_tt_naif_ET_diagnostic.json",
        "receipt":out/"sun_anchor_execution.json",
    }
    if any(p.exists() for p in outputs.values()):
        raise FileExistsError("DT2_SUN_ANCHOR_IMMUTABLE_OUTPUT_COLLISION")
    outputs["script"].write_text(sun_script(
        a1_epochs,report_path=outputs["raw"],
        moon_report_path=outputs["moon_raw"]),encoding="ascii")
    proc=subprocess.run([str(binary),"--run",str(outputs["script"])],
        cwd=binary.parent,capture_output=True,text=True,timeout=240,check=False)
    outputs["stdout"].write_text(proc.stdout,encoding="utf-8")
    outputs["stderr"].write_text(proc.stderr,encoding="utf-8")
    receipt={
        "status":"INCONCLUSIVE",
        "engine":"genuine GMAT R2026a + Basilisk 2.11.0 SpiceInterface + spiceypy 8.4.0",
        "command":[str(binary),"--run",str(outputs["script"])],
        "gmat_exit_code":proc.returncode,
        "gmat_script_sha256":digest(outputs["script"]),
        "gmat_binary_sha256":digest(binary),
        "gmat_stdout_sha256":digest(outputs["stdout"]),
        "kernel_sha256":{k:digest(v) for k,v in paths.items()},
        "gmat_startup_file":startup_evidence["path"],
        "gmat_startup_sha256":startup_evidence["sha256"],
        "gmat_startup_kernel_paths":startup_evidence["kernel_paths"],
        "gmat_sun_report_produced":outputs["raw"].is_file(),
        "gmat_moon_report_produced":outputs["moon_raw"].is_file(),
        "gmat_sun_report_sha256":digest(outputs["raw"]) if outputs["raw"].is_file() else None,
        "gmat_moon_report_sha256":digest(outputs["moon_raw"]) if outputs["moon_raw"].is_file() else None,
        "source_frame_report_sha256":digest(frame),
        "date_utc":manifest["case"]["epoch_utc"],
        "clock_epochs_A1_mjd":a1_epochs,
        "independent_physical_basilisk_n_to_icrf_tie_approved":False,
        "mission_orbit_accuracy_budget_approved":False,
        "scientific_verdict":"INCONCLUSIVE",
    }
    write_json(outputs["receipt"],receipt)
    if proc.returncode!=0 or not outputs["raw"].is_file() or not outputs["moon_raw"].is_file():
        raise RuntimeError("DT2_SUN_ANCHOR_REAL_GMAT_EXECUTION_FAILED")
    gmats=parse_gmat_sun(outputs["raw"])
    gmat_moons=parse_gmat_moon(outputs["moon_raw"])
    # Third-party dependency is needed ONLY after actual GMAT execution and
    # immutable source checks, never to import the CLI or reject missing data.
    import spiceypy as spice
    epoch=datetime.fromisoformat(manifest["case"]["epoch_utc"].replace("Z","+00:00"))
    spice.furnsh(str(paths["lsk"]))
    spice.furnsh(str(paths["spk"]))
    naif=[]
    naif_moon=[]
    naif_et=[]
    gmat_tt_as_et=[]
    gmat_a1_as_et=[]
    try:
        for index in INDICES:
            at=(epoch+timedelta(seconds=index*60)).isoformat().replace("+00:00","Z")
            et=spice.str2et(at)
            naif_et.append(float(et))
            # GMAT's R2026a MODJULIAN is JD-2430000; J2000 is day 21545.
            # Subtract the two day counts BEFORE converting to seconds, to
            # avoid catastrophic double-precision JD cancellation.
            frame_time=rows[index]
            tt_sec=(float(frame_time["tt_mjd"])-21545.0)*86400.0
            a1_sec=(float(frame_time["a1_mjd"])-21545.0)*86400.0
            gmat_tt_as_et.append(float(spice.unitim(tt_sec,"TDT","ET")))
            # GMAT A1 = TAI+0.0343817s; TT = TAI+32.184s.
            # This separate conversion is NOT an inferred spacecraft fit.
            tt_from_a1=a1_sec-0.0343817+32.184
            gmat_a1_as_et.append(float(spice.unitim(tt_from_a1,"TDT","ET")))
            state,_=spice.spkezr("SUN",et,"J2000","NONE","EARTH")
            moon_state,_=spice.spkezr("MOON",et,"J2000","NONE","EARTH")
            naif.append([float(x) for x in state])
            naif_moon.append([float(x) for x in moon_state])
    finally:
        spice.kclear()
    with tempfile.TemporaryDirectory(prefix="dt2_sun_spice_") as td:
        tmp=Path(td)
        for name,src in (("DE421AllPlanets.bsp",paths["spk"]),
                         ("SPICELeapSecondKernel.tls",paths["lsk"]),
                         ("SPICEPlanetaryConstantsKernel.tpc",paths["pck"])):
            os.symlink(src,tmp/name)
        basilisk_outputs=same_source_basilisk_celestials(
            tmp,manifest["case"]["epoch_utc"],
            spacecraft_initial=manifest["actual_initial_state_SI"],
            source_earth_mu_m3_s2=manifest["gravity"]["mu_m3_s2"],
        )
        basilisk=basilisk_outputs["sun"]
        basilisk_moon=basilisk_outputs["moon"]
    compared=[]
    for i,(gm,na,bs) in enumerate(zip(gmats,naif,basilisk)):
        compared.append({
            "time_s":INDICES[i]*60,
            "gmat_a1_mjd":a1_epochs[i],
            "gmat_icrf_vs_naif_j2000_pos_m":dist(gm[:3],na[:3])*1e3,
            "gmat_icrf_vs_naif_j2000_angle_arcsec":angle_arcsec(gm[:3],na[:3]),
            "basilisk_n_vs_naif_j2000_pos_m":dist(bs[:3],na[:3])*1e3,
            "basilisk_n_vs_naif_j2000_angle_arcsec":angle_arcsec(bs[:3],na[:3]),
            "gmat_mj_vs_icrf_pos_m":dist(gm[6:9],gm[:3])*1e3,
            "gmat_icrf_sun_x_km":gm[0],"gmat_icrf_sun_y_km":gm[1],
            "gmat_icrf_sun_z_km":gm[2],
            "naif_j2000_sun_x_km":na[0],"naif_j2000_sun_y_km":na[1],
            "naif_j2000_sun_z_km":na[2],
            "basilisk_spice_n_sun_x_km":bs[0],
            "basilisk_spice_n_sun_y_km":bs[1],
            "basilisk_spice_n_sun_z_km":bs[2],
        })
    moon_compared=[]
    for i,(gm,na,bs) in enumerate(zip(gmat_moons,naif_moon,basilisk_moon)):
        moon_compared.append({
            "time_s":INDICES[i]*60,
            "gmat_a1_mjd":a1_epochs[i],
            "gmat_icrf_vs_naif_j2000_pos_m":dist(gm[:3],na[:3])*1e3,
            "gmat_icrf_vs_naif_j2000_angle_arcsec":angle_arcsec(gm[:3],na[:3]),
            "basilisk_n_vs_naif_j2000_pos_m":dist(bs[:3],na[:3])*1e3,
            "basilisk_n_vs_naif_j2000_angle_arcsec":angle_arcsec(bs[:3],na[:3]),
            "gmat_mj_vs_icrf_pos_m":dist(gm[6:9],gm[:3])*1e3,
            "gmat_icrf_moon_x_km":gm[0],"gmat_icrf_moon_y_km":gm[1],
            "gmat_icrf_moon_z_km":gm[2],
            "naif_j2000_moon_x_km":na[0],"naif_j2000_moon_y_km":na[1],
            "naif_j2000_moon_z_km":na[2],
            "basilisk_spice_n_moon_x_km":bs[0],
            "basilisk_spice_n_moon_y_km":bs[1],
            "basilisk_spice_n_moon_z_km":bs[2],
        })
    with outputs["rows"].open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(compared[0]))
        writer.writeheader()
        writer.writerows(compared)
    with outputs["moon_rows"].open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(moon_compared[0]))
        writer.writeheader()
        writer.writerows(moon_compared)
    # Three authenticated celestial reference vectors determine the independent
    # t=0 orientation. The 91 original GMAT spacecraft states only test it;
    # they never enter the frame solve and raw reported times are preserved.
    bridge, bridge_rows = frozen_celestial_frame_probe(
        gmats, gmat_moons, naif, naif_moon, rows)
    bridge["source_sha256"] = {
        "gmat_sun_raw":digest(outputs["raw"]),
        "gmat_moon_raw":digest(outputs["moon_raw"]),
        "gmat_frame_time_rows":digest(frame),
    }
    with outputs["frame_bridge_rows"].open("w",newline="",encoding="utf-8") as f:
        writer=csv.DictWriter(f,fieldnames=list(bridge_rows[0]))
        writer.writeheader()
        writer.writerows(bridge_rows)
    bridge["holdout_rows_csv_sha256"] = digest(outputs["frame_bridge_rows"])
    write_json(outputs["frame_bridge_diagnostic"],bridge)
    # Independent, SECOND actual Basilisk spacecraft run in the SAME native
    # simulation process as the 3-kernel SPICE J2000 ET clock. Original
    # Basilisk and GMAT trajectories remain unmodified and hash-verified.
    original_state_file=out/"basilisk_state.csv"
    if digest(original_state_file)!=manifest["evidence"].get("basilisk_state_csv_sha256"):
        raise ValueError("DT2_NATIVE_CLOCK_SEALED_BASILISK_SHA_MISMATCH")
    with original_state_file.open(newline="",encoding="utf-8") as stream:
        original_states=list(csv.DictReader(stream))
    native_states=basilisk_outputs["native_spacecraft_replay"]
    native_clock=basilisk_outputs["native_spice_clock"]
    clock_diagnostic, clock_rows=audit_native_same_process_clock(
        native_states, original_states, native_clock, naif_et, rows)
    with outputs["native_clock_replay"].open("w",newline="",encoding="utf-8") as stream:
        fields=("time_s","x_m","y_m","z_m","vx_m_s","vy_m_s","vz_m_s")
        writer=csv.DictWriter(stream,fieldnames=fields)
        writer.writeheader()
        writer.writerows(native_states)
    with outputs["native_clock_raw"].open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(clock_rows[0]))
        writer.writeheader()
        writer.writerows(clock_rows)
    clock_diagnostic["source_baseline_sha256"]=digest(original_state_file)
    clock_diagnostic["same_process_native_state_sha256"]=digest(outputs["native_clock_replay"])
    clock_diagnostic["native_SPICE_ET_vs_NAIF_vs_GMAT_clock_csv_sha256"]=digest(outputs["native_clock_raw"])
    clock_diagnostic["original_GMAT_frame_time_csv_sha256"]=digest(frame)
    clock_diagnostic["pinned_GMAT_DE421_LSK_PCK_sha256"]=receipt["kernel_sha256"]
    write_json(outputs["native_clock_diagnostic"],clock_diagnostic)
    uniform_diag,uniform_rows=audit_gmat_a1_tt_to_real_et(
        rows,native_clock,naif_et,gmat_tt_as_et,gmat_a1_as_et)
    with outputs["a1_tt_et_rows"].open("w",newline="",encoding="utf-8") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(uniform_rows[0]))
        writer.writeheader()
        writer.writerows(uniform_rows)
    uniform_diag["original_GMAT_frame_time_csv_sha256"]=digest(frame)
    uniform_diag["native_same_process_clock_csv_sha256"]=digest(outputs["native_clock_raw"])
    uniform_diag["pinned_real_GMAT_LSK_sha256"]=receipt["kernel_sha256"]["lsk"]
    uniform_diag["three_time_chain_rows_sha256"]=digest(outputs["a1_tt_et_rows"])
    write_json(outputs["a1_tt_et_diagnostic"],uniform_diag)
    receipt.update({
        "a1_tt_to_et_diagnostic_sha256":digest(outputs["a1_tt_et_diagnostic"]),
        "a1_tt_to_et_rows_sha256":digest(outputs["a1_tt_et_rows"]),
        "a1_tt_to_et_real_sample_count":3,
        "GMAT_A1_TT_to_absolute_Basilisk_time_certified":False,
    })
    receipt.update({
        "same_process_native_clock_diagnostic_sha256":digest(outputs["native_clock_diagnostic"]),
        "same_process_native_clock_rows_sha256":digest(outputs["native_clock_raw"]),
        "same_process_native_replay_states_sha256":digest(outputs["native_clock_replay"]),
        "same_process_native_clock_real_sample_count":3,
        "same_process_native_spacecraft_real_sample_count":91,
        "spacecraft_N_axis_independently_certified":False,
    })
    receipt.update({
        "frame_bridge_diagnostic_sha256":digest(outputs["frame_bridge_diagnostic"]),
        "frame_bridge_rows_sha256":digest(outputs["frame_bridge_rows"]),
        "real_sample_count":len(compared),
        "real_moon_sample_count":len(moon_compared),
        "moon_anchor_comparison_csv_sha256":digest(outputs["moon_rows"]),
        "max_gmat_moon_icrf_vs_naif_j2000_pos_m":
            max(row["gmat_icrf_vs_naif_j2000_pos_m"] for row in moon_compared),
        "max_basilisk_moon_n_vs_naif_j2000_pos_m":
            max(row["basilisk_n_vs_naif_j2000_pos_m"] for row in moon_compared),
        "max_gmat_moon_icrf_vs_naif_j2000_angle_arcsec":
            max(row["gmat_icrf_vs_naif_j2000_angle_arcsec"] for row in moon_compared),
        "max_basilisk_moon_n_vs_naif_j2000_angle_arcsec":
            max(row["basilisk_n_vs_naif_j2000_angle_arcsec"] for row in moon_compared),
        "sun_moon_direction_geometry":two_celestial_direction_geometry(
            [x[:3] for x in naif],[x[:3] for x in naif_moon]),
        "sun_direction_triplet_geometry":sun_triplet_condition(
            [state[:3] for state in naif]),
        "sun_anchor_comparison_csv_sha256":digest(outputs["rows"]),
        "max_gmat_icrf_vs_naif_j2000_angle_arcsec":
            max(x["gmat_icrf_vs_naif_j2000_angle_arcsec"] for x in compared),
        "max_basilisk_n_vs_naif_j2000_angle_arcsec":
            max(x["basilisk_n_vs_naif_j2000_angle_arcsec"] for x in compared),
        "max_basilisk_n_vs_naif_j2000_pos_m":
            max(x["basilisk_n_vs_naif_j2000_pos_m"] for x in compared),
        "spice_j2000_frame_semantics":"NAIF SPICE J2000 uses ICRF axes",
        "gmat_solar_ephemeris_declared":"SPICE with packaged DE421 SPK",
        "naif_ephemeris":"same packaged DE421 SPK; independent spiceypy query",
        "basilisk_spice_interface":"actual Basilisk 2.11.0 addKernelPaths, default kernels disabled, J2000, Earth zeroBase",
        "basilisk_spice_kernel_pool_verified":True,
        "basilisk_spice_pinned_kernel_count":3,
        "original_pointmass_n_axes_externally_certified":False,
        "status":"REAL_THREE_ENGINE_CELESTIAL_SOURCE_OBSERVED_INCONCLUSIVE",
    })
    write_json(outputs["receipt"],receipt)
    if len(compared)!=3 or len(moon_compared)!=3 or not all(
        math.isfinite(v) for x in (*compared,*moon_compared) for v in x.values()
    ):
        raise RuntimeError("DT2_SUN_ANCHOR_INVALID_CELESTIAL_DIAGNOSTIC")
    return receipt


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",required=True,type=Path)
    parser.add_argument("--gmat-root",required=True,type=Path)
    parser.add_argument("--gmat-executable",required=True,type=Path)
    args=parser.parse_args()
    r=run(args.output,args.gmat_root,args.gmat_executable)
    print("DT2_REAL_SUN_CELESTIAL_ANCHOR "
          f"rows={r['real_sample_count']} "
          f"gmat_naif_direction_max_arcsec={r['max_gmat_icrf_vs_naif_j2000_angle_arcsec']:.9e} "
          f"bsk_naif_direction_max_arcsec={r['max_basilisk_n_vs_naif_j2000_angle_arcsec']:.9e} "
          f"moon_gmat_naif_max_m={r['max_gmat_moon_icrf_vs_naif_j2000_pos_m']:.9e} "
          f"moon_bsk_naif_max_m={r['max_basilisk_moon_n_vs_naif_j2000_pos_m']:.9e} "
          f"sun_moon_sep_min_deg={min(r['sun_moon_direction_geometry']['sun_moon_separation_deg_by_epoch']):.9f} "
          "status=INCONCLUSIVE absolute_pointmass_frame_tie=false")


if __name__=="__main__":
    main()
