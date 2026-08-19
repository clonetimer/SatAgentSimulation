"""Basilisk-native ADCS/FSW adapter support for BSK-ADCS-1.

The module mirrors the BSK-ORB native-boundary pattern:

* TaskSpec parsing and Basilisk FSW configuration-blueprint generation are fully
  deterministic and unit-testable without Basilisk installed.
* Optional Basilisk imports happen only inside availability/runtime helpers.
* The current local ADCS fidelity proxy remains a separate capability and is
  never used as an implicit fallback for this Basilisk-native path.

BSK-ADCS-1 prepares the FSW-native entry point.  It does not claim a complete
flight software stack, full FDIR, flight calibration, or benchmark pass.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
import math

from sat_sim.frames import vector_norm

BASILISK_ADCS_FSW_SCHEMA_VERSION = "bsk_adcs1.basilisk_adcs_fsw.v1"
BASILISK_REQUIRED_VERSION_NOTE = (
    "Runtime acceptance requires bsk 2.11.0 or the metadata-only 2.11.0+satfix1 variant; "
    "offline wheels are optional and the active Python environment is the source of runtime identity."
)

SUPPORTED_GUIDANCE_MODES = {
    "inertial",
    "inertial3d",
    "sun_pointing",
    "sun_safe",
    "nadir",
    "hill",
    "velocity",
    "detumble",
}
SUPPORTED_CONTROL_LAWS = {"mrp_feedback", "mrp_pd", "mrp_steering", "rate_damping"}
SUPPORTED_ESTIMATORS = {"truth", "simple_nav", "css_est", "inertial_ukf", "sunline_ekf", "sunline_ukf", "sunline_sukf"}
SUPPORTED_RW_GEOMETRIES = {"three_axis", "pyramid", "custom"}

GUIDANCE_TO_MODULES = {
    # Basilisk 2.11.0 exposes FSW algorithms as flat modules under
    # ``Basilisk.fswAlgorithms`` rather than the newer conceptual grouping used
    # in earlier blueprint text.
    "inertial": ("Basilisk.fswAlgorithms.inertial3D",),
    "inertial3d": ("Basilisk.fswAlgorithms.inertial3D",),
    "sun_pointing": ("Basilisk.fswAlgorithms.celestialTwoBodyPoint",),
    "sun_safe": ("Basilisk.fswAlgorithms.sunSafePoint",),
    "nadir": ("Basilisk.fswAlgorithms.hillPoint",),
    "hill": ("Basilisk.fswAlgorithms.hillPoint",),
    "velocity": ("Basilisk.fswAlgorithms.velocityPoint",),
    "detumble": ("Basilisk.fswAlgorithms.inertial3D",),
}
CONTROL_TO_MODULES = {
    "mrp_feedback": ("Basilisk.fswAlgorithms.mrpFeedback",),
    "mrp_pd": ("Basilisk.fswAlgorithms.mrpPD",),
    "mrp_steering": ("Basilisk.fswAlgorithms.mrpSteering", "Basilisk.fswAlgorithms.rateServoFullNonlinear"),
    "rate_damping": ("Basilisk.fswAlgorithms.mrpFeedback",),
}
ESTIMATOR_TO_MODULES = {
    "truth": ("Basilisk.simulation.simpleNav",),
    "simple_nav": ("Basilisk.simulation.simpleNav",),
    "css_est": ("Basilisk.fswAlgorithms.cssWlsEst",),
    "inertial_ukf": ("Basilisk.fswAlgorithms.inertialUKF",),
    "sunline_ekf": ("Basilisk.fswAlgorithms.sunlineEKF",),
    "sunline_ukf": ("Basilisk.fswAlgorithms.sunlineUKF",),
    "sunline_sukf": ("Basilisk.fswAlgorithms.sunlineSuKF",),
}
SENSOR_TO_MODULES = {
    "imu": ("Basilisk.fswAlgorithms.imuComm",),
    "gyro": ("Basilisk.fswAlgorithms.imuComm",),
    "star_tracker": ("Basilisk.fswAlgorithms.stComm",),
    "css": ("Basilisk.fswAlgorithms.cssComm",),
    "sun_sensor": ("Basilisk.fswAlgorithms.cssComm",),
    "tam": ("Basilisk.fswAlgorithms.tamComm",),
}
BSK_RUN5_RUNTIME_GUIDANCE_MODES = {"inertial", "inertial3d", "detumble"}
BSK_RUN5_RUNTIME_CONTROL_LAWS = {"mrp_feedback", "rate_damping"}
BSK_RUN5_RUNTIME_ESTIMATORS = {"truth", "simple_nav"}


class BasiliskAdcsFswError(ValueError):
    """Raised when a BSK-ADCS-1 configuration is invalid."""


class BasiliskAdcsFswRuntimeUnavailable(RuntimeError):
    """Raised when the requested ADCS/FSW runtime smoke is not executable."""

    def __init__(self, reason: str, details: Mapping[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


@dataclass(frozen=True)
class BasiliskAdcsAvailability:
    """Runtime availability report for the optional Basilisk ADCS dependency."""

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


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise BasiliskAdcsFswError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise BasiliskAdcsFswError(f"{name} must be positive")
    return out


def _nonnegative(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out < 0.0:
        raise BasiliskAdcsFswError(f"{name} must be non-negative")
    return out


def _vec3(value: Any, name: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (0.0, 0.0, float(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = tuple(float(x) for x in value)
        if len(items) == 3 and all(math.isfinite(x) for x in items):
            return items  # type: ignore[return-value]
    raise BasiliskAdcsFswError(f"{name} must contain exactly three finite numbers")


def _as_list(value: Any, default: Iterable[str]) -> tuple[str, ...]:
    if value is None:
        return tuple(str(x) for x in default)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (Mapping, bytes, bytearray)):
        return tuple(str(x) for x in value if str(x).strip())
    raise BasiliskAdcsFswError("expected a string or list of strings")


def _enum(value: Any, name: str, allowed: set[str], default: str) -> str:
    raw = default if value is None else str(value).strip().lower().replace("-", "_")
    aliases = {
        "sun": "sun_pointing",
        "sunpoint": "sun_pointing",
        "sun_safe_point": "sun_safe",
        "sun_safe_pointing": "sun_safe",
        "nadir_pointing": "nadir",
        "hill_point": "hill",
        "velocity_point": "velocity",
        "mrpfeedback": "mrp_feedback",
        "mrppd": "mrp_pd",
        "mrpsteering": "mrp_steering",
        "simple_navigation": "simple_nav",
        "inertialukf": "inertial_ukf",
        "sunlineekf": "sunline_ekf",
        "sunlineukf": "sunline_ukf",
        "sunlinesukf": "sunline_sukf",
    }
    out = aliases.get(raw, raw)
    if out not in allowed:
        raise BasiliskAdcsFswError(f"{name} must be one of {sorted(allowed)}")
    return out


@dataclass(frozen=True)
class BasiliskAdcsModeConfig:
    """Basilisk FSW guidance/mode-manager configuration surface."""

    requested_mode: str = "inertial"
    enabled_modes: tuple[str, ...] = ("standby", "inertial3d", "sun_safe", "nadir", "velocity")
    mode_transition_policy: str = "modeRequest_event_gate"
    fault_management_requested: bool = False

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskAdcsModeConfig":
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        adcs = params.get("adcs") if isinstance(params.get("adcs"), Mapping) else {}
        fsw = params.get("fsw") if isinstance(params.get("fsw"), Mapping) else {}
        mode_payload = params.get("mode_manager") if isinstance(params.get("mode_manager"), Mapping) else {}
        raw_mode = params.get("target_mode", adcs.get("target_mode", fsw.get("mode", mode_payload.get("mode", "inertial"))))
        requested = _enum(raw_mode, "parameters.target_mode", SUPPORTED_GUIDANCE_MODES, "inertial")
        enabled = tuple(
            _enum(item, "parameters.mode_manager.enabled_modes", SUPPORTED_GUIDANCE_MODES | {"standby"}, "nadir")
            for item in _as_list(mode_payload.get("enabled_modes"), ("standby", "inertial3d", "sun_safe", "nadir", "velocity"))
        )
        fm = params.get("fault_management") if isinstance(params.get("fault_management"), Mapping) else {}
        fault_requested = bool(fm.get("enabled") or mode_payload.get("fault_management") or params.get("fault_management_enabled"))
        return cls(requested_mode=requested, enabled_modes=enabled, fault_management_requested=fault_requested)

    def guidance_modules(self) -> tuple[str, ...]:
        return GUIDANCE_TO_MODULES[self.requested_mode]

    def to_dict(self) -> dict[str, Any]:
        return {
            "requested_mode": self.requested_mode,
            "enabled_modes": list(self.enabled_modes),
            "mode_transition_policy": self.mode_transition_policy,
            "fault_management_requested": self.fault_management_requested,
            "fault_management_claim": "not_implemented_in_bsk_adcs1_task_specific_fdir_required",
        }


@dataclass(frozen=True)
class BasiliskAdcsControllerConfig:
    """Basilisk attitude controller configuration surface."""

    control_law: str = "mrp_feedback"
    k_gain: float = 3.5
    p_gain: float = 30.0
    ki_gain: float = 0.0
    integral_limit: float = 0.0
    max_control_torque_nm: float = 0.05

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskAdcsControllerConfig":
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        ctrl = params.get("controller") if isinstance(params.get("controller"), Mapping) else {}
        law = _enum(ctrl.get("control_law", params.get("control_law")), "parameters.controller.control_law", SUPPORTED_CONTROL_LAWS, "mrp_feedback")
        return cls(
            control_law=law,
            k_gain=_positive(ctrl.get("K", ctrl.get("k_gain", params.get("K", 3.5))), "controller.K"),
            p_gain=_positive(ctrl.get("P", ctrl.get("p_gain", params.get("P", 30.0))), "controller.P"),
            ki_gain=_nonnegative(ctrl.get("Ki", ctrl.get("ki_gain", params.get("Ki", 0.0))), "controller.Ki"),
            integral_limit=_nonnegative(ctrl.get("integral_limit", params.get("integral_limit", 0.0)), "controller.integral_limit"),
            max_control_torque_nm=_positive(ctrl.get("max_control_torque_nm", params.get("max_control_torque_nm", 0.05)), "controller.max_control_torque_nm"),
        )

    def control_modules(self) -> tuple[str, ...]:
        return CONTROL_TO_MODULES[self.control_law]

    def to_dict(self) -> dict[str, Any]:
        return {
            "control_law": self.control_law,
            "K": self.k_gain,
            "P": self.p_gain,
            "Ki": self.ki_gain,
            "integral_limit": self.integral_limit,
            "max_control_torque_nm": self.max_control_torque_nm,
            "configured_modules": list(self.control_modules()),
        }


@dataclass(frozen=True)
class BasiliskReactionWheelFswConfig:
    """Basilisk reaction-wheel effector/interface configuration surface."""

    geometry: str = "pyramid"
    num_wheels: int = 4
    wheel_inertia_kg_m2: float = 0.08
    max_torque_nm: float = 0.05
    max_speed_rad_s: float = 6000.0
    wheel_axes_body: tuple[tuple[float, float, float], ...] = field(default_factory=tuple)

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskReactionWheelFswConfig":
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        rw = params.get("reaction_wheels") if isinstance(params.get("reaction_wheels"), Mapping) else {}
        geometry = _enum(rw.get("geometry", params.get("wheel_geometry")), "parameters.reaction_wheels.geometry", SUPPORTED_RW_GEOMETRIES, "pyramid")
        default_count = 3 if geometry == "three_axis" else 4
        num_wheels = int(rw.get("num_wheels", params.get("num_wheels", default_count)))
        if num_wheels < 3 or num_wheels > 8:
            raise BasiliskAdcsFswError("reaction_wheels.num_wheels must be within [3, 8]")
        axes_raw = rw.get("axes_body") or rw.get("wheel_axes_body")
        axes: tuple[tuple[float, float, float], ...]
        if axes_raw is None:
            axes = tuple()
        else:
            if not isinstance(axes_raw, Sequence) or isinstance(axes_raw, (str, bytes, bytearray)):
                raise BasiliskAdcsFswError("reaction_wheels.axes_body must be a sequence of 3-vectors")
            axes = tuple(_vec3(v, "reaction_wheels.axes_body[*]", (0.0, 0.0, 1.0)) for v in axes_raw)
            if len(axes) != num_wheels:
                raise BasiliskAdcsFswError("reaction_wheels.axes_body length must equal num_wheels")
        return cls(
            geometry=geometry,
            num_wheels=num_wheels,
            wheel_inertia_kg_m2=_positive(rw.get("wheel_inertia_kg_m2", params.get("wheel_inertia_kg_m2", 0.08)), "reaction_wheels.wheel_inertia_kg_m2"),
            max_torque_nm=_positive(rw.get("max_torque_nm", params.get("rw_max_torque_nm", 0.05)), "reaction_wheels.max_torque_nm"),
            max_speed_rad_s=_positive(rw.get("max_speed_rad_s", params.get("rw_max_speed_rad_s", 6000.0)), "reaction_wheels.max_speed_rad_s"),
            wheel_axes_body=axes,
        )

    def default_axes(self) -> tuple[tuple[float, float, float], ...]:
        if self.wheel_axes_body:
            return self.wheel_axes_body
        if self.geometry == "three_axis":
            return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        # 4-wheel pyramid, normalized enough for configuration trace purposes.
        s = 1.0 / math.sqrt(3.0)
        return ((s, s, s), (-s, s, s), (-s, -s, s), (s, -s, s))[: self.num_wheels]

    def to_dict(self) -> dict[str, Any]:
        axes = self.default_axes()
        return {
            "geometry": self.geometry,
            "num_wheels": self.num_wheels,
            "wheel_inertia_kg_m2": self.wheel_inertia_kg_m2,
            "max_torque_nm": self.max_torque_nm,
            "max_speed_rad_s": self.max_speed_rad_s,
            "wheel_axes_body": [list(v) for v in axes],
            "max_axis_norm": max(vector_norm(v) for v in axes) if axes else 0.0,
        }


@dataclass(frozen=True)
class BasiliskAdcsEstimatorSensorConfig:
    """Basilisk sensor and estimator configuration surface."""

    estimator: str = "simple_nav"
    sensors: tuple[str, ...] = ("imu", "star_tracker", "css")
    sensor_dropout_requested: bool = False
    gyro_bias_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    star_tracker_noise_rad: float = math.radians(0.002)
    sun_sensor_noise_rad: float = math.radians(0.2)

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskAdcsEstimatorSensorConfig":
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        est = params.get("estimator") if isinstance(params.get("estimator"), Mapping) else {}
        sensors_payload = params.get("sensors") if isinstance(params.get("sensors"), Mapping) else {}
        estimator = _enum(est.get("type", params.get("estimator_type")), "parameters.estimator.type", SUPPORTED_ESTIMATORS, "simple_nav")
        sensors = tuple(
            _enum(item, "parameters.sensors.enabled", set(SENSOR_TO_MODULES), "imu")
            for item in _as_list(sensors_payload.get("enabled", params.get("sensor_suite")), ("imu", "star_tracker", "css"))
        )
        dropout = params.get("sensor_dropout") if isinstance(params.get("sensor_dropout"), Mapping) else {}
        gyro_bias = params.get("gyro_bias_rad_s") or sensors_payload.get("gyro_bias_rad_s") or (0.0, 0.0, 0.0)
        if params.get("gyro_bias_deg_s") is not None and params.get("gyro_bias_rad_s") is None:
            gyro_bias = tuple(math.radians(x) for x in _vec3(params.get("gyro_bias_deg_s"), "gyro_bias_deg_s", (0.0, 0.0, 0.0)))
        return cls(
            estimator=estimator,
            sensors=sensors,
            sensor_dropout_requested=bool(dropout or sensors_payload.get("dropout") or params.get("sensor_dropout_enabled")),
            gyro_bias_rad_s=_vec3(gyro_bias, "gyro_bias_rad_s", (0.0, 0.0, 0.0)),
            star_tracker_noise_rad=math.radians(float(sensors_payload.get("star_tracker_noise_deg", params.get("star_tracker_noise_deg", 0.002)))),
            sun_sensor_noise_rad=math.radians(float(sensors_payload.get("sun_sensor_noise_deg", params.get("sun_sensor_noise_deg", 0.2)))),
        )

    def configured_modules(self) -> tuple[str, ...]:
        modules: list[str] = []
        modules.extend(ESTIMATOR_TO_MODULES[self.estimator])
        for sensor in self.sensors:
            modules.extend(SENSOR_TO_MODULES[sensor])
        # stable order, no duplicates
        seen: set[str] = set()
        out: list[str] = []
        for module in modules:
            if module not in seen:
                seen.add(module)
                out.append(module)
        return tuple(out)

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimator": self.estimator,
            "sensors": list(self.sensors),
            "sensor_dropout_requested": self.sensor_dropout_requested,
            "gyro_bias_rad_s": list(self.gyro_bias_rad_s),
            "gyro_bias_norm_rad_s": vector_norm(self.gyro_bias_rad_s),
            "star_tracker_noise_rad": self.star_tracker_noise_rad,
            "sun_sensor_noise_rad": self.sun_sensor_noise_rad,
            "configured_modules": list(self.configured_modules()),
            "claim_boundary": "sensor/estimator modules are configuration surfaces in BSK-ADCS-1; sensor dropout recovery benchmark is BSK-ADCS-2 scope",
        }


@dataclass(frozen=True)
class BasiliskAdcsFswConfig:
    """Complete BSK-ADCS-1 ADCS/FSW native configuration."""

    duration_s: float
    sample_s: float
    fsw_step_s: float
    dynamics_step_s: float
    mode: BasiliskAdcsModeConfig
    controller: BasiliskAdcsControllerConfig
    reaction_wheels: BasiliskReactionWheelFswConfig
    estimator_sensors: BasiliskAdcsEstimatorSensorConfig
    inertia_kg_m2: tuple[float, float, float]
    initial_attitude_error_deg: float
    initial_rate_rad_s: tuple[float, float, float]
    task_id: str = "adcs_basilisk_fsw_task"

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "BasiliskAdcsFswConfig":
        sim = spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}
        params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
        solver = sim.get("solver") if isinstance(sim.get("solver"), Mapping) else {}
        duration_s = _positive(sim.get("duration_s", 600.0), "simulation.duration_s")
        sample_s = _positive(sim.get("sample_s", 10.0), "simulation.sample_s")
        if sample_s > duration_s:
            raise BasiliskAdcsFswError("simulation.sample_s must not exceed simulation.duration_s")
        fsw_step = _positive(params.get("fsw_step_s", sim.get("fsw_step_s", sample_s)), "parameters.fsw_step_s")
        dyn_step = _positive(solver.get("step_s", params.get("dynamics_step_s", min(sample_s, fsw_step))), "simulation.solver.step_s")
        inertia = _vec3(params.get("inertia_kg_m2"), "parameters.inertia_kg_m2", (12.0, 10.0, 8.0))
        if any(x <= 0.0 for x in inertia):
            raise BasiliskAdcsFswError("parameters.inertia_kg_m2 entries must be positive")
        initial_rate = params.get("initial_rate_rad_s")
        if initial_rate is None and params.get("initial_rate_deg_s") is not None:
            initial_rate = tuple(math.radians(x) for x in _vec3(params.get("initial_rate_deg_s"), "initial_rate_deg_s", (0.0, 0.0, 0.0)))
        return cls(
            duration_s=duration_s,
            sample_s=sample_s,
            fsw_step_s=fsw_step,
            dynamics_step_s=dyn_step,
            mode=BasiliskAdcsModeConfig.from_task_spec(spec),
            controller=BasiliskAdcsControllerConfig.from_task_spec(spec),
            reaction_wheels=BasiliskReactionWheelFswConfig.from_task_spec(spec),
            estimator_sensors=BasiliskAdcsEstimatorSensorConfig.from_task_spec(spec),
            inertia_kg_m2=inertia,
            initial_attitude_error_deg=_nonnegative(params.get("initial_attitude_error_deg", 12.0), "parameters.initial_attitude_error_deg"),
            initial_rate_rad_s=_vec3(initial_rate, "parameters.initial_rate_rad_s", (0.0, 0.0, 0.0)),
            task_id=str(spec.get("task_id") or "adcs_basilisk_fsw_task"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BASILISK_ADCS_FSW_SCHEMA_VERSION,
            "task_id": self.task_id,
            "duration_s": self.duration_s,
            "sample_s": self.sample_s,
            "fsw_step_s": self.fsw_step_s,
            "dynamics_step_s": self.dynamics_step_s,
            "mode": self.mode.to_dict(),
            "controller": self.controller.to_dict(),
            "reaction_wheels": self.reaction_wheels.to_dict(),
            "estimator_sensors": self.estimator_sensors.to_dict(),
            "inertia_kg_m2": list(self.inertia_kg_m2),
            "initial_attitude_error_deg": self.initial_attitude_error_deg,
            "initial_rate_rad_s": list(self.initial_rate_rad_s),
        }


def _required_modules(config: BasiliskAdcsFswConfig | None = None) -> tuple[str, ...]:
    """Return modules instantiated by the BSK-RUN-5 native runtime.

    Advanced guidance, estimator and sensor entries remain configuration-only
    surfaces.  They must not make the minimal inertial runtime appear
    unavailable merely because an uninstantiated optional module is absent.
    """

    return (
        "Basilisk",
        "Basilisk.architecture.messaging",
        "Basilisk.utilities.SimulationBaseClass",
        "Basilisk.utilities.macros",
        "Basilisk.utilities.simIncludeRW",
        "Basilisk.simulation.spacecraft",
        "Basilisk.simulation.reactionWheelStateEffector",
        "Basilisk.simulation.simpleNav",
        "Basilisk.fswAlgorithms.inertial3D",
        "Basilisk.fswAlgorithms.attTrackingError",
        "Basilisk.fswAlgorithms.mrpFeedback",
        "Basilisk.fswAlgorithms.rwMotorTorque",
    )


def check_basilisk_adcs_availability(config: BasiliskAdcsFswConfig | None = None) -> BasiliskAdcsAvailability:
    """Return optional Basilisk ADCS dependency availability without raising."""

    checked = _required_modules(config)
    try:
        import Basilisk  # type: ignore
        for module in checked:
            if module == "Basilisk":
                continue
            __import__(module)
        package_path = None
        if getattr(Basilisk, "__path__", None):
            package_path = str(Basilisk.__path__[0])
        version = getattr(Basilisk, "__version__", None)
        return BasiliskAdcsAvailability(True, checked_modules=checked, package_path=package_path, package_version=str(version) if version else None)
    except Exception as exc:
        # In construction/test containers Basilisk is normally absent.  Listing
        # every checked module as missing is conservative and auditable.
        return BasiliskAdcsAvailability(False, checked_modules=checked, missing_modules=checked, error=f"{type(exc).__name__}: {exc}")


def build_basilisk_adcs_fsw_blueprint(config: BasiliskAdcsFswConfig) -> dict[str, Any]:
    """Build an auditable Basilisk FSW configuration blueprint for BSK-ADCS-1."""

    modules: list[str] = [
        "SimulationBaseClass.SimBaseClass",
        "dynamics_process: spacecraft.Spacecraft + reactionWheelStateEffector",
        "fsw_process: modeRequest-driven FSW tasks",
        "simIncludeRW.rwFactory",
        "fswAlgorithms.rwConfigData",
        "fswAlgorithms.rwMotorTorque",
        "fswAlgorithms.attTrackingError",
    ]
    modules.extend(m.split("Basilisk.")[-1] for m in config.mode.guidance_modules())
    modules.extend(m.split("Basilisk.")[-1] for m in config.controller.control_modules())
    modules.extend(m.split("Basilisk.")[-1] for m in config.estimator_sensors.configured_modules())
    seen: set[str] = set()
    configured_modules: list[str] = []
    for module in modules:
        if module not in seen:
            seen.add(module)
            configured_modules.append(module)
    return {
        "schema_version": BASILISK_ADCS_FSW_SCHEMA_VERSION,
        "backend_type": "basilisk_native",
        "basilisk_required": True,
        "execution_scope": "configuration_blueprint_only_bsk_adcs1_runtime_benchmark_pending",
        "processes": {
            "dynamics_process": {"task_step_s": config.dynamics_step_s, "modules": ["spacecraft.Spacecraft", "reactionWheelStateEffector"]},
            "fsw_process": {"task_step_s": config.fsw_step_s, "modules": configured_modules},
        },
        "mode_request": config.mode.requested_mode,
        "mode_manager": config.mode.to_dict(),
        "controller": config.controller.to_dict(),
        "reaction_wheels": config.reaction_wheels.to_dict(),
        "estimator_sensors": config.estimator_sensors.to_dict(),
        "vehicle_config": {"inertia_kg_m2": list(config.inertia_kg_m2)},
        "initial_conditions": {"attitude_error_deg": config.initial_attitude_error_deg, "rate_rad_s": list(config.initial_rate_rad_s)},
        "state_logging": {
            "messages": ["att_guidance", "cmdTorqueBody", "rwMotorTorque", "rwSpeed", "scStateOut"],
            "sample_s": config.sample_s,
        },
        "claim_guardrail": "BSK-RUN-5 can execute a minimal inertial/MRP-feedback/simpleNav/RW runtime smoke when Basilisk is present; it is not full FSW, FDIR, flight calibration, or benchmark validation.",
        "fallback_policy": "No implicit fallback: do not silently fall back to subsystem.adcs_fidelity.v1. Use the local proxy explicitly for fallback or comparison baselines.",
    }


def _bsk_run5_runtime_scope(config: BasiliskAdcsFswConfig) -> tuple[bool, str]:
    """Return whether the BSK-RUN-5 minimal runtime path supports this config."""

    unsupported: list[str] = []
    if config.mode.requested_mode not in BSK_RUN5_RUNTIME_GUIDANCE_MODES:
        unsupported.append(f"guidance_mode={config.mode.requested_mode}")
    if config.controller.control_law not in BSK_RUN5_RUNTIME_CONTROL_LAWS:
        unsupported.append(f"control_law={config.controller.control_law}")
    if config.estimator_sensors.estimator not in BSK_RUN5_RUNTIME_ESTIMATORS:
        unsupported.append(f"estimator={config.estimator_sensors.estimator}")
    if unsupported:
        return False, "unsupported_for_bsk_run5_minimal_runtime: " + ", ".join(unsupported)
    return True, "inertial3d_mrp_feedback_or_rate_damping_simple_nav_rw_runtime_supported"


def _sigma_from_single_axis_error(error_deg: float) -> tuple[float, float, float]:
    """Convert a principal-axis attitude error angle to an MRP vector."""

    angle_rad = math.radians(float(error_deg))
    sigma = math.tan(angle_rad / 4.0)
    return (sigma, -0.5 * sigma, sigma / 3.0)


def _matrix_diag_flat(diagonal: tuple[float, float, float]) -> list[float]:
    return [diagonal[0], 0.0, 0.0, 0.0, diagonal[1], 0.0, 0.0, 0.0, diagonal[2]]


def _matrix_diag_nested(diagonal: tuple[float, float, float]) -> list[list[float]]:
    return [[diagonal[0], 0.0, 0.0], [0.0, diagonal[1], 0.0], [0.0, 0.0, diagonal[2]]]


def _clip(value: float, limit: float) -> float:
    return max(-limit, min(limit, float(value)))


def _first3(values: Any) -> tuple[float, float, float]:
    try:
        return (float(values[0]), float(values[1]), float(values[2]))
    except Exception:
        return (0.0, 0.0, 0.0)


def build_bsk_adcs1_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return normalized BSK-ADCS-1 configuration payload."""

    config = BasiliskAdcsFswConfig.from_task_spec(spec)
    return {
        "schema_version": BASILISK_ADCS_FSW_SCHEMA_VERSION,
        "config": config.to_dict(),
        "availability": check_basilisk_adcs_availability(config).to_dict(),
        "blueprint": build_basilisk_adcs_fsw_blueprint(config),
    }


def run_basilisk_adcs_fsw_if_available(config: BasiliskAdcsFswConfig) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Run the BSK-RUN-5 minimal Basilisk ADCS/FSW runtime smoke.

    Runtime scope is deliberately narrow and auditable: spacecraft hub attitude
    dynamics, simpleNav/truth attitude navigation, inertial3D reference,
    attTrackingError, mrpFeedback/rate-damping control, rwMotorTorque, and a
    three-axis reaction-wheel state effector.  Unsupported FSW modes return an
    explicit unavailable result rather than a fabricated pass.
    """

    availability = check_basilisk_adcs_availability(config)
    if not availability.available:
        raise BasiliskAdcsFswRuntimeUnavailable("Basilisk ADCS/FSW modules are not available", availability.to_dict())
    runtime_supported, runtime_scope = _bsk_run5_runtime_scope(config)
    if not runtime_supported:
        raise BasiliskAdcsFswRuntimeUnavailable(runtime_scope, {"config": config.to_dict(), "availability": availability.to_dict()})

    from Basilisk.architecture import messaging  # type: ignore
    from Basilisk.fswAlgorithms import attTrackingError, inertial3D, mrpFeedback, rwMotorTorque  # type: ignore
    from Basilisk.simulation import reactionWheelStateEffector, simpleNav, spacecraft  # type: ignore
    from Basilisk.utilities import SimulationBaseClass, macros, simIncludeRW  # type: ignore

    sc_sim = SimulationBaseClass.SimBaseClass()
    dyn_process = sc_sim.CreateNewProcess("bsk_run5_adcs_dynamics_process")
    fsw_process = sc_sim.CreateNewProcess("bsk_run5_adcs_fsw_process")
    dyn_task = "bsk_run5_adcs_dynamics_task"
    fsw_task = "bsk_run5_adcs_fsw_task"
    dyn_process.addTask(sc_sim.CreateNewTask(dyn_task, macros.sec2nano(config.dynamics_step_s)))
    fsw_process.addTask(sc_sim.CreateNewTask(fsw_task, macros.sec2nano(config.fsw_step_s)))
    sample_ns = macros.sec2nano(config.sample_s)

    sc_object = spacecraft.Spacecraft()
    sc_object.ModelTag = "bsk-run5-adcs-spacecraft"
    sc_object.hub.mHub = 12.0
    sc_object.hub.IHubPntBc_B = _matrix_diag_nested(config.inertia_kg_m2)
    initial_sigma = _sigma_from_single_axis_error(config.initial_attitude_error_deg)
    sc_object.hub.sigma_BNInit = [[initial_sigma[0]], [initial_sigma[1]], [initial_sigma[2]]]
    sc_object.hub.omega_BN_BInit = [[config.initial_rate_rad_s[0]], [config.initial_rate_rad_s[1]], [config.initial_rate_rad_s[2]]]
    # Translation is not the controlled quantity in this ADCS smoke, but the
    # Spacecraft state message expects a finite translational state.
    sc_object.hub.r_CN_NInit = [7_000_000.0, 0.0, 0.0]
    sc_object.hub.v_CN_NInit = [0.0, 7_500.0, 0.0]
    sc_sim.AddModelToTask(dyn_task, sc_object, 1)

    rw_effector = reactionWheelStateEffector.ReactionWheelStateEffector()
    rw_factory = simIncludeRW.rwFactory()
    axes = config.reaction_wheels.default_axes()
    # BSK-RUN-5 uses at most the first three non-coplanar axes for a minimal
    # full-rank RW interface smoke.  More complex pyramid allocation remains a
    # later ADCS benchmark concern.
    runtime_axes = axes[:3]
    if len(runtime_axes) < 3:
        raise BasiliskAdcsFswRuntimeUnavailable("at least three reaction-wheel axes are required", {"axes": [list(x) for x in axes]})
    max_speed_rpm = config.reaction_wheels.max_speed_rad_s / macros.RPM
    for idx, axis in enumerate(runtime_axes):
        rw_factory.create(
            "Honeywell_HR16",
            list(axis),
            maxMomentum=50.0,
            Omega=min(100.0 * (idx + 1), max_speed_rpm),
            u_max=float(config.reaction_wheels.max_torque_nm),
            label=f"RW{idx + 1}",
        )
    rw_factory.addToSpacecraft("bsk-run5-rw-state-effector", rw_effector, sc_object)
    sc_sim.AddModelToTask(dyn_task, rw_effector, 2)
    rw_config_msg = rw_factory.getConfigMessage()

    nav = simpleNav.SimpleNav()
    nav.ModelTag = "bsk-run5-simple-nav"
    nav.scStateInMsg.subscribeTo(sc_object.scStateOutMsg)
    sc_sim.AddModelToTask(dyn_task, nav)

    inertial_ref = inertial3D.inertial3D()
    inertial_ref.ModelTag = "bsk-run5-inertial3d"
    inertial_ref.sigma_R0N = [0.0, 0.0, 0.0]
    sc_sim.AddModelToTask(fsw_task, inertial_ref)

    attitude_error = attTrackingError.attTrackingError()
    attitude_error.ModelTag = "bsk-run5-att-tracking-error"
    attitude_error.attRefInMsg.subscribeTo(inertial_ref.attRefOutMsg)
    attitude_error.attNavInMsg.subscribeTo(nav.attOutMsg)
    sc_sim.AddModelToTask(fsw_task, attitude_error)

    controller = mrpFeedback.mrpFeedback()
    controller.ModelTag = "bsk-run5-mrp-feedback"
    controller.K = float(config.controller.k_gain)
    controller.P = float(config.controller.p_gain)
    if config.controller.ki_gain > 0.0:
        controller.Ki = float(config.controller.ki_gain)
        controller.integralLimit = float(config.controller.integral_limit)
    else:
        controller.Ki = -1.0
        controller.integralLimit = -1.0
    if config.controller.control_law == "rate_damping" or config.mode.requested_mode == "detumble":
        controller.K = 0.0
    controller.guidInMsg.subscribeTo(attitude_error.attGuidOutMsg)
    veh_payload = messaging.VehicleConfigMsgPayload()
    veh_payload.ISCPntB_B = _matrix_diag_flat(config.inertia_kg_m2)
    veh_payload.massSC = 12.0
    veh_msg = messaging.VehicleConfigMsg().write(veh_payload)
    controller.vehConfigInMsg.subscribeTo(veh_msg)
    controller.rwParamsInMsg.subscribeTo(rw_config_msg)
    controller.rwSpeedsInMsg.subscribeTo(rw_effector.rwSpeedOutMsg)
    sc_sim.AddModelToTask(fsw_task, controller)

    motor_torque = rwMotorTorque.rwMotorTorque()
    motor_torque.ModelTag = "bsk-run5-rw-motor-torque"
    motor_torque.controlAxes_B = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    motor_torque.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
    motor_torque.rwParamsInMsg.subscribeTo(rw_config_msg)
    sc_sim.AddModelToTask(fsw_task, motor_torque)
    rw_effector.rwMotorCmdInMsg.subscribeTo(motor_torque.rwMotorTorqueOutMsg)

    sc_rec = sc_object.scStateOutMsg.recorder(sample_ns)
    nav_rec = nav.attOutMsg.recorder(sample_ns)
    err_rec = attitude_error.attGuidOutMsg.recorder(sample_ns)
    torque_rec = controller.cmdTorqueOutMsg.recorder(sample_ns)
    motor_rec = motor_torque.rwMotorTorqueOutMsg.recorder(sample_ns)
    rw_rec = rw_effector.rwSpeedOutMsg.recorder(sample_ns)
    sc_sim.AddModelToTask(dyn_task, sc_rec)
    sc_sim.AddModelToTask(dyn_task, nav_rec)
    sc_sim.AddModelToTask(fsw_task, err_rec)
    sc_sim.AddModelToTask(fsw_task, torque_rec)
    sc_sim.AddModelToTask(fsw_task, motor_rec)
    sc_sim.AddModelToTask(dyn_task, rw_rec)

    sc_sim.InitializeSimulation()
    sc_sim.ConfigureStopTime(macros.sec2nano(config.duration_s))
    sc_sim.ExecuteSimulation()

    rows: list[dict[str, Any]] = []
    times = sc_rec.times() * macros.NANO2SEC
    for idx, t_s in enumerate(times):
        sigma = _first3(sc_rec.sigma_BN[idx])
        rate = _first3(sc_rec.omega_BN_B[idx])
        sigma_br = _first3(err_rec.sigma_BR[idx])
        omega_br = _first3(err_rec.omega_BR_B[idx])
        cmd_torque = _first3(torque_rec.torqueRequestBody[idx])
        motor_cmd = _first3(motor_rec.motorTorque[idx])
        rw_speed = tuple(float(x) for x in rw_rec.wheelSpeeds[idx][: len(runtime_axes)])
        rows.append({
            "time_s": float(t_s),
            "attitude.sigma_bn_x": sigma[0],
            "attitude.sigma_bn_y": sigma[1],
            "attitude.sigma_bn_z": sigma[2],
            "attitude.sigma_bn_norm": vector_norm(sigma),
            "attitude.omega_bn_b_rad_s_x": rate[0],
            "attitude.omega_bn_b_rad_s_y": rate[1],
            "attitude.omega_bn_b_rad_s_z": rate[2],
            "attitude.omega_bn_b_norm_rad_s": vector_norm(rate),
            "control.sigma_br_x": sigma_br[0],
            "control.sigma_br_y": sigma_br[1],
            "control.sigma_br_z": sigma_br[2],
            "control.sigma_br_norm": vector_norm(sigma_br),
            "control.omega_br_b_norm_rad_s": vector_norm(omega_br),
            "control.cmd_torque_b_nm_x": cmd_torque[0],
            "control.cmd_torque_b_nm_y": cmd_torque[1],
            "control.cmd_torque_b_nm_z": cmd_torque[2],
            "control.cmd_torque_b_norm_nm": vector_norm(cmd_torque),
            "rw.motor_torque_nm_x": motor_cmd[0],
            "rw.motor_torque_nm_y": motor_cmd[1],
            "rw.motor_torque_nm_z": motor_cmd[2],
            "rw.motor_torque_norm_nm": vector_norm(motor_cmd),
            "rw.speed_rad_s_max_abs": max(abs(x) for x in rw_speed) if rw_speed else 0.0,
            "rw.num_runtime_wheels": len(runtime_axes),
            "backend_type": "basilisk_native",
        })

    sigma_norms = [float(row["control.sigma_br_norm"]) for row in rows]
    rate_norms = [float(row["attitude.omega_bn_b_norm_rad_s"]) for row in rows]
    torque_norms = [float(row["control.cmd_torque_b_norm_nm"]) for row in rows]
    motor_norms = [float(row["rw.motor_torque_norm_nm"]) for row in rows]
    initial_pointing_error_deg = 4.0 * math.degrees(math.atan(sigma_norms[0])) if sigma_norms else None
    final_pointing_error_deg = 4.0 * math.degrees(math.atan(sigma_norms[-1])) if sigma_norms else None
    summary = {
        "schema_version": BASILISK_ADCS_FSW_SCHEMA_VERSION,
        "backend_type": "basilisk_native",
        "basilisk_required": True,
        "basilisk_status": "executed",
        "runtime_version": "BSK-RUN-5",
        "runtime_scope": runtime_scope,
        "force_model_scope": "attitude_dynamics_simple_nav_inertial3d_mrp_feedback_rw_motor_torque",
        "physical_validation_status": "runtime_smoke_passed_not_flight_validated",
        "can_claim_high_fidelity": False,
        "trace_rows": len(rows),
        "availability": availability.to_dict(),
        "blueprint": build_basilisk_adcs_fsw_blueprint(config),
        "runtime_configuration": {
            "guidance": "inertial3D",
            "estimator": "simpleNav/truth_state",
            "controller": "mrpFeedback_rate_damping_variant" if (config.controller.control_law == "rate_damping" or config.mode.requested_mode == "detumble") else "mrpFeedback",
            "rw_interface": "rwMotorTorque + reactionWheelStateEffector",
            "controller_integral_mode": "enabled" if config.controller.ki_gain > 0.0 else "disabled_with_negative_Ki",
            "telemetry_policy": "raw_native_message_values_no_posthoc_clipping",
            "runtime_wheel_axes_body": [list(axis) for axis in runtime_axes],
        },
        "qoi": {
            "adcs.initial_pointing_error_deg": initial_pointing_error_deg,
            "adcs.final_pointing_error_deg": final_pointing_error_deg,
            "adcs.pointing_error_delta_deg": (final_pointing_error_deg - initial_pointing_error_deg) if initial_pointing_error_deg is not None and final_pointing_error_deg is not None else None,
            "adcs.max_abs_rate_rad_s": max(rate_norms) if rate_norms else None,
            "adcs.final_rate_norm_rad_s": rate_norms[-1] if rate_norms else None,
            "adcs.control_torque_norm_max_nm": max(torque_norms) if torque_norms else None,
            "adcs.rw_motor_torque_norm_max_nm": max(motor_norms) if motor_norms else None,
            "adcs.rw_speed_max_abs_rad_s": max(float(row["rw.speed_rad_s_max_abs"]) for row in rows) if rows else None,
        },
        "events": {"fault_count": 0, "degradation_count": 0},
        "fallback_policy": "no_implicit_fallback_use_subsystem.adcs_fidelity.v1_explicitly",
    }
    return summary, tuple(rows)


__all__ = [
    "BASILISK_ADCS_FSW_SCHEMA_VERSION",
    "BasiliskAdcsFswError",
    "BasiliskAdcsFswRuntimeUnavailable",
    "BasiliskAdcsAvailability",
    "BasiliskAdcsModeConfig",
    "BasiliskAdcsControllerConfig",
    "BasiliskReactionWheelFswConfig",
    "BasiliskAdcsEstimatorSensorConfig",
    "BasiliskAdcsFswConfig",
    "build_basilisk_adcs_fsw_blueprint",
    "build_bsk_adcs1_payload",
    "check_basilisk_adcs_availability",
    "run_basilisk_adcs_fsw_if_available",
]
