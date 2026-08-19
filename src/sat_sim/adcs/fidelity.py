"""ADCS-1 fidelity-upgrade primitives.

This module builds on the HF-4 closed-loop ADCS model and adds auditable
engineering-prototype fidelity features: environment-torque proxies, sensor
bias/noise/dropout metadata, controller mode trace, and wheel momentum metrics.
It remains an internal engineering benchmark model, not a full FSW or calibrated
sensor/actuator implementation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence
import math

from sat_sim.adcs.closed_loop import (
    ADCSClosedLoopConfig,
    ADCSClosedLoopError,
    ADCSClosedLoopSample,
    RuntimeEffectResolver,
    propagate_adcs_closed_loop,
    summarize_adcs_closed_loop,
)
from sat_sim.frames import vector_norm

ADCS_FIDELITY_SCHEMA_VERSION = "adcs1.adcs_fidelity.v1"


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _vec3(value: Any, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if value is None:
        return default
    if _is_number(value):
        return (0.0, 0.0, float(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        vals = tuple(float(x) for x in value)
        if len(vals) == 3 and all(math.isfinite(x) for x in vals):
            return vals  # type: ignore[return-value]
    raise ADCSClosedLoopError("vector must contain exactly three finite numbers")


def _add(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    return (float(a[0]) + float(b[0]), float(a[1]) + float(b[1]), float(a[2]) + float(b[2]))


@dataclass(frozen=True)
class ADCSEnvironmentTorqueConfig:
    """Proxy environmental torque configuration."""

    gravity_gradient_enabled: bool = True
    magnetic_enabled: bool = True
    aerodynamic_enabled: bool = False
    srp_enabled: bool = False
    residual_dipole_am2: tuple[float, float, float] = (0.08, 0.0, 0.02)
    magnetic_field_t: tuple[float, float, float] = (0.0, 0.0, 3.0e-5)
    aero_dynamic_pressure_pa: float = 2.0e-7
    aero_area_m2: float = 0.8
    aero_cp_offset_m: tuple[float, float, float] = (0.02, 0.0, 0.01)
    srp_pressure_pa: float = 4.56e-6
    srp_area_m2: float = 0.7
    srp_cp_offset_m: tuple[float, float, float] = (0.01, 0.0, 0.02)

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "ADCSEnvironmentTorqueConfig":
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        env = params.get("environment_torques") if isinstance(params.get("environment_torques"), Mapping) else {}
        sensor = params.get("magnetic_field_t")
        return cls(
            gravity_gradient_enabled=bool(env.get("gravity_gradient", params.get("gravity_gradient_enabled", True))),
            magnetic_enabled=bool(env.get("magnetic", params.get("magnetic_torque_enabled", True))),
            aerodynamic_enabled=bool(env.get("aerodynamic", params.get("aerodynamic_torque_enabled", False))),
            srp_enabled=bool(env.get("srp", params.get("srp_torque_enabled", False))),
            residual_dipole_am2=_vec3(params.get("residual_dipole_am2", env.get("residual_dipole_am2")), (0.08, 0.0, 0.02)),
            magnetic_field_t=_vec3(sensor or env.get("magnetic_field_t"), (0.0, 0.0, 3.0e-5)),
            aero_dynamic_pressure_pa=float(params.get("aero_dynamic_pressure_pa", env.get("aero_dynamic_pressure_pa", 2.0e-7))),
            aero_area_m2=float(params.get("aero_area_m2", env.get("aero_area_m2", 0.8))),
            aero_cp_offset_m=_vec3(params.get("aero_cp_offset_m", env.get("aero_cp_offset_m")), (0.02, 0.0, 0.01)),
            srp_pressure_pa=float(params.get("srp_pressure_pa", env.get("srp_pressure_pa", 4.56e-6))),
            srp_area_m2=float(params.get("srp_area_m2", env.get("srp_area_m2", 0.7))),
            srp_cp_offset_m=_vec3(params.get("srp_cp_offset_m", env.get("srp_cp_offset_m")), (0.01, 0.0, 0.02)),
        )

    def components_nm(self, inertia_kg_m2: Sequence[float] = (12.0, 10.0, 8.0)) -> dict[str, tuple[float, float, float]]:
        out: dict[str, tuple[float, float, float]] = {}
        if self.gravity_gradient_enabled:
            ix, iy, iz = (float(x) for x in inertia_kg_m2)
            denom = max(ix + iy + iz, 1.0)
            out["gravity_gradient"] = (0.0, 3.0e-7 * (iz - ix) / denom, 1.5e-7 * (iy - ix) / denom)
        else:
            out["gravity_gradient"] = (0.0, 0.0, 0.0)
        if self.magnetic_enabled:
            mx, my, mz = self.residual_dipole_am2
            bx, by, bz = self.magnetic_field_t
            out["magnetic"] = (my * bz - mz * by, mz * bx - mx * bz, mx * by - my * bx)
        else:
            out["magnetic"] = (0.0, 0.0, 0.0)
        if self.aerodynamic_enabled:
            _cx, cy, cz = self.aero_cp_offset_m
            force = self.aero_dynamic_pressure_pa * self.aero_area_m2
            out["aerodynamic"] = (0.0, cz * force, -cy * force)
        else:
            out["aerodynamic"] = (0.0, 0.0, 0.0)
        if self.srp_enabled:
            _cx, cy, cz = self.srp_cp_offset_m
            force = self.srp_pressure_pa * self.srp_area_m2
            out["srp"] = (0.0, cz * force, -cy * force)
        else:
            out["srp"] = (0.0, 0.0, 0.0)
        return out

    def total_nm(self, inertia_kg_m2: Sequence[float] = (12.0, 10.0, 8.0)) -> tuple[float, float, float]:
        total = (0.0, 0.0, 0.0)
        for value in self.components_nm(inertia_kg_m2).values():
            total = _add(total, value)
        return total

    def to_dict(self, inertia_kg_m2: Sequence[float] = (12.0, 10.0, 8.0)) -> dict[str, Any]:
        comps = self.components_nm(inertia_kg_m2)
        total = self.total_nm(inertia_kg_m2)
        return {
            "enabled": {"gravity_gradient": self.gravity_gradient_enabled, "magnetic": self.magnetic_enabled, "aerodynamic": self.aerodynamic_enabled, "srp": self.srp_enabled},
            "components_nm": {k: list(v) for k, v in comps.items()},
            "total_nm": list(total),
            "total_norm_nm": vector_norm(total),
            "proxy_model": "constant_body_disturbance_torque_from_environment_terms",
        }


@dataclass(frozen=True)
class ADCSSensorFidelityConfig:
    gyro_bias_rad_s: tuple[float, float, float]
    gyro_noise_std_rad_s: float
    star_tracker_noise_rad: float
    sun_sensor_noise_rad: float
    dropout_start_s: float | None = None
    dropout_duration_s: float = 0.0

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "ADCSSensorFidelityConfig":
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        dropout = params.get("sensor_dropout") if isinstance(params.get("sensor_dropout"), Mapping) else {}
        gyro_bias = params.get("gyro_bias_rad_s") or params.get("gyro_bias_deg_s") or (0.0, 0.0, 0.0)
        if "gyro_bias_deg_s" in params and "gyro_bias_rad_s" not in params:
            gyro_bias = tuple(math.radians(float(x)) for x in _vec3(gyro_bias, (0.0, 0.0, 0.0)))
        start = dropout.get("start_s") if dropout else params.get("sensor_dropout_start_s")
        dur = dropout.get("duration_s") if dropout else params.get("sensor_dropout_duration_s", 0.0)
        return cls(
            gyro_bias_rad_s=_vec3(gyro_bias, (0.0, 0.0, 0.0)),
            gyro_noise_std_rad_s=float(params.get("gyro_noise_std_rad_s", math.radians(float(params.get("gyro_noise_std_deg_s", 0.0))))),
            star_tracker_noise_rad=math.radians(float(params.get("star_tracker_noise_deg", 0.002))),
            sun_sensor_noise_rad=math.radians(float(params.get("sun_sensor_noise_deg", 0.2))),
            dropout_start_s=float(start) if start is not None else None,
            dropout_duration_s=float(dur or 0.0),
        )

    def is_dropout(self, time_s: float) -> bool:
        return self.dropout_start_s is not None and self.dropout_duration_s > 0.0 and self.dropout_start_s <= float(time_s) <= self.dropout_start_s + self.dropout_duration_s

    def to_dict(self) -> dict[str, Any]:
        return {"gyro_bias_rad_s": list(self.gyro_bias_rad_s), "gyro_noise_std_rad_s": self.gyro_noise_std_rad_s, "star_tracker_noise_rad": self.star_tracker_noise_rad, "sun_sensor_noise_rad": self.sun_sensor_noise_rad, "dropout_start_s": self.dropout_start_s, "dropout_duration_s": self.dropout_duration_s}


@dataclass(frozen=True)
class ADCSFidelityConfig:
    closed_loop: ADCSClosedLoopConfig
    environment_torques: ADCSEnvironmentTorqueConfig
    sensors: ADCSSensorFidelityConfig
    wheel_configuration: str = "orthogonal_3"
    wheel_allocation_method: str = "weighted_pseudoinverse"

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "ADCSFidelityConfig":
        env = ADCSEnvironmentTorqueConfig.from_task_spec(spec)
        params = dict(spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {})
        base_dist = _vec3(params.get("disturbance_torque_nm", params.get("external_disturbance_torque_nm")), (0.0, 0.0, 0.0))
        tmp_spec = dict(spec)
        tmp_spec["parameters"] = params
        preliminary = ADCSClosedLoopConfig.from_task_spec(tmp_spec)
        total_dist = _add(base_dist, env.total_nm(preliminary.inertia_kg_m2))
        params["disturbance_torque_nm"] = list(total_dist)
        params.setdefault("star_tracker_available", True)
        params.setdefault("sun_sensor_available", True)
        tmp_spec["parameters"] = params
        return cls(
            closed_loop=ADCSClosedLoopConfig.from_task_spec(tmp_spec),
            environment_torques=env,
            sensors=ADCSSensorFidelityConfig.from_task_spec(tmp_spec),
            wheel_configuration=ADCSClosedLoopConfig.from_task_spec(tmp_spec).reaction_wheels.configuration,
            wheel_allocation_method=ADCSClosedLoopConfig.from_task_spec(tmp_spec).reaction_wheels.allocation_method,
        )

    def to_dict(self) -> dict[str, Any]:
        return {"schema_version": ADCS_FIDELITY_SCHEMA_VERSION, "closed_loop": self.closed_loop.to_dict(), "environment_torques": self.environment_torques.to_dict(self.closed_loop.inertia_kg_m2), "sensors": self.sensors.to_dict(), "wheel_configuration": self.wheel_configuration, "wheel_allocation_method": self.wheel_allocation_method}


def propagate_adcs_fidelity(config: ADCSFidelityConfig, effect_resolver: RuntimeEffectResolver | None = None) -> tuple[ADCSClosedLoopSample, ...]:
    return propagate_adcs_closed_loop(config.closed_loop, effect_resolver=effect_resolver)


def _dropout_count(samples: Sequence[ADCSClosedLoopSample], sensors: ADCSSensorFidelityConfig) -> int:
    return sum(1 for s in samples if sensors.is_dropout(s.time_s))


def augment_trace_rows(rows: Sequence[dict[str, Any]], config: ADCSFidelityConfig) -> tuple[dict[str, Any], ...]:
    env = config.environment_torques.to_dict(config.closed_loop.inertia_kg_m2)
    out = []
    for row in rows:
        r = dict(row)
        wheel_count = config.closed_loop.reaction_wheels.num_wheels
        wheel = [float(r.get(f"adcs.rw.speed_rad_s_{i}", 0.0)) for i in range(wheel_count)]
        momentum = [config.closed_loop.reaction_wheels.wheel_inertia_kg_m2 * x for x in wheel]
        r["fidelity_level"] = "medium"
        r["adcs.model"] = "adcs1_fidelity_proxy"
        r["adcs.environment.total_torque_norm_nm"] = env["total_norm_nm"]
        r["adcs.environment.gravity_gradient_enabled"] = config.environment_torques.gravity_gradient_enabled
        r["adcs.environment.magnetic_enabled"] = config.environment_torques.magnetic_enabled
        r["adcs.environment.aerodynamic_enabled"] = config.environment_torques.aerodynamic_enabled
        r["adcs.environment.srp_enabled"] = config.environment_torques.srp_enabled
        r["adcs.sensor.star_tracker_noise_rad"] = config.sensors.star_tracker_noise_rad
        r["adcs.sensor.sun_sensor_noise_rad"] = config.sensors.sun_sensor_noise_rad
        r["adcs.sensor.dropout_flag"] = config.sensors.is_dropout(float(r.get("time_s", 0.0)))
        for i, value in enumerate(momentum):
            r[f"adcs.rw.momentum_nms_{i}"] = value
        r["adcs.rw.max_abs_momentum_nms"] = max(abs(x) for x in momentum)
        r["adcs.control.wheel_configuration"] = config.wheel_configuration
        r["adcs.control.wheel_allocation_method"] = config.wheel_allocation_method
        out.append(r)
    return tuple(out)


def summarize_adcs_fidelity(samples: Sequence[ADCSClosedLoopSample], config: ADCSFidelityConfig) -> dict[str, Any]:
    base = summarize_adcs_closed_loop(samples, config.closed_loop)
    env = config.environment_torques.to_dict(config.closed_loop.inertia_kg_m2)
    wheel_momentum = [max(abs(x) for x in s.wheel_speed_rad_s) * config.closed_loop.reaction_wheels.wheel_inertia_kg_m2 for s in samples]
    qoi = dict(base.get("qoi", {}))
    qoi.update({
        "adcs.environment.total_torque_norm_nm": float(env["total_norm_nm"]),
        "adcs.sensor.dropout_count": _dropout_count(samples, config.sensors),
        "adcs.rw.max_abs_momentum_nms": max(wheel_momentum) if wheel_momentum else 0.0,
        "adcs.control.mode_trace_available": 1.0,
    })
    base.update({
        "schema_version": ADCS_FIDELITY_SCHEMA_VERSION,
        "fidelity_level": "medium",
        "model_family": "orbit_adcs_fidelity_candidate_adcs_proxy",
        "can_claim_high_fidelity": False,
        "reason_high_fidelity_still_blocked": "ADCS-1 adds environmental torque, sensor, actuator, and controller-mode proxies plus ADCS-2 benchmark envelopes, but it is not calibrated FSW or sensor/actuator hardware physics.",
        "qoi": qoi,
        "adcs1_fidelity": config.to_dict(),
        "known_physics_limits": ADCS_FIDELITY_LIMITS,
    })
    return base


ADCS_FIDELITY_LIMITS = [
    "Environmental torques are deterministic proxies, not a calibrated orbital disturbance model.",
    "Star tracker, sun sensor, and gyro are measurement proxies; no hardware calibration or detailed failure dynamics are modeled.",
    "Reaction-wheel allocation is a proxy and does not model motor electronics or a full wheel geometry matrix.",
    "Controller is still a deterministic PD/controller-mode proxy, not flight software.",
    "ADCS-2 benchmark envelopes are internal regression tolerances, not flight-data validation.",
]


def build_adcs1_fidelity_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        cfg = ADCSFidelityConfig.from_task_spec(spec)
        status = "implemented_adcs_fidelity_proxy_not_high_fidelity"
        cfg_payload = cfg.to_dict()
    except Exception as exc:
        status = "metadata_build_failed"
        cfg_payload = {"error": str(exc)}
    return {
        "schema_version": ADCS_FIDELITY_SCHEMA_VERSION,
        "route_version": "ADCS-1",
        "capability_id": "subsystem.adcs_fidelity.v1",
        "status": status,
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "implemented_model_features": [
            "environmental torque proxies: gravity-gradient, magnetic, aerodynamic, SRP",
            "gyro bias/noise and sensor dropout metadata",
            "star-tracker and sun-sensor measurement proxy metadata",
            "reaction-wheel momentum and allocation proxy trace",
            "controller mode trace and pointing target frame contract",
        ],
        "foundation_dependencies": ["HF-1 time grid", "HF-2 attitude state/frame/solver", "HF-4 closed-loop propagation", "HF-8 physical validation gates", "HF-9 benchmark reporting pattern"],
        "known_physics_limits": ADCS_FIDELITY_LIMITS,
        "config": cfg_payload,
    }


__all__ = [
    "ADCS_FIDELITY_SCHEMA_VERSION",
    "ADCS_FIDELITY_LIMITS",
    "ADCSEnvironmentTorqueConfig",
    "ADCSSensorFidelityConfig",
    "ADCSFidelityConfig",
    "build_adcs1_fidelity_payload",
    "propagate_adcs_fidelity",
    "summarize_adcs_fidelity",
    "augment_trace_rows",
]
