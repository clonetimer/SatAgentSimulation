"""ORB-1 numerical orbit propagation.

The propagator integrates osculating Cartesian state with a deterministic fixed
step RK4/Euler solver and records per-force contribution traces.  It is an
engineering prototype orbit-fidelity upgrade, not a flight-grade propagator.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Iterable, Mapping
import math

from sat_sim.frames import ECI, FrameMetadata, vector_norm
from sat_sim.orbit.force_models import (
    ORB1_FORCE_MODEL_SCHEMA_VERSION,
    OrbitForceModelConfig,
    OrbitForceModelError,
    force_contributions_m_s2,
    force_norms,
)
from sat_sim.orbit.medium import (
    DEFAULT_EPOCH_UTC,
    EARTH_MU_M3_S2,
    EARTH_RADIUS_M,
    GroundStation,
    analytic_sun_vector_eci,
    dipole_magnetic_field_eci_t,
    eclipse_shadow_factor,
    ground_access_geometry,
    orbital_elements_to_cartesian,
    true_anomaly_to_mean_anomaly,
)
from sat_sim.solvers import FixedStepSolverConfig
from sat_sim.state import CartesianState
from sat_sim.time_systems import TimeGrid, build_time_grid, parse_utc

ORB1_NUMERICAL_ORBIT_SCHEMA_VERSION = "orb1.orbit_fidelity.numerical.v1"


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise OrbitForceModelError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise OrbitForceModelError(f"{name} must be positive")
    return out


def _deg_to_rad(value: float) -> float:
    return math.radians(float(value))


def _rad_to_deg(value: float) -> float:
    return math.degrees(float(value))


def _add_state(a: tuple[float, ...], b: tuple[float, ...], scale: float = 1.0) -> tuple[float, ...]:
    return tuple(float(x + scale * y) for x, y in zip(a, b))


def _combine_rk4(y: tuple[float, ...], k1: tuple[float, ...], k2: tuple[float, ...], k3: tuple[float, ...], k4: tuple[float, ...], dt: float) -> tuple[float, ...]:
    return tuple(float(y[i] + dt * (k1[i] + 2.0 * k2[i] + 2.0 * k3[i] + k4[i]) / 6.0) for i in range(len(y)))


@dataclass(frozen=True)
class OrbitFidelityConfig:
    """Numerical orbit-fidelity configuration."""

    semi_major_axis_m: float
    eccentricity: float = 0.0
    inclination_rad: float = math.radians(51.6)
    raan_rad: float = 0.0
    arg_perigee_rad: float = 0.0
    mean_anomaly_rad: float = 0.0
    epoch_utc: str = DEFAULT_EPOCH_UTC
    force_model: OrbitForceModelConfig = field(default_factory=OrbitForceModelConfig)
    ground_station: GroundStation | None = None
    solver: FixedStepSolverConfig | None = None
    sun_model: str = "analytic"
    enable_eclipse: bool = True
    magnetic_field_model: str = "dipole"

    def __post_init__(self) -> None:
        object.__setattr__(self, "semi_major_axis_m", _positive(self.semi_major_axis_m, "semi_major_axis_m"))
        ecc = _finite(self.eccentricity, "eccentricity")
        if not 0.0 <= ecc < 0.2:
            raise OrbitForceModelError("eccentricity must be within [0, 0.2) for ORB-1 LEO envelope")
        object.__setattr__(self, "eccentricity", ecc)
        inc = _finite(self.inclination_rad, "inclination_rad")
        if not 0.0 <= inc <= math.pi:
            raise OrbitForceModelError("inclination_rad must be within [0, pi]")
        object.__setattr__(self, "inclination_rad", inc)
        parse_utc(self.epoch_utc)
        perigee = self.semi_major_axis_m * (1.0 - ecc) - self.force_model.earth_radius_m
        apogee = self.semi_major_axis_m * (1.0 + ecc) - self.force_model.earth_radius_m
        if perigee < 120_000.0:
            raise OrbitForceModelError("perigee altitude must exceed 120 km for ORB-1 numerical LEO envelope")
        if apogee > 2_500_000.0:
            raise OrbitForceModelError("apogee altitude must remain below 2500 km for ORB-1 LEO envelope")
        if str(self.sun_model).lower() not in {"analytic", "constant"}:
            raise OrbitForceModelError("sun_model must be analytic or constant for ORB-1")
        if str(self.magnetic_field_model).lower() not in {"dipole", "none"}:
            raise OrbitForceModelError("magnetic_field_model must be dipole or none for ORB-1")

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "OrbitFidelityConfig":
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        force = OrbitForceModelConfig.from_task_spec(spec)
        earth_radius = force.earth_radius_m
        if orbit.get("semi_major_axis_m") is not None:
            semi_major_axis = float(orbit["semi_major_axis_m"])
        else:
            semi_major_axis = earth_radius + float(orbit.get("altitude_m", params.get("altitude_m", 500_000.0)))
        ecc = float(orbit.get("eccentricity", params.get("eccentricity", 0.0)))
        if orbit.get("mean_anomaly_deg") is not None:
            mean_anomaly = _deg_to_rad(float(orbit["mean_anomaly_deg"]))
        else:
            true_deg = float(orbit.get("true_anomaly_deg", orbit.get("arg_lat0_deg", params.get("true_anomaly_deg", 0.0))))
            mean_anomaly = true_anomaly_to_mean_anomaly(_deg_to_rad(true_deg), ecc)
        gs_payload = params.get("ground_station") if isinstance(params.get("ground_station"), Mapping) else orbit.get("ground_station") if isinstance(orbit.get("ground_station"), Mapping) else None
        return cls(
            semi_major_axis_m=semi_major_axis,
            eccentricity=ecc,
            inclination_rad=_deg_to_rad(float(orbit.get("inclination_deg", params.get("inclination_deg", 51.6)))),
            raan_rad=_deg_to_rad(float(orbit.get("raan_deg", params.get("raan_deg", 0.0)))),
            arg_perigee_rad=_deg_to_rad(float(orbit.get("arg_perigee_deg", orbit.get("argument_of_perigee_deg", params.get("arg_perigee_deg", 0.0))))),
            mean_anomaly_rad=mean_anomaly,
            epoch_utc=str(sim.get("epoch_utc") or orbit.get("epoch_utc") or DEFAULT_EPOCH_UTC),
            force_model=force,
            ground_station=GroundStation.from_mapping(gs_payload),
            solver=FixedStepSolverConfig.from_simulation(dict(sim)),
            sun_model=str(orbit.get("sun_model", "analytic")),
            enable_eclipse=bool(orbit.get("enable_eclipse", True)),
            magnetic_field_model=str(orbit.get("magnetic_field_model", "dipole")),
        )

    def initial_state_tuple(self) -> tuple[float, ...]:
        r, v = orbital_elements_to_cartesian(
            semi_major_axis_m=self.semi_major_axis_m,
            eccentricity=self.eccentricity,
            inclination_rad=self.inclination_rad,
            raan_rad=self.raan_rad,
            arg_perigee_rad=self.arg_perigee_rad,
            mean_anomaly_rad=self.mean_anomaly_rad,
            earth_mu_m3_s2=self.force_model.earth_mu_m3_s2,
        )
        return (r[0], r[1], r[2], v[0], v[1], v[2])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": ORB1_NUMERICAL_ORBIT_SCHEMA_VERSION,
            "semi_major_axis_m": self.semi_major_axis_m,
            "eccentricity": self.eccentricity,
            "inclination_deg": _rad_to_deg(self.inclination_rad),
            "raan_deg": _rad_to_deg(self.raan_rad),
            "arg_perigee_deg": _rad_to_deg(self.arg_perigee_rad),
            "mean_anomaly_deg": _rad_to_deg(self.mean_anomaly_rad),
            "epoch_utc": self.epoch_utc,
            "force_model": self.force_model.to_dict(),
            "ground_station": self.ground_station.to_dict() if self.ground_station else None,
            "solver": self.solver.to_dict() if self.solver else None,
            "fidelity_level": "medium",
            "can_claim_high_fidelity": False,
        }


@dataclass(frozen=True)
class OrbitFidelitySample:
    time_s: float
    sample_index: int
    utc: str
    cartesian: CartesianState
    sun_vector_eci: tuple[float, float, float]
    shadow_factor: float
    eclipse_flag: bool
    force_norms_m_s2: dict[str, float]
    energy_j_kg: float
    ground_range_m: float | None = None
    ground_elevation_deg: float | None = None
    ground_has_access: bool | None = None
    frame: FrameMetadata = field(default_factory=lambda: FrameMetadata(frame_id=ECI))

    def to_trace_row(self, *, task_id: str, case_id: str, capability_id: str, mode: str, earth_radius_m: float) -> dict[str, Any]:
        pos = self.cartesian.position_m
        vel = self.cartesian.velocity_m_s
        out = {
            "task_id": task_id,
            "case_id": case_id,
            "time_s": self.time_s,
            "sample_index": self.sample_index,
            "utc": self.utc,
            "target_level": "integrated",
            "target_name": "orbit_environment",
            "mode": mode,
            "capability_id": capability_id,
            "fidelity_level": "medium",
            "frame.orbit_state": self.frame.frame_id,
            "orbit.model": "numerical_cowell_central_j2_drag_srp_proxy",
            "orbit.r_bn_n_m_x": pos[0],
            "orbit.r_bn_n_m_y": pos[1],
            "orbit.r_bn_n_m_z": pos[2],
            "orbit.v_bn_n_m_s_x": vel[0],
            "orbit.v_bn_n_m_s_y": vel[1],
            "orbit.v_bn_n_m_s_z": vel[2],
            "orbit.radius_m": self.cartesian.radius_m,
            "orbit.altitude_m": self.cartesian.radius_m - earth_radius_m,
            "orbit.speed_m_s": self.cartesian.speed_m_s,
            "orbit.specific_energy_j_kg": self.energy_j_kg,
            "environment.sun_vector_n_x": self.sun_vector_eci[0],
            "environment.sun_vector_n_y": self.sun_vector_eci[1],
            "environment.sun_vector_n_z": self.sun_vector_eci[2],
            "environment.shadow_factor": self.shadow_factor,
            "environment.eclipse_flag": self.eclipse_flag,
            "orbit.force.central_gravity_norm_m_s2": self.force_norms_m_s2.get("central_gravity_norm_m_s2", 0.0),
            "orbit.force.j2_norm_m_s2": self.force_norms_m_s2.get("j2_norm_m_s2", 0.0),
            "orbit.force.drag_norm_m_s2": self.force_norms_m_s2.get("drag_norm_m_s2", 0.0),
            "orbit.force.srp_norm_m_s2": self.force_norms_m_s2.get("srp_norm_m_s2", 0.0),
            "orbit.force.total_norm_m_s2": self.force_norms_m_s2.get("total_norm_m_s2", 0.0),
            "ground.range_m": self.ground_range_m,
            "ground.elevation_deg": self.ground_elevation_deg,
            "ground.has_access": self.ground_has_access,
        }
        return out


def _derivative(t_s: float, state: tuple[float, ...], config: OrbitFidelityConfig) -> tuple[float, ...]:
    r = (state[0], state[1], state[2])
    v = (state[3], state[4], state[5])
    sun = analytic_sun_vector_eci(config.epoch_utc, t_s)
    contrib = force_contributions_m_s2(position_m=r, velocity_m_s=v, elapsed_s=t_s, epoch_utc=config.epoch_utc, config=config.force_model, sun_vector_eci=sun)
    a = contrib["total"]
    return (v[0], v[1], v[2], a[0], a[1], a[2])


def _step(t_s: float, state: tuple[float, ...], dt: float, config: OrbitFidelityConfig, method: str) -> tuple[float, ...]:
    if method == "euler":
        return _add_state(state, _derivative(t_s, state, config), dt)
    if method != "rk4":
        raise OrbitForceModelError("ORB-1 supports euler or rk4")
    k1 = _derivative(t_s, state, config)
    k2 = _derivative(t_s + dt / 2.0, _add_state(state, k1, dt / 2.0), config)
    k3 = _derivative(t_s + dt / 2.0, _add_state(state, k2, dt / 2.0), config)
    k4 = _derivative(t_s + dt, _add_state(state, k3, dt), config)
    return _combine_rk4(state, k1, k2, k3, k4, dt)


def propagate_orbit_fidelity(config: OrbitFidelityConfig, time_grid: TimeGrid | None = None) -> list[OrbitFidelitySample]:
    if not isinstance(config, OrbitFidelityConfig):
        raise OrbitForceModelError("config must be OrbitFidelityConfig")
    solver = config.solver or FixedStepSolverConfig(method="rk4", step_s=10.0, duration_s=300.0)
    if time_grid is None:
        time_grid = build_time_grid(duration_s=solver.duration_s, sample_s=solver.step_s, epoch_utc=config.epoch_utc, include_endpoint=solver.include_endpoint)
    state = config.initial_state_tuple()
    frame = FrameMetadata(frame_id=ECI, epoch_utc=config.epoch_utc, frame_orientation="J2000-like inertial axes; numerical osculating state")
    samples: list[OrbitFidelitySample] = []
    for idx, t in enumerate(time_grid.times_s):
        if idx > 0:
            prev_t = time_grid.times_s[idx - 1]
            state = _step(prev_t, state, t - prev_t, config, solver.method)
        r = (state[0], state[1], state[2])
        v = (state[3], state[4], state[5])
        sun = analytic_sun_vector_eci(config.epoch_utc, t)
        shadow, eclipse, _, _ = eclipse_shadow_factor(r, sun, earth_radius_m=config.force_model.earth_radius_m, enabled=config.enable_eclipse)
        contrib = force_contributions_m_s2(position_m=r, velocity_m_s=v, elapsed_s=t, epoch_utc=config.epoch_utc, config=config.force_model, sun_vector_eci=sun)
        norms = force_norms(contrib)
        energy = 0.5 * vector_norm(v) ** 2 - config.force_model.earth_mu_m3_s2 / vector_norm(r)
        utc = time_grid.utc_at(t).isoformat().replace("+00:00", "Z")
        ground = ground_access_geometry(r, ground_station=config.ground_station, epoch_utc=config.epoch_utc, elapsed_s=t, earth_radius_m=config.force_model.earth_radius_m)
        samples.append(OrbitFidelitySample(
            time_s=float(t),
            sample_index=idx,
            utc=utc,
            cartesian=CartesianState(position_m=r, velocity_m_s=v, frame=frame, epoch_utc=utc),
            sun_vector_eci=sun,
            shadow_factor=shadow,
            eclipse_flag=eclipse,
            force_norms_m_s2=norms,
            energy_j_kg=energy,
            ground_range_m=ground["range_m"],
            ground_elevation_deg=ground["elevation_deg"],
            ground_has_access=ground["has_access"],
            frame=frame,
        ))
    return samples


def summarize_orbit_fidelity(samples: Iterable[OrbitFidelitySample], config: OrbitFidelityConfig) -> dict[str, Any]:
    rows = list(samples)
    if not rows:
        raise OrbitForceModelError("cannot summarize empty ORB-1 sample list")
    radii = [s.cartesian.radius_m for s in rows]
    altitudes = [r - config.force_model.earth_radius_m for r in radii]
    speeds = [s.cartesian.speed_m_s for s in rows]
    energies = [s.energy_j_kg for s in rows]
    drag_norms = [s.force_norms_m_s2.get("drag_norm_m_s2", 0.0) for s in rows]
    j2_norms = [s.force_norms_m_s2.get("j2_norm_m_s2", 0.0) for s in rows]
    srp_norms = [s.force_norms_m_s2.get("srp_norm_m_s2", 0.0) for s in rows]
    access_values = [s.ground_has_access for s in rows if s.ground_has_access is not None]
    return {
        "schema_version": ORB1_NUMERICAL_ORBIT_SCHEMA_VERSION,
        "status": "complete",
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "model_family": "numerical_cowell_central_j2_drag_srp_proxy",
        "sample_count": len(rows),
        "duration_s": rows[-1].time_s,
        "sample_s": rows[1].time_s - rows[0].time_s if len(rows) > 1 else None,
        "qoi": {
            "orbit.altitude_min_m": min(altitudes),
            "orbit.altitude_max_m": max(altitudes),
            "orbit.altitude_span_m": max(altitudes) - min(altitudes),
            "orbit.altitude_delta_m": altitudes[-1] - altitudes[0],
            "orbit.speed_mean_m_s": sum(speeds) / len(speeds),
            "orbit.specific_energy_delta_j_kg": energies[-1] - energies[0],
            "orbit.force.j2_norm_mean_m_s2": sum(j2_norms) / len(j2_norms),
            "orbit.force.drag_norm_mean_m_s2": sum(drag_norms) / len(drag_norms),
            "orbit.force.srp_norm_mean_m_s2": sum(srp_norms) / len(srp_norms),
            "environment.eclipse_fraction": sum(1 for s in rows if s.eclipse_flag) / len(rows),
            "ground.access_fraction": (sum(1 for v in access_values if v) / len(access_values)) if access_values else None,
        },
        "config": config.to_dict(),
        "known_physics_limits": [
            "Drag uses an exponential atmosphere proxy, not NRLMSISE-00 or density assimilation.",
            "SRP is a compact cannonball proxy, not a detailed optical surface model.",
            "Third-body gravity, SPICE/truth ephemerides, and calibrated force-model tolerances are not implemented.",
            "Benchmarks are internal regression envelopes, not external flight-data validation.",
        ],
    }


def build_orb1_orbit_fidelity_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        cfg = OrbitFidelityConfig.from_task_spec(spec) if spec else None
        config_payload = cfg.to_dict() if cfg else None
        status = "implemented_orbit_fidelity_numerical_prototype_not_flight_grade"
        validation_status = "config_valid" if cfg else "metadata_only"
    except Exception as exc:
        config_payload = {"error": str(exc)}
        status = "configured_with_validation_error"
        validation_status = "config_invalid"
    return {
        "schema_version": ORB1_NUMERICAL_ORBIT_SCHEMA_VERSION,
        "route_version": "ORB-1",
        "status": status,
        "validation_status": validation_status,
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "implemented_models": [
            "Cowell-style Cartesian numerical propagation",
            "central gravity acceleration",
            "J2 acceleration",
            "exponential atmospheric drag proxy",
            "optional SRP cannonball proxy",
            "force contribution trace",
        ],
        "unsupported_models": [
            "third-body gravity",
            "SPICE/truth ephemerides",
            "calibrated flight-grade tolerance envelopes",
            "NRLMSISE-00 / assimilated atmosphere",
        ],
        "config": config_payload,
    }


__all__ = [
    "ORB1_NUMERICAL_ORBIT_SCHEMA_VERSION",
    "OrbitFidelityConfig",
    "OrbitFidelitySample",
    "propagate_orbit_fidelity",
    "summarize_orbit_fidelity",
    "build_orb1_orbit_fidelity_payload",
]
