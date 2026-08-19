"""HF-4 basic closed-loop ADCS model primitives.

This module implements an auditable, deterministic, local Route-B model for
basic closed-loop attitude control.  It intentionally stays below an engineering
high-fidelity FSW/sensor claim: controller law, sensors, reaction-wheel assembly,
and rigid-body dynamics are simplified, but the state, units, frames, and solver
metadata are explicit and testable.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Callable, Iterable, Mapping, Sequence
import math

from sat_sim.frames import BODY, ECI, vector_norm
from sat_sim.solvers import FixedStepSolverConfig
from sat_sim.state import AttitudeState, normalize_quaternion, quaternion_norm
from sat_sim.time_systems import TimeGrid, build_time_grid
from sat_sim.units import convert_unit

HF4_ADCS_CLOSED_LOOP_SCHEMA_VERSION = "hf4.adcs_closed_loop.basic.v1"


class ADCSClosedLoopError(ValueError):
    """Raised when HF-4 closed-loop ADCS configuration is invalid."""


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float)) and math.isfinite(float(value))


def _positive(value: Any, name: str) -> float:
    if not _is_number(value) or float(value) <= 0.0:
        raise ADCSClosedLoopError(f"{name} must be a positive finite number")
    return float(value)


def _nonnegative(value: Any, name: str) -> float:
    if not _is_number(value) or float(value) < 0.0:
        raise ADCSClosedLoopError(f"{name} must be a non-negative finite number")
    return float(value)


def _vec3(value: Any, name: str, *, default: tuple[float, float, float] | None = None) -> tuple[float, float, float]:
    if value is None:
        if default is None:
            raise ADCSClosedLoopError(f"{name} must contain three finite numbers")
        return default
    if _is_number(value):
        # Scalar shorthand is applied to the body z axis for angle/rate-like inputs.
        return (0.0, 0.0, float(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = tuple(float(x) for x in value)
        if len(items) == 3 and all(math.isfinite(x) for x in items):
            return items  # type: ignore[return-value]
    raise ADCSClosedLoopError(f"{name} must contain exactly three finite numbers")


def _diag_inertia(value: Any) -> tuple[float, float, float]:
    if _is_number(value):
        v = _positive(value, "inertia_kg_m2")
        return (v, v, v)
    items = _vec3(value, "inertia_kg_m2", default=(12.0, 10.0, 8.0))
    if any(x <= 0.0 for x in items):
        raise ADCSClosedLoopError("inertia_kg_m2 entries must be positive")
    return items


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return float(sum(float(x) * float(y) for x, y in zip(a, b)))


def _cross(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float]:
    ax, ay, az = (float(x) for x in a)
    bx, by, bz = (float(x) for x in b)
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def _normalize_vec3(value: Iterable[float], name: str) -> tuple[float, float, float]:
    vec = tuple(float(x) for x in value)
    if len(vec) != 3 or any(not math.isfinite(x) for x in vec):
        raise ADCSClosedLoopError(f"{name} must contain three finite numbers")
    norm = vector_norm(vec)
    if norm <= 0.0:
        raise ADCSClosedLoopError(f"{name} norm must be positive")
    return (vec[0] / norm, vec[1] / norm, vec[2] / norm)


def _wheel_vector(value: Any, name: str, count: int, *, default: float = 60.0) -> tuple[float, ...]:
    """Normalize a scalar or wheel-length sequence to ``count`` finite values."""

    if count < 1:
        raise ADCSClosedLoopError(f"{name} wheel count must be positive")
    if value is None:
        return tuple(float(default) for _ in range(count))
    if _is_number(value):
        return tuple(float(value) for _ in range(count))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = tuple(float(x) for x in value)
        if len(items) == count and all(math.isfinite(x) for x in items):
            return items
        # Backward compatibility: old three-axis tasks can be opened with a
        # four-wheel geometry.  The fourth wheel starts at the mean old speed.
        if count == 4 and len(items) == 3 and all(math.isfinite(x) for x in items):
            return (*items, sum(items) / 3.0)
    raise ADCSClosedLoopError(f"{name} must be a scalar or contain exactly {count} finite numbers")


def _invert_3x3(matrix: Sequence[Sequence[float]]) -> tuple[tuple[float, float, float], ...]:
    a, b, c = (float(x) for x in matrix[0])
    d, e, f = (float(x) for x in matrix[1])
    g, h, i = (float(x) for x in matrix[2])
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    if abs(det) < 1.0e-12:
        raise ADCSClosedLoopError("reaction-wheel geometry matrix is rank deficient")
    inv = 1.0 / det
    return (
        ((e * i - f * h) * inv, (c * h - b * i) * inv, (b * f - c * e) * inv),
        ((f * g - d * i) * inv, (a * i - c * g) * inv, (c * d - a * f) * inv),
        ((d * h - e * g) * inv, (b * g - a * h) * inv, (a * e - b * d) * inv),
    )


def _mat_vec(matrix: Sequence[Sequence[float]], vector: Sequence[float]) -> tuple[float, ...]:
    return tuple(sum(float(row[j]) * float(vector[j]) for j in range(len(vector))) for row in matrix)


def _wheel_axes(configuration: str) -> tuple[tuple[float, float, float], ...]:
    """Return body-frame unit spin axes for supported wheel configurations."""

    key = str(configuration or "orthogonal_3").strip().lower()
    aliases = {
        "three_axis_plus_null_space_proxy": "orthogonal_3",
        "three_axis": "orthogonal_3",
        "orthogonal": "orthogonal_3",
        "four_skewed": "four_skew",
        "pyramid": "pyramid_4",
    }
    key = aliases.get(key, key)
    if key == "orthogonal_3":
        return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    if key == "four_skew":
        # Four 45-degree canted wheels at body-frame azimuths 0/90/180/270 deg.
        c = math.sqrt(0.5)
        return ((c, 0.0, c), (0.0, c, c), (-c, 0.0, c), (0.0, -c, c))
    if key == "pyramid_4":
        # Symmetric four-wheel pyramid. Each axis has equal absolute body-axis
        # components (cant angle 54.7356 deg from +Z).
        c = 1.0 / math.sqrt(3.0)
        return ((c, c, c), (-c, c, c), (-c, -c, c), (c, -c, c))
    raise ADCSClosedLoopError(f"unsupported reaction-wheel configuration: {configuration!r}")


def _quat_multiply(a: Sequence[float], b: Sequence[float]) -> tuple[float, float, float, float]:
    aw, ax, ay, az = (float(x) for x in a)
    bw, bx, by, bz = (float(x) for x in b)
    return (
        aw * bw - ax * bx - ay * by - az * bz,
        aw * bx + ax * bw + ay * bz - az * by,
        aw * by - ax * bz + ay * bw + az * bx,
        aw * bz + ax * by - ay * bx + az * bw,
    )


def quaternion_conjugate(q: Iterable[float]) -> tuple[float, float, float, float]:
    """Return the conjugate of a normalized scalar-first quaternion."""

    qw, qx, qy, qz = normalize_quaternion(tuple(q))
    return (qw, -qx, -qy, -qz)


def quaternion_error(
    current_body_to_ref: Iterable[float],
    target_body_to_ref: Iterable[float],
) -> tuple[float, float, float, float]:
    """Compute the shortest-path attitude error with Hamilton multiplication.

    For the scalar-first body-to-reference convention used by this module,
    ``q_error = conjugate(q_target) ⊗ q_current``.  This is deliberately not
    component-wise subtraction; equal current/target attitudes produce the
    identity quaternion and ``q``/``-q`` remain physically equivalent.
    """

    current = normalize_quaternion(tuple(current_body_to_ref))
    target = normalize_quaternion(tuple(target_body_to_ref))
    error = normalize_quaternion(_quat_multiply(quaternion_conjugate(target), current))
    return tuple(-value for value in error) if error[0] < 0.0 else error


def axis_angle_to_quaternion(axis: Iterable[float], angle_rad: float) -> tuple[float, float, float, float]:
    """Return a unit quaternion for an axis-angle attitude error."""

    if not _is_number(angle_rad):
        raise ADCSClosedLoopError("angle_rad must be finite")
    u = _normalize_vec3(axis, "axis")
    half = 0.5 * float(angle_rad)
    s = math.sin(half)
    return normalize_quaternion((math.cos(half), u[0] * s, u[1] * s, u[2] * s))


def quaternion_to_error_vector(q: Iterable[float]) -> tuple[float, float, float]:
    """Convert attitude-error quaternion to a shortest-path rotation vector.

    The result is expressed in radians and is suitable for a basic PD control law.
    """

    qq = normalize_quaternion(tuple(q))
    qw, qx, qy, qz = qq
    if qw < 0.0:
        qw, qx, qy, qz = -qw, -qx, -qy, -qz
    v_norm = math.sqrt(qx * qx + qy * qy + qz * qz)
    if v_norm < 1.0e-12:
        return (2.0 * qx, 2.0 * qy, 2.0 * qz)
    angle = 2.0 * math.atan2(v_norm, max(-1.0, min(1.0, qw)))
    if angle > math.pi:
        angle -= 2.0 * math.pi
    scale = angle / v_norm
    return (qx * scale, qy * scale, qz * scale)


def integrate_quaternion(q: Sequence[float], omega_body_rad_s: Sequence[float], dt_s: float) -> tuple[float, float, float, float]:
    """Advance a body-to-reference quaternion by one small fixed step."""

    if dt_s <= 0.0:
        return normalize_quaternion(q)
    ox, oy, oz = (float(x) for x in omega_body_rad_s)
    qdot = _quat_multiply(q, (0.0, ox, oy, oz))
    next_q = tuple(float(qi) + 0.5 * float(dt_s) * float(dqi) for qi, dqi in zip(q, qdot))
    return normalize_quaternion(next_q)


@dataclass(frozen=True)
class ReactionWheelAssemblyConfig:
    """Reaction-wheel assembly with explicit body-frame geometry."""

    num_wheels: int = 3
    configuration: str = "orthogonal_3"
    allocation_method: str = "weighted_pseudoinverse"
    wheel_inertia_kg_m2: float = 0.08
    max_torque_nm: float = 0.05
    max_speed_rad_s: float = 900.0
    idle_power_w: float = 2.0
    power_per_torque_w: float = 80.0

    def __post_init__(self) -> None:
        normalized = {
            "three_axis_plus_null_space_proxy": "orthogonal_3",
            "three_axis": "orthogonal_3",
            "orthogonal": "orthogonal_3",
            "four_skewed": "four_skew",
            "pyramid": "pyramid_4",
        }.get(str(self.configuration).strip().lower(), str(self.configuration).strip().lower())
        axes = _wheel_axes(normalized)
        expected_count = len(axes)
        if isinstance(self.num_wheels, bool) or not isinstance(self.num_wheels, int):
            raise ADCSClosedLoopError("num_wheels must be an integer")
        # Geometry owns the physical wheel count.  This also upgrades old forms
        # that still carry the historical default num_reaction_wheels=3.
        object.__setattr__(self, "num_wheels", expected_count)
        object.__setattr__(self, "configuration", normalized)
        allocation = str(self.allocation_method or "weighted_pseudoinverse").strip().lower()
        if allocation in {"three_axis_plus_null_space_proxy", "pseudoinverse", "minimum_norm"}:
            allocation = "weighted_pseudoinverse"
        if allocation != "weighted_pseudoinverse":
            raise ADCSClosedLoopError("allocation_method must be 'weighted_pseudoinverse'")
        object.__setattr__(self, "allocation_method", allocation)
        object.__setattr__(self, "wheel_inertia_kg_m2", _positive(self.wheel_inertia_kg_m2, "wheel_inertia_kg_m2"))
        object.__setattr__(self, "max_torque_nm", _positive(self.max_torque_nm, "max_torque_nm"))
        object.__setattr__(self, "max_speed_rad_s", _positive(self.max_speed_rad_s, "max_speed_rad_s"))
        object.__setattr__(self, "idle_power_w", _nonnegative(self.idle_power_w, "idle_power_w"))
        object.__setattr__(self, "power_per_torque_w", _nonnegative(self.power_per_torque_w, "power_per_torque_w"))

    @property
    def axes_body(self) -> tuple[tuple[float, float, float], ...]:
        return _wheel_axes(self.configuration)

    def to_dict(self) -> dict[str, Any]:
        return {
            "num_wheels": self.num_wheels,
            "configuration": self.configuration,
            "allocation_method": self.allocation_method,
            "wheel_inertia_kg_m2": self.wheel_inertia_kg_m2,
            "max_torque_nm": self.max_torque_nm,
            "max_speed_rad_s": self.max_speed_rad_s,
            "idle_power_w": self.idle_power_w,
            "power_per_torque_w": self.power_per_torque_w,
            "axes_body": [list(axis) for axis in self.axes_body],
            "axis_mapping": "explicit_body_frame_spin_axis_matrix",
        }


@dataclass(frozen=True)
class ADCSClosedLoopConfig:
    """Configuration for the HF-4 basic closed-loop ADCS model."""

    epoch_utc: str = "2000-01-01T12:00:00Z"
    duration_s: float = 600.0
    sample_s: float = 5.0
    integration_step_s: float = 0.25
    solver_method: str = "euler"
    inertia_kg_m2: tuple[float, float, float] = (12.0, 10.0, 8.0)
    initial_quaternion_body_to_ref: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    target_quaternion_body_to_ref: tuple[float, float, float, float] = (1.0, 0.0, 0.0, 0.0)
    initial_rate_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    target_mode: str = "inertial"
    target_reference_vector: tuple[float, float, float] = (1.0, 0.0, 0.0)
    control_kp_nm_per_rad: float = 0.10
    control_kd_nm_per_rad_s: float = 0.85
    pointing_requirement_deg: float = 1.0
    disturbance_torque_nm: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gyro_bias_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gyro_noise_std_rad_s: float = 0.0
    sun_sensor_noise_rad: float = 0.0
    star_tracker_available: bool = False
    sun_sensor_available: bool = True
    reaction_wheels: ReactionWheelAssemblyConfig = field(default_factory=ReactionWheelAssemblyConfig)
    initial_wheel_speed_rad_s: tuple[float, ...] = (60.0, 60.0, 60.0)
    schema_version: str = HF4_ADCS_CLOSED_LOOP_SCHEMA_VERSION

    def __post_init__(self) -> None:
        object.__setattr__(self, "duration_s", _positive(self.duration_s, "duration_s"))
        object.__setattr__(self, "sample_s", _positive(self.sample_s, "sample_s"))
        object.__setattr__(self, "integration_step_s", _positive(self.integration_step_s, "integration_step_s"))
        if self.integration_step_s > self.sample_s:
            raise ADCSClosedLoopError("integration_step_s must not exceed sample_s")
        method = str(self.solver_method or "euler").strip().lower()
        if method not in {"euler", "rk4"}:
            raise ADCSClosedLoopError("solver_method must be 'euler' or 'rk4'")
        object.__setattr__(self, "solver_method", method)
        object.__setattr__(self, "inertia_kg_m2", _diag_inertia(self.inertia_kg_m2))
        object.__setattr__(self, "initial_quaternion_body_to_ref", normalize_quaternion(self.initial_quaternion_body_to_ref))
        object.__setattr__(self, "target_quaternion_body_to_ref", normalize_quaternion(self.target_quaternion_body_to_ref))
        object.__setattr__(self, "initial_rate_rad_s", _vec3(self.initial_rate_rad_s, "initial_rate_rad_s"))
        mode = str(self.target_mode or "inertial").strip().lower()
        if mode not in {"inertial", "nadir", "sun", "detumble"}:
            raise ADCSClosedLoopError("target_mode must be one of inertial/nadir/sun/detumble")
        object.__setattr__(self, "target_mode", mode)
        object.__setattr__(self, "target_reference_vector", _normalize_vec3(self.target_reference_vector, "target_reference_vector"))
        object.__setattr__(self, "control_kp_nm_per_rad", _nonnegative(self.control_kp_nm_per_rad, "control_kp_nm_per_rad"))
        object.__setattr__(self, "control_kd_nm_per_rad_s", _nonnegative(self.control_kd_nm_per_rad_s, "control_kd_nm_per_rad_s"))
        object.__setattr__(self, "pointing_requirement_deg", _positive(self.pointing_requirement_deg, "pointing_requirement_deg"))
        object.__setattr__(self, "disturbance_torque_nm", _vec3(self.disturbance_torque_nm, "disturbance_torque_nm", default=(0.0, 0.0, 0.0)))
        object.__setattr__(self, "gyro_bias_rad_s", _vec3(self.gyro_bias_rad_s, "gyro_bias_rad_s", default=(0.0, 0.0, 0.0)))
        object.__setattr__(self, "gyro_noise_std_rad_s", _nonnegative(self.gyro_noise_std_rad_s, "gyro_noise_std_rad_s"))
        object.__setattr__(self, "sun_sensor_noise_rad", _nonnegative(self.sun_sensor_noise_rad, "sun_sensor_noise_rad"))
        object.__setattr__(self, "initial_wheel_speed_rad_s", _wheel_vector(self.initial_wheel_speed_rad_s, "initial_wheel_speed_rad_s", self.reaction_wheels.num_wheels, default=60.0))
        # Reuse HF-2 AttitudeState validation for quaternion/rate metadata.
        AttitudeState(
            quaternion_body_to_ref=self.initial_quaternion_body_to_ref,
            angular_rate_rad_s=self.initial_rate_rad_s,
            reference_frame_id=ECI,
            body_frame_id=BODY,
            epoch_utc=self.epoch_utc,
        )

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "ADCSClosedLoopConfig":
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        solver = sim.get("solver") if isinstance(sim.get("solver"), Mapping) else {}
        duration_s = float(sim.get("duration_s", 600.0))
        sample_s = float(sim.get("sample_s", 5.0))
        solver_cfg = FixedStepSolverConfig.from_simulation({
            "duration_s": duration_s,
            "sample_s": sample_s,
            "solver": {
                "method": solver.get("method", "euler"),
                "step_s": solver.get("step_s", min(sample_s, 0.25)),
                "rtol": solver.get("rtol", 1.0e-9),
                "atol": solver.get("atol", 1.0e-12),
                "deterministic_seed": solver.get("deterministic_seed", sim.get("seed", 0) or 0),
                "include_endpoint": solver.get("include_endpoint", True),
            },
        })
        init_q = params.get("initial_quaternion_body_to_ref")
        if init_q is None:
            error_deg = float(params.get("initial_attitude_error_deg", params.get("initial_pointing_error_deg", 10.0)))
            axis = params.get("initial_error_axis", params.get("initial_error_axis_body", (0.0, 0.0, 1.0)))
            init_q = axis_angle_to_quaternion(axis, math.radians(error_deg))
        rate = params.get("initial_rate_rad_s")
        if rate is None:
            rate_deg_s = params.get("initial_rate_deg_s", params.get("initial_angular_rate_deg_s", (0.0, 0.0, 0.0)))
            if _is_number(rate_deg_s):
                rate = tuple(math.radians(x) for x in _vec3(rate_deg_s, "initial_rate_deg_s"))
            else:
                rate = tuple(convert_unit(x, "deg/s", "rad/s", quantity="angular_rate") for x in _vec3(rate_deg_s, "initial_rate_deg_s"))
        bias = params.get("gyro_bias_rad_s")
        if bias is None:
            bias_deg_s = params.get("gyro_bias_deg_s", (0.0, 0.0, 0.0))
            bias = tuple(convert_unit(x, "deg/s", "rad/s", quantity="angular_rate") for x in _vec3(bias_deg_s, "gyro_bias_deg_s"))
        disturbance = params.get("disturbance_torque_nm", params.get("external_disturbance_torque_nm", (0.0, 0.0, 0.0)))
        if _is_number(disturbance):
            disturbance = (0.0, 0.0, float(disturbance))
        wheel_configuration = str(params.get("wheel_configuration", params.get("wheel_allocation", "orthogonal_3")))
        allocation_method = str(params.get("wheel_allocation_method", "weighted_pseudoinverse"))
        requested_count = int(params.get("num_reaction_wheels", params.get("reaction_wheel_count", 3)))
        rw = ReactionWheelAssemblyConfig(
            num_wheels=requested_count,
            configuration=wheel_configuration,
            allocation_method=allocation_method,
            wheel_inertia_kg_m2=float(params.get("wheel_inertia_kg_m2", 0.08)),
            max_torque_nm=float(params.get("max_wheel_torque_nm", params.get("max_rw_torque_nm", 0.05))),
            max_speed_rad_s=float(params.get("max_wheel_speed_rad_s", params.get("max_rw_speed_rad_s", 900.0))),
            idle_power_w=float(params.get("rw_idle_power_w", 2.0)),
            power_per_torque_w=float(params.get("rw_power_per_torque_w", 80.0)),
        )
        initial_wheel_speed = _wheel_vector(
            params.get("initial_wheel_speed_rad_s", 60.0),
            "initial_wheel_speed_rad_s",
            rw.num_wheels,
        )
        return cls(
            epoch_utc=str(sim.get("epoch_utc") or "2000-01-01T12:00:00Z"),
            duration_s=duration_s,
            sample_s=sample_s,
            integration_step_s=solver_cfg.step_s,
            solver_method=solver_cfg.method,
            inertia_kg_m2=_diag_inertia(params.get("inertia_kg_m2", params.get("spacecraft_inertia_kg_m2", (12.0, 10.0, 8.0)))),
            initial_quaternion_body_to_ref=tuple(float(x) for x in init_q),  # type: ignore[arg-type]
            target_quaternion_body_to_ref=tuple(float(x) for x in params.get("target_quaternion_body_to_ref", (1.0, 0.0, 0.0, 0.0))),
            initial_rate_rad_s=tuple(float(x) for x in rate),  # type: ignore[arg-type]
            target_mode=str(params.get("target_mode", params.get("pointing_mode", "inertial"))),
            target_reference_vector=tuple(float(x) for x in _vec3(params.get("target_reference_vector", (1.0, 0.0, 0.0)), "target_reference_vector")),
            control_kp_nm_per_rad=float(params.get("control_kp_nm_per_rad", params.get("kp", 0.10))),
            control_kd_nm_per_rad_s=float(params.get("control_kd_nm_per_rad_s", params.get("kd", 0.85))),
            pointing_requirement_deg=float(params.get("pointing_requirement_deg", 1.0)),
            disturbance_torque_nm=tuple(float(x) for x in _vec3(disturbance, "disturbance_torque_nm")),
            gyro_bias_rad_s=tuple(float(x) for x in bias),
            gyro_noise_std_rad_s=float(convert_unit(params.get("gyro_noise_std_deg_s", 0.0), "deg/s", "rad/s", quantity="angular_rate") if "gyro_noise_std_deg_s" in params else params.get("gyro_noise_std_rad_s", 0.0)),
            sun_sensor_noise_rad=float(convert_unit(params.get("sun_sensor_noise_deg", 0.0), "deg", "rad", quantity="angle") if "sun_sensor_noise_deg" in params else params.get("sun_sensor_noise_rad", 0.0)),
            star_tracker_available=bool(params.get("star_tracker_available", False)),
            sun_sensor_available=bool(params.get("sun_sensor_available", True)),
            reaction_wheels=rw,
            initial_wheel_speed_rad_s=tuple(float(x) for x in initial_wheel_speed),
        )

    def output_time_grid(self) -> TimeGrid:
        return build_time_grid(
            duration_s=self.duration_s,
            sample_s=self.sample_s,
            epoch_utc=self.epoch_utc,
            include_endpoint=True,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "epoch_utc": self.epoch_utc,
            "duration_s": self.duration_s,
            "sample_s": self.sample_s,
            "integration_step_s": self.integration_step_s,
            "solver_method": self.solver_method,
            "inertia_kg_m2": list(self.inertia_kg_m2),
            "initial_quaternion_body_to_ref": list(self.initial_quaternion_body_to_ref),
            "target_quaternion_body_to_ref": list(self.target_quaternion_body_to_ref),
            "initial_rate_rad_s": list(self.initial_rate_rad_s),
            "target_mode": self.target_mode,
            "target_reference_vector": list(self.target_reference_vector),
            "control_kp_nm_per_rad": self.control_kp_nm_per_rad,
            "control_kd_nm_per_rad_s": self.control_kd_nm_per_rad_s,
            "pointing_requirement_deg": self.pointing_requirement_deg,
            "disturbance_torque_nm": list(self.disturbance_torque_nm),
            "gyro_bias_rad_s": list(self.gyro_bias_rad_s),
            "gyro_noise_std_rad_s": self.gyro_noise_std_rad_s,
            "sun_sensor_noise_rad": self.sun_sensor_noise_rad,
            "star_tracker_available": self.star_tracker_available,
            "sun_sensor_available": self.sun_sensor_available,
            "reaction_wheels": self.reaction_wheels.to_dict(),
            "initial_wheel_speed_rad_s": list(self.initial_wheel_speed_rad_s),
        }


@dataclass(frozen=True)
class ADCSRuntimeEffects:
    """Time-local actuator and sensor effects owned by the ADCS runtime.

    The values are deterministic and auditable.  They are intentionally kept
    separate from the nominal configuration so a time-window event can become
    active without mutating the baseline TaskSpec.
    """

    axis_torque_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    max_speed_scale: tuple[float, float, float] = (1.0, 1.0, 1.0)
    gyro_bias_add_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    gyro_noise_scale: float = 1.0
    wheel_drag_nms: tuple[float, float, float] = (0.0, 0.0, 0.0)
    jammed_axes: tuple[int, ...] = ()
    gyro_dropout: bool = False
    active_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "axis_torque_scale", tuple(max(0.0, float(x)) for x in self.axis_torque_scale))
        object.__setattr__(self, "max_speed_scale", tuple(max(0.0, float(x)) for x in self.max_speed_scale))
        object.__setattr__(self, "gyro_noise_scale", max(0.0, float(self.gyro_noise_scale)))
        object.__setattr__(self, "wheel_drag_nms", tuple(max(0.0, float(x)) for x in self.wheel_drag_nms))


RuntimeEffectResolver = Callable[[float], ADCSRuntimeEffects]


@dataclass(frozen=True)
class ADCSClosedLoopSample:
    """One HF-4 ADCS trace sample."""

    time_s: float
    utc: str
    quaternion_body_to_ref: tuple[float, float, float, float]
    angular_rate_rad_s: tuple[float, float, float]
    pointing_error_vector_rad: tuple[float, float, float]
    pointing_error_deg: float
    gyro_measured_rad_s: tuple[float, float, float]
    gyro_true_rad_s: tuple[float, float, float]
    gyro_bias_rad_s: tuple[float, float, float]
    gyro_noise_rad_s: tuple[float, float, float]
    command_torque_nm: tuple[float, float, float]
    applied_torque_nm: tuple[float, float, float]
    wheel_speed_rad_s: tuple[float, ...]
    wheel_torque_nm: tuple[float, ...]
    wheel_speed_saturated: bool
    torque_saturated: bool
    rw_power_w: float
    target_mode: str
    controller_mode: str
    sensor_mode: str

    @property
    def saturation_flag(self) -> bool:
        return bool(self.wheel_speed_saturated or self.torque_saturated)

    @property
    def quaternion_norm_error(self) -> float:
        return abs(quaternion_norm(self.quaternion_body_to_ref) - 1.0)

    def to_trace_row(self, *, task_id: str, case_id: str, capability_id: str, pointing_requirement_deg: float) -> dict[str, Any]:
        q = self.quaternion_body_to_ref
        w = self.angular_rate_rad_s
        e = self.pointing_error_vector_rad
        gyro = self.gyro_measured_rad_s
        gyro_true = self.gyro_true_rad_s
        gyro_bias = self.gyro_bias_rad_s
        gyro_noise = self.gyro_noise_rad_s
        cmd = self.command_torque_nm
        tau = self.applied_torque_nm
        wheel = self.wheel_speed_rad_s
        wheel_tau = self.wheel_torque_nm
        pointing_state = "acquired" if self.pointing_error_deg <= pointing_requirement_deg else "converging"
        health_state = "saturated" if self.saturation_flag else ("nominal" if pointing_state == "acquired" else "degraded")
        return {
            "task_id": task_id,
            "case_id": case_id,
            "time_s": round(self.time_s, 12),
            "utc": self.utc,
            "capability_id": capability_id,
            "fidelity_level": "basic",
            "frame.attitude_ref": ECI,
            "frame.body": BODY,
            "adcs.model": "hf4_basic_closed_loop",
            "adcs.target.mode": self.target_mode,
            "adcs.control.controller_mode": self.controller_mode,
            "adcs.sensor.mode": self.sensor_mode,
            "adcs.attitude.q_bn_0": q[0],
            "adcs.attitude.q_bn_1": q[1],
            "adcs.attitude.q_bn_2": q[2],
            "adcs.attitude.q_bn_3": q[3],
            "adcs.attitude.quaternion_norm": quaternion_norm(q),
            "adcs.attitude.quaternion_norm_error": self.quaternion_norm_error,
            "adcs.pointing.error_deg": self.pointing_error_deg,
            "adcs.pointing.error_vector_rad_0": e[0],
            "adcs.pointing.error_vector_rad_1": e[1],
            "adcs.pointing.error_vector_rad_2": e[2],
            "adcs.rate.omega_bn_b_rad_s_0": w[0],
            "adcs.rate.omega_bn_b_rad_s_1": w[1],
            "adcs.rate.omega_bn_b_rad_s_2": w[2],
            "adcs.sensor.gyro_measured_rad_s_0": gyro[0],
            "adcs.sensor.gyro_measured_rad_s_1": gyro[1],
            "adcs.sensor.gyro_measured_rad_s_2": gyro[2],
            "adcs.sensor.gyro_true_rad_s_0": gyro_true[0],
            "adcs.sensor.gyro_true_rad_s_1": gyro_true[1],
            "adcs.sensor.gyro_true_rad_s_2": gyro_true[2],
            "adcs.sensor.gyro_bias_rad_s_0": gyro_bias[0],
            "adcs.sensor.gyro_bias_rad_s_1": gyro_bias[1],
            "adcs.sensor.gyro_bias_rad_s_2": gyro_bias[2],
            "adcs.sensor.gyro_noise_rad_s_0": gyro_noise[0],
            "adcs.sensor.gyro_noise_rad_s_1": gyro_noise[1],
            "adcs.sensor.gyro_noise_rad_s_2": gyro_noise[2],
            "adcs.sensor.gyro_residual_rad_s_0": gyro[0] - gyro_true[0],
            "adcs.sensor.gyro_residual_rad_s_1": gyro[1] - gyro_true[1],
            "adcs.sensor.gyro_residual_rad_s_2": gyro[2] - gyro_true[2],
            "adcs.control.command_torque_nm_0": cmd[0],
            "adcs.control.command_torque_nm_1": cmd[1],
            "adcs.control.command_torque_nm_2": cmd[2],
            "adcs.control.applied_torque_nm_0": tau[0],
            "adcs.control.applied_torque_nm_1": tau[1],
            "adcs.control.applied_torque_nm_2": tau[2],
            "adcs.control.torque_saturation_flag": self.torque_saturated,
            **{f"adcs.rw.speed_rad_s_{i}": value for i, value in enumerate(wheel)},
            **{f"adcs.rw.command_torque_nm_{i}": value for i, value in enumerate(wheel_tau)},
            "adcs.rw.max_abs_speed_rad_s": max(abs(x) for x in wheel),
            "adcs.rw.speed_saturation_flag": self.wheel_speed_saturated,
            "adcs.rw.saturation_flag": self.saturation_flag,
            "adcs.power.rw_power_w": self.rw_power_w,
            "label.pointing_state": pointing_state,
            "label.health_state": health_state,
            "label.rw_saturation": self.saturation_flag,
        }


def _gyro_noise(config: ADCSClosedLoopConfig, time_s: float, effects: ADCSRuntimeEffects | None = None) -> tuple[float, float, float]:
    # Deterministic bounded pseudo-noise: repeatable and visible in the evidence trace.
    amplitude = config.gyro_noise_std_rad_s * (effects.gyro_noise_scale if effects else 1.0)
    return tuple(amplitude * math.sin(0.173 * float(time_s) + 1.37 * i) for i in range(3))  # type: ignore[return-value]


def _measure_gyro(w: Sequence[float], config: ADCSClosedLoopConfig, time_s: float, effects: ADCSRuntimeEffects | None = None) -> tuple[float, float, float]:
    effects = effects or ADCSRuntimeEffects()
    if effects.gyro_dropout:
        return (0.0, 0.0, 0.0)
    noise = _gyro_noise(config, time_s, effects)
    return tuple(float(w[i]) + config.gyro_bias_rad_s[i] + effects.gyro_bias_add_rad_s[i] + noise[i] for i in range(3))  # type: ignore[return-value]


def _allocate_wheel_torques(
    body_command_nm: Sequence[float],
    config: ADCSClosedLoopConfig,
    effects: ADCSRuntimeEffects,
) -> tuple[tuple[float, float, float], tuple[float, ...], bool]:
    """Allocate a desired body torque to the configured wheel geometry.

    The minimum-norm solution is ``u = -G^T (G G^T)^-1 tau`` where columns of
    ``G`` are wheel spin axes in the body frame and ``u`` is motor torque on
    each wheel.  Per-wheel fault/constraint limits are then applied and the
    actually delivered body torque is reconstructed as ``-G u``.
    """

    axes = config.reaction_wheels.axes_body
    n = len(axes)
    # G G^T
    gram = tuple(
        tuple(sum(axes[k][r] * axes[k][c] for k in range(n)) for c in range(3))
        for r in range(3)
    )
    inv = _invert_3x3(gram)
    tmp = _mat_vec(inv, tuple(float(x) for x in body_command_nm))
    unconstrained = tuple(-sum(axes[k][r] * tmp[r] for r in range(3)) for k in range(n))
    wheel_torque: list[float] = []
    saturated = False
    for k, requested in enumerate(unconstrained):
        scale = effects.axis_torque_scale[k] if k < len(effects.axis_torque_scale) else 1.0
        limit = config.reaction_wheels.max_torque_nm * max(0.0, scale)
        actual = 0.0 if k in effects.jammed_axes else max(-limit, min(limit, requested))
        if abs(actual - requested) > 1.0e-12:
            saturated = True
        wheel_torque.append(actual)
    applied = tuple(-sum(axes[k][r] * wheel_torque[k] for k in range(n)) for r in range(3))
    if any(abs(applied[i] - float(body_command_nm[i])) > 1.0e-10 for i in range(3)):
        saturated = True
    return applied, tuple(wheel_torque), saturated


def _control_law(q: Sequence[float], w: Sequence[float], config: ADCSClosedLoopConfig, time_s: float, effects: ADCSRuntimeEffects | None = None) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, ...], bool]:
    effects = effects or ADCSRuntimeEffects()
    gyro = _measure_gyro(w, config, time_s, effects)
    error_q = quaternion_error(q, config.target_quaternion_body_to_ref)
    error = quaternion_to_error_vector(error_q)
    if config.target_mode == "detumble":
        command = tuple(-config.control_kd_nm_per_rad_s * gyro[i] for i in range(3))
    else:
        command = tuple(-config.control_kp_nm_per_rad * error[i] - config.control_kd_nm_per_rad_s * gyro[i] for i in range(3))
    applied, wheel_torque, saturated = _allocate_wheel_torques(command, config, effects)
    return command, applied, wheel_torque, saturated


def _dynamics_step(q: tuple[float, float, float, float], w: tuple[float, float, float], wheel: tuple[float, ...], config: ADCSClosedLoopConfig, dt: float, time_s: float, effects: ADCSRuntimeEffects | None = None) -> tuple[tuple[float, float, float, float], tuple[float, float, float], tuple[float, ...], bool, bool]:
    effects = effects or ADCSRuntimeEffects()
    _cmd, applied, wheel_torque, torque_sat = _control_law(q, w, config, time_s, effects)
    iw = tuple(config.inertia_kg_m2[i] * w[i] for i in range(3))
    gyroscopic = _cross(w, iw)
    wdot = tuple((applied[i] + config.disturbance_torque_nm[i] - gyroscopic[i]) / config.inertia_kg_m2[i] for i in range(3))
    w_next = tuple(w[i] + wdot[i] * dt for i in range(3))
    q_next = integrate_quaternion(q, w_next, dt)
    n = config.reaction_wheels.num_wheels
    wheel_next_raw = tuple(
        0.0 if i in effects.jammed_axes else wheel[i] + (wheel_torque[i] / config.reaction_wheels.wheel_inertia_kg_m2) * dt
        - ((effects.wheel_drag_nms[i] if i < len(effects.wheel_drag_nms) else 0.0) * (1.0 if wheel[i] > 0 else -1.0 if wheel[i] < 0 else 0.0) / config.reaction_wheels.wheel_inertia_kg_m2) * dt
        for i in range(n)
    )
    max_speed = tuple(config.reaction_wheels.max_speed_rad_s * (effects.max_speed_scale[i] if i < len(effects.max_speed_scale) else 1.0) for i in range(n))
    wheel_next = tuple(max(-max_speed[i], min(max_speed[i], x)) for i, x in enumerate(wheel_next_raw))
    wheel_sat = any(abs(wheel_next[i] - wheel_next_raw[i]) > 1.0e-12 or abs(wheel_next[i]) >= max_speed[i] - 1.0e-9 for i in range(n))
    return q_next, w_next, wheel_next, torque_sat, wheel_sat


def _sample_from_state(time_s: float, q: tuple[float, float, float, float], w: tuple[float, float, float], wheel: tuple[float, ...], config: ADCSClosedLoopConfig, grid: TimeGrid, effects: ADCSRuntimeEffects | None = None) -> ADCSClosedLoopSample:
    effects = effects or ADCSRuntimeEffects()
    command, applied, wheel_torque, torque_sat = _control_law(q, w, config, time_s, effects)
    max_speed = tuple(config.reaction_wheels.max_speed_rad_s * (effects.max_speed_scale[i] if i < len(effects.max_speed_scale) else 1.0) for i in range(config.reaction_wheels.num_wheels))
    wheel_sat = any(abs(wheel[i]) >= max_speed[i] - 1.0e-9 for i in range(3))
    error_q = quaternion_error(q, config.target_quaternion_body_to_ref)
    error = quaternion_to_error_vector(error_q)
    error_deg = math.degrees(vector_norm(error))
    rw_power_w = config.reaction_wheels.idle_power_w * config.reaction_wheels.num_wheels + config.reaction_wheels.power_per_torque_w * sum(abs(value) for value in wheel_torque)
    sensor_bits = []
    if config.star_tracker_available:
        sensor_bits.append("star_tracker_stub")
    if config.sun_sensor_available:
        sensor_bits.append("sun_sensor_simple")
    sensor_bits.append("gyro_dropout" if effects.gyro_dropout else "gyro_bias_noise_proxy")
    if effects.active_effects:
        sensor_bits.append("events_active")
    utc = grid.utc_at(time_s).isoformat().replace("+00:00", "Z")
    return ADCSClosedLoopSample(
        time_s=float(time_s),
        utc=utc,
        quaternion_body_to_ref=q,
        angular_rate_rad_s=w,
        pointing_error_vector_rad=error,
        pointing_error_deg=error_deg,
        gyro_measured_rad_s=_measure_gyro(w, config, time_s, effects),
        gyro_true_rad_s=tuple(float(value) for value in w),
        gyro_bias_rad_s=tuple(config.gyro_bias_rad_s[i] + effects.gyro_bias_add_rad_s[i] for i in range(3)),
        gyro_noise_rad_s=_gyro_noise(config, time_s, effects),
        command_torque_nm=command,
        applied_torque_nm=applied,
        wheel_speed_rad_s=wheel,
        wheel_torque_nm=wheel_torque,
        wheel_speed_saturated=wheel_sat,
        torque_saturated=torque_sat,
        rw_power_w=rw_power_w,
        target_mode=config.target_mode,
        controller_mode="pd_small_angle_detumble" if config.target_mode == "detumble" else "pd_hamilton_quaternion_error",
        sensor_mode="+".join(sensor_bits),
    )


def propagate_adcs_closed_loop(
    config: ADCSClosedLoopConfig,
    grid: TimeGrid | None = None,
    effect_resolver: RuntimeEffectResolver | None = None,
) -> tuple[ADCSClosedLoopSample, ...]:
    """Propagate the closed-loop model with optional time-window runtime effects."""

    if not isinstance(config, ADCSClosedLoopConfig):
        raise ADCSClosedLoopError("config must be ADCSClosedLoopConfig")
    out_grid = grid or config.output_time_grid()
    resolver = effect_resolver or (lambda _time_s: ADCSRuntimeEffects())
    q = config.initial_quaternion_body_to_ref
    w = config.initial_rate_rad_s
    wheel = config.initial_wheel_speed_rad_s
    samples: list[ADCSClosedLoopSample] = []
    current_t = out_grid.times_s[0]
    samples.append(_sample_from_state(current_t, q, w, wheel, config, out_grid, resolver(current_t)))
    for next_t in out_grid.times_s[1:]:
        while current_t < next_t - 1.0e-12:
            dt = min(config.integration_step_s, next_t - current_t)
            effects = resolver(current_t)
            q, w, wheel, _torque_sat, _wheel_sat = _dynamics_step(q, w, wheel, config, dt, current_t, effects)
            current_t = min(next_t, current_t + dt)
        samples.append(_sample_from_state(next_t, q, w, wheel, config, out_grid, resolver(next_t)))
    return tuple(samples)

def summarize_adcs_closed_loop(samples: Sequence[ADCSClosedLoopSample], config: ADCSClosedLoopConfig) -> dict[str, Any]:
    if not samples:
        raise ADCSClosedLoopError("samples cannot be empty")
    errors = [s.pointing_error_deg for s in samples]
    qerr = [s.quaternion_norm_error for s in samples]
    sat_count = sum(1 for s in samples if s.saturation_flag)
    settled_time_s: float | None = None
    for s in samples:
        if s.pointing_error_deg <= config.pointing_requirement_deg:
            settled_time_s = s.time_s
            break
    initial = errors[0]
    final = errors[-1]
    convergence_ratio = final / initial if initial > 1.0e-12 else 0.0
    return {
        "schema_version": HF4_ADCS_CLOSED_LOOP_SCHEMA_VERSION,
        "fidelity_level": "basic",
        "model_family": "route_b_basic_closed_loop_adcs",
        "can_claim_high_fidelity": False,
        "reason_high_fidelity_still_blocked": "HF-4 implements basic closed-loop attitude dynamics and proxy sensors/actuators; full FSW, calibrated sensors, actuator electronics, and benchmark validation are pending.",
        "qoi": {
            "adcs.initial_pointing_error_deg": initial,
            "adcs.final_pointing_error_deg": final,
            "adcs.max_pointing_error_deg": max(errors),
            "adcs.min_pointing_error_deg": min(errors),
            "adcs.settled_time_s": settled_time_s,
            "adcs.convergence_ratio": convergence_ratio,
            "adcs.quaternion_norm_max_error": max(qerr),
            "adcs.saturation_count": sat_count,
            "adcs.max_abs_rate_rad_s": max(vector_norm(s.angular_rate_rad_s) for s in samples),
            "adcs.max_abs_wheel_speed_rad_s": max(max(abs(x) for x in s.wheel_speed_rad_s) for s in samples),
            "adcs.max_rw_power_w": max(s.rw_power_w for s in samples),
        },
        "validation_preview": {
            "quaternion_norm_within_1e-9": max(qerr) < 1.0e-9,
            "pointing_error_reduced": final < initial if initial > 1.0e-12 else True,
            "wheel_speed_within_limit": all(max(abs(x) for x in s.wheel_speed_rad_s) <= config.reaction_wheels.max_speed_rad_s + 1.0e-9 for s in samples),
            "validation_gate_status": "preview_only_pending_HF8_physical_validation_gates",
        },
        "known_physics_limits": [
            "Rigid body is diagonal-inertia only; products of inertia are ignored.",
            "Controller is a basic PD quaternion-error proxy, not flight software.",
            "Gyro/sun-sensor/star-tracker behavior is deterministic proxy or readiness stub, not calibrated sensor physics.",
            "Reaction-wheel assembly supports orthogonal-three, four-skew and symmetric four-wheel-pyramid geometry with deterministic weighted-pseudoinverse allocation; motor electronics remain proxy-only.",
            "No environmental torque model beyond user-supplied disturbance_torque_nm.",
            "Benchmark tolerance envelopes and physical validation gates remain pending HF8/HF9.",
        ],
        "config": config.to_dict(),
    }


def build_hf4_adcs_closed_loop_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        config = ADCSClosedLoopConfig.from_task_spec(spec)
        config_payload: dict[str, Any] = config.to_dict()
        status = "implemented_basic_closed_loop_not_high_fidelity"
    except Exception as exc:
        config_payload = {"error": str(exc)}
        status = "metadata_build_failed"
    return {
        "schema_version": HF4_ADCS_CLOSED_LOOP_SCHEMA_VERSION,
        "route_b_version": "B-3/HF-4",
        "capability_id": "subsystem.adcs_closed_loop.basic.v1",
        "status": status,
        "fidelity_level": "basic",
        "can_claim_high_fidelity": False,
        "implemented_model_features": [
            "quaternion attitude state",
            "body angular-rate propagation",
            "diagonal rigid-body dynamics",
            "reaction-wheel torque and speed proxy",
            "PD pointing/detumble controller",
            "gyro bias/noise proxy",
            "sun-sensor/star-tracker readiness metadata",
            "pointing convergence and quaternion-norm metrics",
        ],
        "foundation_dependencies": ["HF-1 time grid", "HF-2 AttitudeState", "HF-2 fixed-step solver metadata", "HF-2 BODY/ECI frame metadata"],
        "remaining_route_b_dependencies": [
            "HF-5 EPS-thermal-orbit coupling",
            "HF-6 comm/payload mission coupling",
            "HF-7 propulsion-orbit-attitude coupling",
            "HF-8 physical validation gates",
            "HF-9 benchmark scenarios and tolerance envelopes",
        ],
        "known_physics_limits": summarize_adcs_closed_loop([_sample_from_state(0.0, (1.0, 0.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 0.0, 0.0), ADCSClosedLoopConfig(), ADCSClosedLoopConfig().output_time_grid())], ADCSClosedLoopConfig())["known_physics_limits"],
        "config": config_payload,
    }


__all__ = [
    "HF4_ADCS_CLOSED_LOOP_SCHEMA_VERSION",
    "ADCSClosedLoopConfig",
    "ADCSClosedLoopError",
    "ADCSClosedLoopSample",
    "ADCSRuntimeEffects",
    "RuntimeEffectResolver",
    "ReactionWheelAssemblyConfig",
    "axis_angle_to_quaternion",
    "build_hf4_adcs_closed_loop_payload",
    "integrate_quaternion",
    "propagate_adcs_closed_loop",
    "quaternion_conjugate",
    "quaternion_error",
    "quaternion_to_error_vector",
    "summarize_adcs_closed_loop",
]
