"""HF-7 propulsion/orbit/attitude coupled spacecraft model.

This Route-B model couples a deterministic finite burn event to an HF-3
medium-fidelity orbit trace and a compact attitude-disturbance proxy.  It is an
auditable engineering prototype; it is not a full 6-DOF force/torque integration
or high-fidelity propulsion model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
import math

from sat_sim.frames import vector_norm, normalize_vector
from sat_sim.orbit import MediumOrbitConfig, propagate_medium_orbit_environment
from sat_sim.time_systems import build_time_grid
from sat_sim.validation import build_hf8_physical_validation_payload, evaluate_physical_validation

HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION = "hf7.propulsion_orbit_attitude.v1"
G0_M_S2 = 9.80665


class PropulsionOrbitAttitudeCoupledError(ValueError):
    """Raised when HF-7 coupled model inputs are invalid."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PropulsionOrbitAttitudeCoupledError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise PropulsionOrbitAttitudeCoupledError(f"{name} must be positive")
    return out


def _nonnegative(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out < 0.0:
        raise PropulsionOrbitAttitudeCoupledError(f"{name} must be non-negative")
    return out


def _ratio(value: Any, name: str) -> float:
    out = _finite(value, name)
    if not 0.0 <= out <= 1.0:
        raise PropulsionOrbitAttitudeCoupledError(f"{name} must be in [0, 1]")
    return out


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(*mappings: Mapping[str, Any], key: str, default: Any) -> Any:
    for mapping in mappings:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return default


def _vec3(value: Any, name: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if value is None:
        return default
    try:
        items = tuple(float(x) for x in value)
    except Exception as exc:  # pragma: no cover - defensive
        raise PropulsionOrbitAttitudeCoupledError(f"{name} must be a 3-vector") from exc
    if len(items) != 3 or any(not math.isfinite(x) for x in items):
        raise PropulsionOrbitAttitudeCoupledError(f"{name} must contain exactly three finite numbers")
    return items  # type: ignore[return-value]


def _unit_or_default(value: Any, name: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    raw = _vec3(value, name, default)
    if vector_norm(raw) <= 1.0e-12:
        return default
    return normalize_vector(raw, name)


def _scale(v: tuple[float, float, float], s: float) -> tuple[float, float, float]:
    return (v[0] * s, v[1] * s, v[2] * s)


def _add(a: tuple[float, float, float], b: tuple[float, float, float]) -> tuple[float, float, float]:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _safe_unit(v: tuple[float, float, float], default: tuple[float, float, float] = (1.0, 0.0, 0.0)) -> tuple[float, float, float]:
    if vector_norm(v) <= 1.0e-12:
        return default
    return normalize_vector(v, "vector")


@dataclass(frozen=True)
class PropulsionOrbitAttitudeCoupledConfig:
    """Configuration for the HF-7 coupled propulsion/orbit/attitude model."""

    duration_s: float = 3600.0
    sample_s: float = 20.0
    epoch_utc: str = "2026-07-05T00:00:00Z"
    dry_mass_kg: float = 120.0
    initial_propellant_kg: float = 8.0
    thrust_n: float = 0.45
    specific_impulse_s: float = 220.0
    burn_start_s: float = 600.0
    burn_duration_s: float = 180.0
    thrust_efficiency: float = 0.96
    burn_direction: str = "prograde"
    burn_direction_eci: tuple[float, float, float] = (1.0, 0.0, 0.0)
    initial_pointing_error_deg: float = 0.5
    attitude_damping_rate_1_s: float = 0.0015
    disturbance_gain_deg_per_m_s: float = 0.22
    angular_rate_gain_rad_s_per_m_s: float = 8.0e-5
    disturbance_torque_arm_m: float = 0.05
    attitude_control_available: bool = True
    orbit_config: MediumOrbitConfig | None = None

    def __post_init__(self) -> None:
        for key in ("duration_s", "sample_s", "dry_mass_kg", "specific_impulse_s"):
            object.__setattr__(self, key, _positive(getattr(self, key), key))
        if self.sample_s > self.duration_s:
            raise PropulsionOrbitAttitudeCoupledError("sample_s must not exceed duration_s")
        for key in ("initial_propellant_kg", "thrust_n", "burn_start_s", "burn_duration_s", "initial_pointing_error_deg", "attitude_damping_rate_1_s", "disturbance_gain_deg_per_m_s", "angular_rate_gain_rad_s_per_m_s", "disturbance_torque_arm_m"):
            object.__setattr__(self, key, _nonnegative(getattr(self, key), key))
        object.__setattr__(self, "thrust_efficiency", _ratio(self.thrust_efficiency, "thrust_efficiency"))
        if self.burn_start_s > self.duration_s:
            raise PropulsionOrbitAttitudeCoupledError("burn_start_s must not exceed duration_s")
        burn_direction = str(self.burn_direction or "prograde").strip().lower()
        if burn_direction not in {"prograde", "retrograde", "radial_out", "radial_in", "normal", "anti_normal", "eci_vector"}:
            raise PropulsionOrbitAttitudeCoupledError("burn_direction must be prograde/retrograde/radial_out/radial_in/normal/anti_normal/eci_vector")
        object.__setattr__(self, "burn_direction", burn_direction)
        object.__setattr__(self, "burn_direction_eci", _unit_or_default(self.burn_direction_eci, "burn_direction_eci", (1.0, 0.0, 0.0)))

    @property
    def initial_mass_kg(self) -> float:
        return self.dry_mass_kg + self.initial_propellant_kg

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "PropulsionOrbitAttitudeCoupledConfig":
        sim = _mapping(spec.get("simulation"))
        params = _mapping(spec.get("parameters"))
        spacecraft = _mapping(spec.get("spacecraft"))
        propulsion = _mapping(spacecraft.get("propulsion"))
        adcs = _mapping(spacecraft.get("adcs"))
        orbit = _mapping(spec.get("orbit_environment"))
        duration_s = float(sim.get("duration_s", 3600.0))
        sample_s = float(sim.get("sample_s", 20.0))
        epoch_utc = str(sim.get("epoch_utc") or "2026-07-05T00:00:00Z")
        orbit_spec = dict(spec)
        orbit_spec["task_type"] = "orbit_environment"
        orbit_spec["capability_id"] = "orbit_environment.medium_fidelity.v1"
        orbit_spec["simulation"] = {**dict(sim), "duration_s": duration_s, "sample_s": sample_s, "epoch_utc": epoch_utc}
        orbit_spec["target"] = {"level": "integrated", "name": "orbit_environment", "mode": "nominal"}
        orbit_spec["orbit_environment"] = dict(orbit) if orbit else {
            "altitude_m": 500_000.0,
            "inclination_deg": 51.6,
            "enable_eclipse": True,
            "sun_model": "constant",
            "sun_vector_eci": [1.0, 0.0, 0.0],
        }
        orbit_config = MediumOrbitConfig.from_task_spec(orbit_spec)
        return cls(
            duration_s=duration_s,
            sample_s=sample_s,
            epoch_utc=epoch_utc,
            dry_mass_kg=float(_first(params, propulsion, key="dry_mass_kg", default=120.0)),
            initial_propellant_kg=float(_first(params, propulsion, key="initial_propellant_kg", default=_first(params, propulsion, key="propellant_mass_kg", default=8.0))),
            thrust_n=float(_first(params, propulsion, key="thrust_n", default=0.45)),
            specific_impulse_s=float(_first(params, propulsion, key="specific_impulse_s", default=_first(params, propulsion, key="isp_s", default=220.0))),
            burn_start_s=float(_first(params, propulsion, key="burn_start_s", default=600.0)),
            burn_duration_s=float(_first(params, propulsion, key="burn_duration_s", default=180.0)),
            thrust_efficiency=float(_first(params, propulsion, key="thrust_efficiency", default=0.96)),
            burn_direction=str(_first(params, propulsion, key="burn_direction", default="prograde")),
            burn_direction_eci=_vec3(_first(params, propulsion, key="burn_direction_eci", default=(1.0, 0.0, 0.0)), "burn_direction_eci", (1.0, 0.0, 0.0)),
            initial_pointing_error_deg=float(_first(params, adcs, key="initial_pointing_error_deg", default=0.5)),
            attitude_damping_rate_1_s=float(_first(params, adcs, key="attitude_damping_rate_1_s", default=0.0015)),
            disturbance_gain_deg_per_m_s=float(_first(params, adcs, key="disturbance_gain_deg_per_m_s", default=0.22)),
            angular_rate_gain_rad_s_per_m_s=float(_first(params, adcs, key="angular_rate_gain_rad_s_per_m_s", default=8.0e-5)),
            disturbance_torque_arm_m=float(_first(params, adcs, key="disturbance_torque_arm_m", default=0.05)),
            attitude_control_available=bool(_first(params, adcs, key="attitude_control_available", default=True)),
            orbit_config=orbit_config,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {k: v for k, v in self.__dict__.items() if k != "orbit_config"}
        payload["burn_direction_eci"] = list(self.burn_direction_eci)
        payload["initial_mass_kg"] = self.initial_mass_kg
        payload["orbit_config"] = self.orbit_config.to_dict() if self.orbit_config else None
        return payload


def _burn_direction_eci(config: PropulsionOrbitAttitudeCoupledConfig, r_eci: tuple[float, float, float], v_eci: tuple[float, float, float]) -> tuple[float, float, float]:
    radial = _safe_unit(r_eci)
    prograde = _safe_unit(v_eci)
    normal = _safe_unit((r_eci[1] * v_eci[2] - r_eci[2] * v_eci[1], r_eci[2] * v_eci[0] - r_eci[0] * v_eci[2], r_eci[0] * v_eci[1] - r_eci[1] * v_eci[0]), (0.0, 0.0, 1.0))
    if config.burn_direction == "prograde":
        return prograde
    if config.burn_direction == "retrograde":
        return _scale(prograde, -1.0)
    if config.burn_direction == "radial_out":
        return radial
    if config.burn_direction == "radial_in":
        return _scale(radial, -1.0)
    if config.burn_direction == "normal":
        return normal
    if config.burn_direction == "anti_normal":
        return _scale(normal, -1.0)
    return config.burn_direction_eci


def propagate_propulsion_orbit_attitude_coupled(config: PropulsionOrbitAttitudeCoupledConfig) -> list[dict[str, Any]]:
    """Run HF-7 propulsion/orbit/attitude coupling and return trace rows."""

    grid = build_time_grid(duration_s=config.duration_s, sample_s=config.sample_s, epoch_utc=config.epoch_utc)
    orbit_config = config.orbit_config or MediumOrbitConfig.from_task_spec({"simulation": {"duration_s": config.duration_s, "sample_s": config.sample_s, "epoch_utc": config.epoch_utc}, "orbit_environment": {"altitude_m": 500_000.0}})
    orbit_samples = propagate_medium_orbit_environment(orbit_config, time_grid=grid)
    rows: list[dict[str, Any]] = []
    propellant_kg = config.initial_propellant_kg
    cumulative_delta_v_m_s = 0.0
    pointing_error_deg = config.initial_pointing_error_deg
    angular_rate_rad_s = 0.0
    previous_t = 0.0
    for idx, sample in enumerate(orbit_samples):
        t = float(sample.time_s)
        dt = 0.0 if idx == 0 else max(0.0, t - previous_t)
        previous_t = t
        burn_active = (config.burn_start_s <= t < config.burn_start_s + config.burn_duration_s) and propellant_kg > 0.0 and config.thrust_n > 0.0 and dt > 0.0
        _mass_kg = config.dry_mass_kg + propellant_kg
        step_delta_v = 0.0
        propellant_used_step = 0.0
        thrust_n = config.thrust_n if burn_active else 0.0
        if burn_active:
            requested_propellant = config.thrust_n * dt / (config.specific_impulse_s * G0_M_S2)
            propellant_used_step = min(propellant_kg, requested_propellant)
            effective_burn_time = propellant_used_step * config.specific_impulse_s * G0_M_S2 / config.thrust_n if config.thrust_n > 0.0 else 0.0
            average_mass = max(config.dry_mass_kg + propellant_kg - 0.5 * propellant_used_step, config.dry_mass_kg)
            step_delta_v = config.thrust_efficiency * config.thrust_n * effective_burn_time / average_mass
            propellant_kg -= propellant_used_step
            cumulative_delta_v_m_s += step_delta_v
        direction = _burn_direction_eci(config, sample.cartesian.position_m, sample.cartesian.velocity_m_s)
        delta_v_vec = _scale(direction, cumulative_delta_v_m_s)
        adjusted_velocity = _add(sample.cartesian.velocity_m_s, delta_v_vec)
        adjusted_speed = vector_norm(adjusted_velocity)
        base_speed = sample.cartesian.speed_m_s
        attitude_kick = config.disturbance_gain_deg_per_m_s * step_delta_v * (1.0 + 0.25 * config.disturbance_torque_arm_m)
        pointing_error_deg += attitude_kick
        if config.attitude_control_available and dt > 0.0:
            # First-order damping to represent a basic attitude-control response.
            pointing_error_deg *= math.exp(-config.attitude_damping_rate_1_s * dt)
            angular_rate_rad_s *= math.exp(-0.5 * config.attitude_damping_rate_1_s * dt)
        angular_rate_rad_s += config.angular_rate_gain_rad_s_per_m_s * step_delta_v
        row = sample.to_trace_row(
            task_id="",
            case_id="",
            capability_id="whole_spacecraft.maneuver_orbit_attitude.v1",
            mode="nominal",
            target_level="whole_spacecraft",
            target_name="maneuver_orbit_attitude",
            earth_radius_m=orbit_config.earth_radius_m,
        )
        row.update({
            "fidelity_level": "medium",
            "propulsion.burn.active": bool(burn_active),
            "propulsion.thrust_n": thrust_n,
            "propulsion.specific_impulse_s": config.specific_impulse_s,
            "propulsion.propellant.remaining_kg": propellant_kg,
            "propulsion.propellant.used_step_kg": propellant_used_step,
            "propulsion.delta_v.step_m_s": step_delta_v,
            "propulsion.delta_v.cumulative_m_s": cumulative_delta_v_m_s,
            "propulsion.mass.total_kg": config.dry_mass_kg + propellant_kg,
            "propulsion.burn.direction_x": direction[0],
            "propulsion.burn.direction_y": direction[1],
            "propulsion.burn.direction_z": direction[2],
            "orbit.v_burn_adjusted_m_s_x": adjusted_velocity[0],
            "orbit.v_burn_adjusted_m_s_y": adjusted_velocity[1],
            "orbit.v_burn_adjusted_m_s_z": adjusted_velocity[2],
            "orbit.speed_adjusted_m_s": adjusted_speed,
            "orbit.speed_delta_proxy_m_s": adjusted_speed - base_speed,
            "adcs.pointing_error_deg": max(0.0, pointing_error_deg),
            "adcs.angular_rate_disturbance_rad_s": max(0.0, angular_rate_rad_s),
            "adcs.disturbance_torque_proxy_n_m": thrust_n * config.disturbance_torque_arm_m,
            "label.burn_active": bool(burn_active),
            "label.attitude_control_available": bool(config.attitude_control_available),
        })
        rows.append(row)
    return rows


def summarize_propulsion_orbit_attitude_coupled(rows: Iterable[Mapping[str, Any]], config: PropulsionOrbitAttitudeCoupledConfig) -> dict[str, Any]:
    trace = [dict(row) for row in rows]
    if not trace:
        raise PropulsionOrbitAttitudeCoupledError("cannot summarize empty HF-7 trace")
    prop_values = [float(row["propulsion.propellant.remaining_kg"]) for row in trace if row.get("propulsion.propellant.remaining_kg") is not None]
    delta_v_values = [float(row["propulsion.delta_v.cumulative_m_s"]) for row in trace if row.get("propulsion.delta_v.cumulative_m_s") is not None]
    pointing_values = [float(row["adcs.pointing_error_deg"]) for row in trace if row.get("adcs.pointing_error_deg") is not None]
    fuel_monotonic = all(b <= a + 1.0e-9 for a, b in zip(prop_values, prop_values[1:])) if len(prop_values) >= 2 else True
    burn_count = sum(1 for row in trace if row.get("propulsion.burn.active"))
    return {
        "schema_version": HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION,
        "status": "complete",
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "model_family": "finite_burn_delta_v_proxy_with_attitude_disturbance_proxy",
        "sample_count": len(trace),
        "duration_s": trace[-1].get("time_s"),
        "qoi": {
            "propulsion.total_delta_v_m_s": delta_v_values[-1] if delta_v_values else 0.0,
            "propulsion.propellant_used_kg": config.initial_propellant_kg - (prop_values[-1] if prop_values else config.initial_propellant_kg),
            "propulsion.final_propellant_kg": prop_values[-1] if prop_values else config.initial_propellant_kg,
            "propulsion.burn_step_count": burn_count,
            "propulsion.fuel_monotonic": fuel_monotonic,
            "orbit.speed_delta_proxy_final_m_s": float(trace[-1].get("orbit.speed_delta_proxy_m_s") or 0.0),
            "adcs.initial_pointing_error_deg": pointing_values[0] if pointing_values else None,
            "adcs.final_pointing_error_deg": pointing_values[-1] if pointing_values else None,
            "adcs.max_pointing_error_deg": max(pointing_values) if pointing_values else None,
        },
        "config": config.to_dict(),
        "known_physics_limits": [
            "Finite burn is represented as a deterministic delta-v proxy, not a force-model numerical integration.",
            "Attitude disturbance is a compact torque/pointing-error proxy, not a full 6-DOF coupled rigid-body simulation.",
            "Thruster plume, slosh, tank pressure, valve transients, and detailed actuator dynamics are not modeled.",
        ],
    }


def build_hf7_propulsion_orbit_attitude_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Return serializable HF-7 metadata for manifests/readiness reports."""

    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        config = PropulsionOrbitAttitudeCoupledConfig.from_task_spec(spec) if spec else None
        config_payload = config.to_dict() if config else None
        status = "implemented_medium_fidelity_not_high_fidelity"
        validation_status = "config_valid" if config else "metadata_only"
    except Exception as exc:
        config_payload = {"error": str(exc)}
        status = "configured_with_validation_error"
        validation_status = "config_invalid"
    return {
        "schema_version": HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION,
        "route_b_version": "B-5/HF-7",
        "foundation_dependency": "B-1/HF-1+HF-2",
        "orbit_dependency": "B-2/HF-3",
        "validation_dependency": "B-5/HF-8",
        "status": status,
        "validation_status": validation_status,
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "implemented_models": [
            "finite thruster burn event window",
            "propellant mass monotonic update",
            "specific-impulse propellant consumption",
            "delta-v proxy applied to HF-3 orbit velocity trace",
            "compact attitude disturbance and control damping proxy",
        ],
        "uses_hf_foundations": [
            "hf1.time_systems.TimeGrid",
            "hf2.state Cartesian orbit trace metadata",
            "hf3.orbit_environment.medium_fidelity.v1",
            "hf8.physical_validation.v1 fuel/orbit/energy gates",
        ],
        "config": config_payload,
        "hf8_physical_validation": build_hf8_physical_validation_payload(spec),
        "remaining_route_b_dependencies": ["HF-9 benchmark scenarios and tolerance envelopes"],
    }


def build_hf7_readiness_matrix(*, capability_count: int | None = None, route_b_model_library_capability_count: int | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "schema_version": HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION,
        "route_b_version": "B-5/HF-7",
        "propulsion_orbit_attitude_coupling_status": "implemented_medium_fidelity_proxy",
        "high_fidelity_ready_count": 0,
        "can_claim_package_high_fidelity": False,
        "remaining_route_b_dependencies": ["HF-9 benchmark scenarios and tolerance envelopes"],
    }
    if capability_count is not None:
        out["capability_count"] = int(capability_count)
    if route_b_model_library_capability_count is not None:
        out["route_b_model_library_capability_count"] = int(route_b_model_library_capability_count)
    return out


__all__ = [
    "HF7_PROPULSION_ORBIT_ATTITUDE_SCHEMA_VERSION",
    "G0_M_S2",
    "PropulsionOrbitAttitudeCoupledConfig",
    "PropulsionOrbitAttitudeCoupledError",
    "build_hf7_propulsion_orbit_attitude_payload",
    "build_hf7_readiness_matrix",
    "propagate_propulsion_orbit_attitude_coupled",
    "summarize_propulsion_orbit_attitude_coupled",
]
