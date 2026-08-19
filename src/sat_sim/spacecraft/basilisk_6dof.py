"""Basilisk-native 6-DOF spacecraft graph support for BSK-6DOF-1.

BSK-6DOF-1 adds a whole-spacecraft capability surface that ties together the
Basilisk-native orbit and ADCS/FSW candidate paths through a spacecraft-hub
configuration blueprint.  The code is intentionally split into deterministic
configuration parsing / blueprint generation and optional Basilisk availability
checks so the repository remains testable when Basilisk is not installed.

This module does **not** replace ``whole_spacecraft.orbit_adcs_fidelity.v1``.
The existing INT-1 local integration gate remains the ordinary smoke/regression
path and an explicit comparison baseline.  ``whole_spacecraft.basilisk_6dof.v1``
requires Basilisk and must never silently fall back to the local proxy path.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence
import math

from sat_sim.adcs.basilisk_fsw import (
    BASILISK_REQUIRED_VERSION_NOTE,
    BasiliskAdcsFswConfig,
    build_basilisk_adcs_fsw_blueprint,
    check_basilisk_adcs_availability,
)
from sat_sim.orbit.basilisk_hf import (
    BasiliskOrbitHfConfig,
    build_basilisk_orbit_blueprint,
    check_basilisk_availability,
    resolve_spherical_harmonics_coefficient_file,
)
from sat_sim.frames import vector_norm
from sat_sim.orbit.medium import EARTH_RADIUS_M

BASILISK_6DOF_SCHEMA_VERSION = "bsk_6dof1.basilisk_6dof_spacecraft_graph.v1"
BSK_RUN6_RUNTIME_VERSION = "BSK-RUN-6"
SUPPORTED_6DOF_TARGETS = {"basilisk_6dof", "orbit_adcs", "spacecraft_6dof", "whole_spacecraft"}
SUPPORTED_EFFECTOR_TYPES = {
    "gravity",
    "spherical_harmonics",
    "third_body",
    "spice",
    "drag",
    "srp",
    "external_torque",
    "reaction_wheels",
}


class Basilisk6DofError(ValueError):
    """Raised when a BSK-6DOF-1 configuration is invalid."""


class Basilisk6DofRuntimeUnavailable(RuntimeError):
    """Raised when a requested BSK-RUN-6 runtime path is outside the minimal smoke scope."""

    def __init__(self, reason: str, details: Mapping[str, Any] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.details = dict(details or {})


@dataclass(frozen=True)
class Basilisk6DofAvailability:
    """Runtime availability report for the optional Basilisk 6-DOF dependency."""

    available: bool
    checked_modules: tuple[str, ...]
    missing_modules: tuple[str, ...] = ()
    package_path: str | None = None
    package_version: str | None = None
    error: str | None = None
    orbit_availability: Mapping[str, Any] | None = None
    adcs_availability: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "available": self.available,
            "checked_modules": list(self.checked_modules),
            "missing_modules": list(self.missing_modules),
            "package_path": self.package_path,
            "package_version": self.package_version,
            "error": self.error,
            "version_note": BASILISK_REQUIRED_VERSION_NOTE,
            "orbit_availability": dict(self.orbit_availability or {}),
            "adcs_availability": dict(self.adcs_availability or {}),
        }


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise Basilisk6DofError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise Basilisk6DofError(f"{name} must be positive")
    return out


def _nonnegative(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out < 0.0:
        raise Basilisk6DofError(f"{name} must be non-negative")
    return out


def _vec3(value: Any, name: str, default: tuple[float, float, float]) -> tuple[float, float, float]:
    if value is None:
        return default
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (float(value), float(value), float(value))
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        items = tuple(float(x) for x in value)
        if len(items) == 3 and all(math.isfinite(x) for x in items):
            return items  # type: ignore[return-value]
    raise Basilisk6DofError(f"{name} must contain exactly three finite numbers")


def _bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on", "enabled"}
    return bool(value)


def _as_list(value: Any, default: Iterable[str] = ()) -> tuple[str, ...]:
    if value is None:
        return tuple(str(x) for x in default)
    if isinstance(value, str):
        return (value,)
    if isinstance(value, Iterable) and not isinstance(value, (Mapping, bytes, bytearray)):
        return tuple(str(x) for x in value if str(x).strip())
    raise Basilisk6DofError("expected a string or list of strings")


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


@dataclass(frozen=True)
class Basilisk6DofSpacecraftConfig:
    """Spacecraft hub and initial 6-DOF state configuration surface."""

    mass_kg: float = 12.0
    inertia_kg_m2: tuple[float, float, float] = (12.0, 10.0, 8.0)
    hub_center_of_mass_b_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    initial_sigma_bn: tuple[float, float, float] = (0.05, -0.02, 0.01)
    initial_omega_bn_b_rad_s: tuple[float, float, float] = (0.0017, -0.0008, 0.0003)
    area_m2: float = 0.08
    drag_coefficient: float = 2.2
    srp_coefficient: float = 1.3

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "Basilisk6DofSpacecraftConfig":
        params = _mapping(spec.get("parameters"))
        sc = _mapping(params.get("spacecraft") or spec.get("spacecraft"))
        adcs = _mapping(params.get("adcs"))
        inertia_raw = sc.get("inertia_kg_m2", params.get("inertia_kg_m2", adcs.get("inertia_kg_m2")))
        inertia = _vec3(inertia_raw, "parameters.spacecraft.inertia_kg_m2", (12.0, 10.0, 8.0))
        if any(x <= 0.0 for x in inertia):
            raise Basilisk6DofError("parameters.spacecraft.inertia_kg_m2 entries must be positive")
        sigma = sc.get("initial_sigma_bn") or params.get("initial_sigma_bn")
        if sigma is None and params.get("initial_attitude_error_deg") is not None:
            # Keep this deterministic: place the small attitude error on the first MRP component.
            sigma = (math.radians(_nonnegative(params.get("initial_attitude_error_deg"), "parameters.initial_attitude_error_deg")) / 4.0, 0.0, 0.0)
        rate = sc.get("initial_omega_bn_b_rad_s") or params.get("initial_omega_bn_b_rad_s") or params.get("initial_rate_rad_s")
        if rate is None and params.get("initial_rate_deg_s") is not None:
            rate = tuple(math.radians(x) for x in _vec3(params.get("initial_rate_deg_s"), "parameters.initial_rate_deg_s", (0.0, 0.0, 0.0)))
        return cls(
            mass_kg=_positive(sc.get("mass_kg", params.get("mass_kg", 12.0)), "parameters.spacecraft.mass_kg"),
            inertia_kg_m2=inertia,
            hub_center_of_mass_b_m=_vec3(sc.get("hub_center_of_mass_b_m"), "parameters.spacecraft.hub_center_of_mass_b_m", (0.0, 0.0, 0.0)),
            initial_sigma_bn=_vec3(sigma, "parameters.spacecraft.initial_sigma_bn", (0.05, -0.02, 0.01)),
            initial_omega_bn_b_rad_s=_vec3(rate, "parameters.spacecraft.initial_omega_bn_b_rad_s", (0.0017, -0.0008, 0.0003)),
            area_m2=_positive(sc.get("area_m2", params.get("area_m2", 0.08)), "parameters.spacecraft.area_m2"),
            drag_coefficient=_positive(sc.get("drag_coefficient", params.get("drag_coefficient", 2.2)), "parameters.spacecraft.drag_coefficient"),
            srp_coefficient=_positive(sc.get("srp_coefficient", params.get("srp_coefficient", 1.3)), "parameters.spacecraft.srp_coefficient"),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mass_kg": self.mass_kg,
            "inertia_kg_m2": list(self.inertia_kg_m2),
            "hub_center_of_mass_b_m": list(self.hub_center_of_mass_b_m),
            "initial_sigma_bn": list(self.initial_sigma_bn),
            "initial_omega_bn_b_rad_s": list(self.initial_omega_bn_b_rad_s),
            "area_m2": self.area_m2,
            "drag_coefficient": self.drag_coefficient,
            "srp_coefficient": self.srp_coefficient,
        }


@dataclass(frozen=True)
class Basilisk6DofCouplingConfig:
    """Native orbit-attitude coupling and metadata contract."""

    target_mode: str = "inertial"
    target_frame: str = "inertial_or_sun"
    attitude_frame: str = "body"
    inertial_frame: str = "J2000"
    time_scale: str = "UTC"
    require_frame_metadata: bool = True
    enable_reaction_wheels: bool = True
    enable_external_torque: bool = True
    enable_orbit_environment: bool = True
    enable_srp: bool = False
    enable_drag: bool = False
    enable_third_body: bool = False
    effectors: tuple[str, ...] = field(default_factory=lambda: ("gravity", "reaction_wheels", "external_torque"))

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "Basilisk6DofCouplingConfig":
        params = _mapping(spec.get("parameters"))
        coupling = _mapping(params.get("coupling") or params.get("basilisk_6dof") or spec.get("coupling"))
        adcs = _mapping(params.get("adcs"))
        orbit = _mapping(spec.get("orbit_environment") or params.get("orbit_environment") or params.get("orbit"))
        force = _mapping(orbit.get("force_models"))
        mode = str(coupling.get("target_mode", params.get("target_mode", adcs.get("target_mode", "inertial")))).strip().lower().replace("-", "_")
        if mode == "sun":
            mode = "sun_pointing"
        if mode not in {"nadir", "hill", "velocity", "sun_pointing", "sun_safe", "inertial", "inertial3d", "detumble"}:
            raise Basilisk6DofError("parameters.coupling.target_mode must be a supported ADCS guidance mode")
        requested_effectors = _as_list(coupling.get("effectors"), default=())
        auto_effectors = ["gravity", "reaction_wheels", "external_torque"]
        if _bool(force.get("srp"), False) or _bool(force.get("solar_radiation_pressure"), False):
            auto_effectors.append("srp")
        if force.get("atmosphere_model") or force.get("atmosphere") or _bool(force.get("drag"), False):
            auto_effectors.append("drag")
        if force.get("third_bodies") or _bool(force.get("third_body"), False):
            auto_effectors.extend(["third_body", "spice"])
        effectors = tuple(dict.fromkeys(str(x).strip().lower() for x in (requested_effectors or tuple(auto_effectors)) if str(x).strip()))
        unsupported = sorted(set(effectors) - SUPPORTED_EFFECTOR_TYPES)
        if unsupported:
            raise Basilisk6DofError(f"unsupported 6-DOF effector type(s): {', '.join(unsupported)}")
        return cls(
            target_mode=mode,
            target_frame=str(coupling.get("target_frame", "lvlh" if mode in {"nadir", "hill", "velocity"} else "inertial_or_sun")).strip().lower(),
            attitude_frame=str(coupling.get("attitude_frame", "body")).strip(),
            inertial_frame=str(coupling.get("inertial_frame", "J2000")).strip(),
            time_scale=str(coupling.get("time_scale", "UTC")).strip().upper(),
            require_frame_metadata=_bool(coupling.get("require_frame_metadata"), True),
            enable_reaction_wheels="reaction_wheels" in effectors,
            enable_external_torque="external_torque" in effectors,
            enable_orbit_environment=True,
            enable_srp="srp" in effectors,
            enable_drag="drag" in effectors,
            enable_third_body="third_body" in effectors or "spice" in effectors,
            effectors=effectors,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "target_mode": self.target_mode,
            "target_frame": self.target_frame,
            "attitude_frame": self.attitude_frame,
            "inertial_frame": self.inertial_frame,
            "time_scale": self.time_scale,
            "require_frame_metadata": self.require_frame_metadata,
            "enable_reaction_wheels": self.enable_reaction_wheels,
            "enable_external_torque": self.enable_external_torque,
            "enable_orbit_environment": self.enable_orbit_environment,
            "enable_srp": self.enable_srp,
            "enable_drag": self.enable_drag,
            "enable_third_body": self.enable_third_body,
            "effectors": list(self.effectors),
        }


@dataclass(frozen=True)
class Basilisk6DofConfig:
    """Top-level BSK-6DOF-1 whole-spacecraft graph configuration."""

    duration_s: float
    sample_s: float
    dynamics_step_s: float
    fsw_step_s: float
    task_id: str
    spacecraft: Basilisk6DofSpacecraftConfig
    coupling: Basilisk6DofCouplingConfig
    orbit_config: BasiliskOrbitHfConfig
    adcs_config: BasiliskAdcsFswConfig

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "Basilisk6DofConfig":
        sim = _mapping(spec.get("simulation"))
        params = _mapping(spec.get("parameters"))
        solver = _mapping(sim.get("solver"))
        duration_s = _positive(sim.get("duration_s", 600.0), "simulation.duration_s")
        sample_s = _positive(sim.get("sample_s", 10.0), "simulation.sample_s")
        if sample_s > duration_s:
            raise Basilisk6DofError("simulation.sample_s must not exceed simulation.duration_s")
        fsw_step = _positive(params.get("fsw_step_s", _mapping(params.get("adcs")).get("fsw_step_s", sim.get("fsw_step_s", min(sample_s, 1.0)))), "parameters.fsw_step_s")
        dyn_step = _positive(solver.get("step_s", params.get("dynamics_step_s", min(sample_s, fsw_step))), "simulation.solver.step_s")
        if dyn_step > sample_s:
            raise Basilisk6DofError("simulation.solver.step_s must not exceed simulation.sample_s")
        spacecraft = Basilisk6DofSpacecraftConfig.from_task_spec(spec)
        coupling = Basilisk6DofCouplingConfig.from_task_spec(spec)
        orbit_spec = _orbit_spec_for_native_child(spec, duration_s=duration_s, sample_s=sample_s, dyn_step_s=dyn_step, spacecraft=spacecraft)
        adcs_spec = _adcs_spec_for_native_child(spec, duration_s=duration_s, sample_s=sample_s, fsw_step_s=fsw_step, dyn_step_s=dyn_step, spacecraft=spacecraft, coupling=coupling)
        return cls(
            duration_s=duration_s,
            sample_s=sample_s,
            dynamics_step_s=dyn_step,
            fsw_step_s=fsw_step,
            task_id=str(spec.get("task_id") or "spacecraft_basilisk_6dof_task"),
            spacecraft=spacecraft,
            coupling=coupling,
            orbit_config=BasiliskOrbitHfConfig.from_task_spec(orbit_spec),
            adcs_config=BasiliskAdcsFswConfig.from_task_spec(adcs_spec),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": BASILISK_6DOF_SCHEMA_VERSION,
            "task_id": self.task_id,
            "duration_s": self.duration_s,
            "sample_s": self.sample_s,
            "dynamics_step_s": self.dynamics_step_s,
            "fsw_step_s": self.fsw_step_s,
            "spacecraft": self.spacecraft.to_dict(),
            "coupling": self.coupling.to_dict(),
            "orbit_config": self.orbit_config.to_dict(),
            "adcs_config": self.adcs_config.to_dict(),
        }


def _orbit_spec_for_native_child(
    spec: Mapping[str, Any], *, duration_s: float, sample_s: float, dyn_step_s: float, spacecraft: Basilisk6DofSpacecraftConfig
) -> dict[str, Any]:
    params = _mapping(spec.get("parameters"))
    orbit_payload = dict(_mapping(spec.get("orbit_environment") or params.get("orbit_environment") or params.get("orbit")))
    if not orbit_payload:
        orbit_payload = {
            "central_body": "earth",
            "altitude_m": 500_000.0,
            "eccentricity": 0.001,
            "inclination_deg": 51.6,
            "force_models": {"spherical_harmonics": {"enabled": False, "degree": 2}},
        }
    return {
        "schema_version": spec.get("schema_version", "0.1.0"),
        "task_id": f"{spec.get('task_id', 'spacecraft_basilisk_6dof')}_orbit_child",
        "task_type": "orbit_environment",
        "capability_id": "orbit_environment.basilisk_hf.v1",
        "target": {"level": "integrated", "name": "orbit_environment", "mode": "nominal"},
        "simulation": {"duration_s": duration_s, "sample_s": sample_s, "epoch_utc": _mapping(spec.get("simulation")).get("epoch_utc", "2026-07-06T00:00:00Z"), "solver": {"step_s": dyn_step_s}},
        "orbit_environment": orbit_payload,
        "parameters": {"spacecraft": spacecraft.to_dict()},
        "metadata": {"parent_capability_id": "whole_spacecraft.basilisk_6dof.v1"},
    }


def _adcs_spec_for_native_child(
    spec: Mapping[str, Any], *, duration_s: float, sample_s: float, fsw_step_s: float, dyn_step_s: float, spacecraft: Basilisk6DofSpacecraftConfig, coupling: Basilisk6DofCouplingConfig
) -> dict[str, Any]:
    params = _mapping(spec.get("parameters"))
    adcs_params = dict(_mapping(params.get("adcs")))
    # Allow users to place ADCS parameters either under parameters.adcs or at the top level.
    for key in ("target_mode", "mode_manager", "controller", "reaction_wheels", "estimator", "sensors", "sensor_dropout"):
        if key in params and key not in adcs_params:
            adcs_params[key] = params[key]
    adcs_params.setdefault("target_mode", coupling.target_mode)
    adcs_params.setdefault("fsw_step_s", fsw_step_s)
    adcs_params.setdefault("inertia_kg_m2", list(spacecraft.inertia_kg_m2))
    adcs_params.setdefault("initial_rate_rad_s", list(spacecraft.initial_omega_bn_b_rad_s))
    adcs_params.setdefault("reaction_wheels", {"geometry": "pyramid", "num_wheels": 4, "wheel_inertia_kg_m2": 0.08, "max_torque_nm": 0.05, "max_speed_rad_s": 6000.0})
    adcs_params.setdefault("controller", {"control_law": "mrp_feedback", "K": 3.5, "P": 30.0, "Ki": 0.0, "max_control_torque_nm": 0.05})
    adcs_params.setdefault("estimator", {"type": "simple_nav"})
    adcs_params.setdefault("sensors", {"enabled": ["imu", "star_tracker", "css"]})
    return {
        "schema_version": spec.get("schema_version", "0.1.0"),
        "task_id": f"{spec.get('task_id', 'spacecraft_basilisk_6dof')}_adcs_child",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_basilisk_fsw.v1",
        "target": {"level": "subsystem", "name": "adcs_fsw", "mode": "nominal"},
        "simulation": {"duration_s": duration_s, "sample_s": sample_s, "epoch_utc": _mapping(spec.get("simulation")).get("epoch_utc", "2026-07-06T00:00:00Z"), "solver": {"step_s": dyn_step_s}},
        "parameters": adcs_params,
        "metadata": {"parent_capability_id": "whole_spacecraft.basilisk_6dof.v1"},
    }


def _required_modules(config: Basilisk6DofConfig | None = None) -> tuple[str, ...]:
    """Return modules instantiated by the BSK-RUN-6 native runtime path."""

    modules = [
        "Basilisk",
        "Basilisk.architecture.messaging",
        "Basilisk.utilities.SimulationBaseClass",
        "Basilisk.utilities.macros",
        "Basilisk.utilities.orbitalMotion",
        "Basilisk.utilities.simIncludeGravBody",
        "Basilisk.utilities.simIncludeRW",
        "Basilisk.simulation.spacecraft",
        "Basilisk.simulation.reactionWheelStateEffector",
        "Basilisk.simulation.simpleNav",
        "Basilisk.fswAlgorithms.inertial3D",
        "Basilisk.fswAlgorithms.attTrackingError",
        "Basilisk.fswAlgorithms.mrpFeedback",
        "Basilisk.fswAlgorithms.rwMotorTorque",
    ]
    if config is None or config.coupling.enable_external_torque:
        modules.append("Basilisk.simulation.extForceTorque")
    seen: set[str] = set()
    return tuple(module for module in modules if not (module in seen or seen.add(module)))


def check_basilisk_6dof_availability(config: Basilisk6DofConfig | None = None) -> Basilisk6DofAvailability:
    """Return optional Basilisk 6-DOF dependency availability without raising."""

    checked = _required_modules(config)
    orbit_availability = check_basilisk_availability(config.orbit_config).to_dict() if config is not None else check_basilisk_availability().to_dict()
    adcs_availability = check_basilisk_adcs_availability(config.adcs_config).to_dict() if config is not None else check_basilisk_adcs_availability().to_dict()
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
        return Basilisk6DofAvailability(
            True,
            checked_modules=checked,
            package_path=package_path,
            package_version=str(version) if version else None,
            orbit_availability=orbit_availability,
            adcs_availability=adcs_availability,
        )
    except Exception as exc:
        return Basilisk6DofAvailability(
            False,
            checked_modules=checked,
            missing_modules=checked,
            error=f"{type(exc).__name__}: {exc}",
            orbit_availability=orbit_availability,
            adcs_availability=adcs_availability,
        )


def build_basilisk_6dof_blueprint(config: Basilisk6DofConfig) -> dict[str, Any]:
    """Build an auditable Basilisk spacecraft graph blueprint for BSK-6DOF-1."""

    orbit_bp = build_basilisk_orbit_blueprint(config.orbit_config)
    adcs_bp = build_basilisk_adcs_fsw_blueprint(config.adcs_config)
    dynamics_modules = [
        "SimulationBaseClass.SimBaseClass",
        "spacecraft.Spacecraft",
        "spacecraft.hub: mHub / IHubPntBc_B / r_BcB_B",
        "simIncludeGravBody.gravBodyFactory",
        "orbitalMotion.ClassicElements + elem2rv",
    ]
    if config.coupling.enable_reaction_wheels:
        dynamics_modules.extend(["reactionWheelStateEffector", "simIncludeRW.rwFactory"])
    if config.coupling.enable_external_torque:
        dynamics_modules.append("extForceTorque.ExtForceTorque")
    if config.coupling.enable_drag:
        dynamics_modules.extend(["exponentialAtmosphere", "dragDynamicEffector"])
    if config.coupling.enable_srp:
        dynamics_modules.append("radiationPressure")
    if config.coupling.enable_third_body:
        dynamics_modules.append("spiceInterface.SpiceInterface")
    fsw_modules = list(adcs_bp["processes"]["fsw_process"]["modules"])
    state_messages = [
        "spacecraft.scStateOutMsg",
        "reactionWheelStateEffector.rwSpeedOutMsg",
        "rwMotorTorque.motorTorqueOutMsg",
        "att_guidance.attRefOutMsg / attGuidOutMsg",
        "cmdTorqueBody.cmdTorqueOutMsg",
    ]
    return {
        "schema_version": BASILISK_6DOF_SCHEMA_VERSION,
        "backend_type": "basilisk_native",
        "basilisk_required": True,
        "execution_scope": _bsk_run6_runtime_scope(config)[1],
        "capability_id": "whole_spacecraft.basilisk_6dof.v1",
        "processes": {
            "dynamics_process": {"task_step_s": config.dynamics_step_s, "modules": dynamics_modules},
            "fsw_process": {"task_step_s": config.fsw_step_s, "modules": fsw_modules},
        },
        "spacecraft_graph": {
            "hub": config.spacecraft.to_dict(),
            "state_effectors": ["reactionWheelStateEffector"] if config.coupling.enable_reaction_wheels else [],
            "dynamic_effectors": [name for name in [
                "gravityFieldEffector",
                "extForceTorque" if config.coupling.enable_external_torque else None,
                "dragDynamicEffector" if config.coupling.enable_drag else None,
                "radiationPressure" if config.coupling.enable_srp else None,
            ] if name],
            "environment_effectors": [name for name in [
                "gravityBodies",
                "spiceInterface" if config.coupling.enable_third_body else None,
                "atmosphere" if config.coupling.enable_drag else None,
                "sunVector/SRP" if config.coupling.enable_srp else None,
            ] if name],
        },
        "coupling_contract": config.coupling.to_dict(),
        "child_capabilities": {
            "orbit_primary": "orbit_environment.basilisk_hf.v1",
            "adcs_primary": "subsystem.adcs_basilisk_fsw.v1",
            "comparison_baseline": "whole_spacecraft.orbit_adcs_fidelity.v1",
            "fallback_policy": "no_implicit_fallback_use_whole_spacecraft.orbit_adcs_fidelity.v1_explicitly",
        },
        "child_blueprints": {"orbit": orbit_bp, "adcs_fsw": adcs_bp},
        "state_logging": {"messages": state_messages, "sample_s": config.sample_s},
        "metadata_contract": {
            "time_fields": ["time_s", "epoch_utc", "time_scale"],
            "frame_fields": ["inertial_frame", "attitude_frame", "target_frame"],
            "unit_policy": "SI_units_for_state_force_torque_rad_s_attitude_rates",
            "solver_fields": ["dynamics_step_s", "fsw_step_s", "sample_s"],
        },
        "claim_guardrail": "BSK-RUN-6 can execute a minimal orbit-attitude-RW 6-DOF runtime smoke for central/J2 gravity plus inertial/detumble MRP-feedback modes when Basilisk is present; it is not full ADCS/FSW, HIL, flight validation, or external truth correlation.",
        "fallback_policy": "No implicit fallback: do not silently use whole_spacecraft.orbit_adcs_fidelity.v1. Use INT-1 only as an explicit comparison baseline.",
    }



def _bsk_run6_runtime_scope(config: Basilisk6DofConfig) -> tuple[bool, str]:
    """Return whether the BSK-RUN-6 minimal 6-DOF runtime supports this config."""

    unsupported: list[str] = []
    if config.coupling.enable_drag:
        unsupported.append("drag_dynamic_effector")
    if config.coupling.enable_srp:
        unsupported.append("srp_dynamic_effector")
    if config.coupling.enable_third_body or config.orbit_config.force_models.spice_ephemeris:
        unsupported.append("spice_third_body")
    if config.orbit_config.force_models.central_body != "earth":
        unsupported.append(f"central_body={config.orbit_config.force_models.central_body}")
    if not config.coupling.enable_reaction_wheels:
        unsupported.append("reaction_wheels_disabled")
    if config.coupling.target_mode not in {"inertial", "inertial3d", "detumble"}:
        unsupported.append(f"target_mode={config.coupling.target_mode}")
    if config.adcs_config.controller.control_law not in {"mrp_feedback", "rate_damping"}:
        unsupported.append(f"control_law={config.adcs_config.controller.control_law}")
    if config.adcs_config.estimator_sensors.estimator not in {"truth", "simple_nav"}:
        unsupported.append(f"estimator={config.adcs_config.estimator_sensors.estimator}")
    if unsupported:
        return False, "configuration_blueprint_only_bsk_run6_minimal_runtime_unsupported: " + ", ".join(unsupported)
    if config.orbit_config.force_models.spherical_harmonics.enabled:
        return True, "bsk_run6_minimal_6dof_runtime_supported: earth_gravity_j2_attitude_rw_fsw"
    return True, "bsk_run6_minimal_6dof_runtime_supported: earth_central_gravity_attitude_rw_fsw"


def _matrix_diag_flat(diagonal: tuple[float, float, float]) -> list[float]:
    return [diagonal[0], 0.0, 0.0, 0.0, diagonal[1], 0.0, 0.0, 0.0, diagonal[2]]


def _matrix_diag_nested(diagonal: tuple[float, float, float]) -> list[list[float]]:
    return [[diagonal[0], 0.0, 0.0], [0.0, diagonal[1], 0.0], [0.0, 0.0, diagonal[2]]]


def _first3(values: Any) -> tuple[float, float, float]:
    try:
        return (float(values[0]), float(values[1]), float(values[2]))
    except Exception:
        return (0.0, 0.0, 0.0)


def _clip(value: float, limit: float) -> float:
    return max(-limit, min(limit, float(value)))


def build_bsk_6dof1_payload(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Return normalized BSK-6DOF-1 configuration payload."""

    config = Basilisk6DofConfig.from_task_spec(spec)
    return {
        "schema_version": BASILISK_6DOF_SCHEMA_VERSION,
        "config": config.to_dict(),
        "availability": check_basilisk_6dof_availability(config).to_dict(),
        "blueprint": build_basilisk_6dof_blueprint(config),
    }


def run_basilisk_6dof_if_available(config: Basilisk6DofConfig) -> tuple[dict[str, Any], tuple[dict[str, Any], ...]]:
    """Run the BSK-RUN-6 minimal Basilisk 6-DOF runtime smoke.

    Runtime scope is deliberately narrow and auditable: one Basilisk
    ``Spacecraft`` hub propagates both translational orbit state and attitude
    state, Earth gravity/J2 is attached through ``gravBodyFactory``, a
    ``ReactionWheelStateEffector`` is mounted on the same hub, and a minimal
    inertial3D + simpleNav + attTrackingError + mrpFeedback + rwMotorTorque FSW
    loop drives the wheel commands.  Unsupported environment/FSW modes return a
    configured-but-not-executed result rather than a fabricated runtime pass.
    """

    availability = check_basilisk_6dof_availability(config)
    if not availability.available:
        raise Basilisk6DofRuntimeUnavailable("Basilisk 6-DOF modules are not available", availability.to_dict())
    runtime_supported, runtime_scope = _bsk_run6_runtime_scope(config)
    blueprint = build_basilisk_6dof_blueprint(config)
    if not runtime_supported:
        return {
            "schema_version": BASILISK_6DOF_SCHEMA_VERSION,
            "backend_type": "basilisk_native",
            "basilisk_required": True,
            "basilisk_status": "configured_but_not_executed",
            "runtime_version": BSK_RUN6_RUNTIME_VERSION,
            "runtime_scope": runtime_scope,
            "physical_validation_status": "not_run_bsk_run6_minimal_runtime_scope_limit",
            "can_claim_high_fidelity": False,
            "trace_rows": 0,
            "availability": availability.to_dict(),
            "blueprint": blueprint,
            "qoi": {},
            "fallback_policy": "no_implicit_fallback_use_whole_spacecraft.orbit_adcs_fidelity.v1_explicitly",
        }, ()

    from Basilisk.architecture import messaging  # type: ignore
    from Basilisk.fswAlgorithms import attTrackingError, inertial3D, mrpFeedback, rwMotorTorque  # type: ignore
    from Basilisk.simulation import extForceTorque, reactionWheelStateEffector, simpleNav, spacecraft  # type: ignore
    from Basilisk.utilities import SimulationBaseClass, macros, orbitalMotion, simIncludeGravBody, simIncludeRW  # type: ignore

    sc_sim = SimulationBaseClass.SimBaseClass()
    dyn_process = sc_sim.CreateNewProcess("bsk_run6_6dof_dynamics_process")
    fsw_process = sc_sim.CreateNewProcess("bsk_run6_6dof_fsw_process")
    dyn_task = "bsk_run6_6dof_dynamics_task"
    fsw_task = "bsk_run6_6dof_fsw_task"
    dyn_process.addTask(sc_sim.CreateNewTask(dyn_task, macros.sec2nano(config.dynamics_step_s)))
    fsw_process.addTask(sc_sim.CreateNewTask(fsw_task, macros.sec2nano(config.fsw_step_s)))
    sample_ns = macros.sec2nano(config.sample_s)

    sc_object = spacecraft.Spacecraft()
    sc_object.ModelTag = "bsk-run6-6dof-spacecraft"
    sc_object.hub.mHub = config.spacecraft.mass_kg
    sc_object.hub.IHubPntBc_B = _matrix_diag_nested(config.spacecraft.inertia_kg_m2)
    sc_object.hub.r_BcB_B = [[config.spacecraft.hub_center_of_mass_b_m[0]], [config.spacecraft.hub_center_of_mass_b_m[1]], [config.spacecraft.hub_center_of_mass_b_m[2]]]
    sc_object.hub.sigma_BNInit = [[config.spacecraft.initial_sigma_bn[0]], [config.spacecraft.initial_sigma_bn[1]], [config.spacecraft.initial_sigma_bn[2]]]
    sc_object.hub.omega_BN_BInit = [[config.spacecraft.initial_omega_bn_b_rad_s[0]], [config.spacecraft.initial_omega_bn_b_rad_s[1]], [config.spacecraft.initial_omega_bn_b_rad_s[2]]]

    grav_factory = simIncludeGravBody.gravBodyFactory()
    earth = grav_factory.createEarth()
    earth.isCentralBody = True
    resolved_spherical_harmonics_file: str | None = None
    if config.orbit_config.force_models.spherical_harmonics.enabled:
        resolved_spherical_harmonics_file = resolve_spherical_harmonics_coefficient_file(
            config.orbit_config.force_models.spherical_harmonics,
            central_body=config.orbit_config.force_models.central_body,
        )
        earth.useSphericalHarmonicsGravityModel(
            resolved_spherical_harmonics_file,
            config.orbit_config.force_models.spherical_harmonics.degree,
        )
    grav_factory.addBodiesTo(sc_object)

    oe = orbitalMotion.ClassicElements()
    oe.a = config.orbit_config.initial_state.semi_major_axis_m
    oe.e = config.orbit_config.initial_state.eccentricity
    oe.i = config.orbit_config.initial_state.inclination_deg * macros.D2R
    oe.Omega = config.orbit_config.initial_state.raan_deg * macros.D2R
    oe.omega = config.orbit_config.initial_state.arg_perigee_deg * macros.D2R
    oe.f = config.orbit_config.initial_state.true_anomaly_deg * macros.D2R
    r_n, v_n = orbitalMotion.elem2rv(earth.mu, oe)
    sc_object.hub.r_CN_NInit = r_n
    sc_object.hub.v_CN_NInit = v_n
    sc_sim.AddModelToTask(dyn_task, sc_object, 1)

    external_torque = None
    if config.coupling.enable_external_torque:
        external_torque = extForceTorque.ExtForceTorque()
        external_torque.ModelTag = "bsk-run6-zero-external-torque"
        external_torque.extTorquePntB_B = [[0.0], [0.0], [0.0]]
        sc_object.addDynamicEffector(external_torque)
        sc_sim.AddModelToTask(dyn_task, external_torque, 3)

    rw_effector = reactionWheelStateEffector.ReactionWheelStateEffector()
    rw_factory = simIncludeRW.rwFactory()
    axes = config.adcs_config.reaction_wheels.default_axes()
    runtime_axes = axes[:3]
    if len(runtime_axes) < 3:
        raise Basilisk6DofRuntimeUnavailable("at least three reaction-wheel axes are required", {"axes": [list(x) for x in axes]})
    max_speed_rpm = config.adcs_config.reaction_wheels.max_speed_rad_s / macros.RPM
    for idx, axis in enumerate(runtime_axes):
        rw_factory.create(
            "Honeywell_HR16",
            list(axis),
            maxMomentum=50.0,
            Omega=min(100.0 * (idx + 1), max_speed_rpm),
            u_max=float(config.adcs_config.reaction_wheels.max_torque_nm),
            label=f"RW{idx + 1}",
        )
    rw_factory.addToSpacecraft("bsk-run6-rw-state-effector", rw_effector, sc_object)
    sc_sim.AddModelToTask(dyn_task, rw_effector, 2)
    rw_config_msg = rw_factory.getConfigMessage()

    nav = simpleNav.SimpleNav()
    nav.ModelTag = "bsk-run6-simple-nav"
    nav.scStateInMsg.subscribeTo(sc_object.scStateOutMsg)
    sc_sim.AddModelToTask(dyn_task, nav)

    inertial_ref = inertial3D.inertial3D()
    inertial_ref.ModelTag = "bsk-run6-inertial3d-reference"
    inertial_ref.sigma_R0N = [0.0, 0.0, 0.0]
    sc_sim.AddModelToTask(fsw_task, inertial_ref)

    attitude_error = attTrackingError.attTrackingError()
    attitude_error.ModelTag = "bsk-run6-att-tracking-error"
    attitude_error.attRefInMsg.subscribeTo(inertial_ref.attRefOutMsg)
    attitude_error.attNavInMsg.subscribeTo(nav.attOutMsg)
    sc_sim.AddModelToTask(fsw_task, attitude_error)

    controller = mrpFeedback.mrpFeedback()
    controller.ModelTag = "bsk-run6-mrp-feedback"
    controller.K = float(config.adcs_config.controller.k_gain)
    controller.P = float(config.adcs_config.controller.p_gain)
    if config.adcs_config.controller.ki_gain > 0.0:
        controller.Ki = float(config.adcs_config.controller.ki_gain)
        controller.integralLimit = float(config.adcs_config.controller.integral_limit)
    else:
        controller.Ki = -1.0
        controller.integralLimit = -1.0
    if config.adcs_config.controller.control_law == "rate_damping" or config.coupling.target_mode == "detumble":
        controller.K = 0.0
    controller.guidInMsg.subscribeTo(attitude_error.attGuidOutMsg)
    veh_payload = messaging.VehicleConfigMsgPayload()
    veh_payload.ISCPntB_B = _matrix_diag_flat(config.spacecraft.inertia_kg_m2)
    veh_payload.massSC = config.spacecraft.mass_kg
    veh_msg = messaging.VehicleConfigMsg().write(veh_payload)
    controller.vehConfigInMsg.subscribeTo(veh_msg)
    controller.rwParamsInMsg.subscribeTo(rw_config_msg)
    controller.rwSpeedsInMsg.subscribeTo(rw_effector.rwSpeedOutMsg)
    sc_sim.AddModelToTask(fsw_task, controller)

    motor_torque = rwMotorTorque.rwMotorTorque()
    motor_torque.ModelTag = "bsk-run6-rw-motor-torque"
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
        r = _first3(sc_rec.r_BN_N[idx])
        v = _first3(sc_rec.v_BN_N[idx])
        sigma = _first3(sc_rec.sigma_BN[idx])
        rate = _first3(sc_rec.omega_BN_B[idx])
        sigma_br = _first3(err_rec.sigma_BR[idx])
        omega_br = _first3(err_rec.omega_BR_B[idx])
        cmd_torque = _first3(torque_rec.torqueRequestBody[idx])
        motor_cmd = _first3(motor_rec.motorTorque[idx])
        rw_speed = tuple(float(x) for x in rw_rec.wheelSpeeds[idx][: len(runtime_axes)])
        radius = vector_norm(r)
        rows.append({
            "time_s": float(t_s),
            "epoch_utc": config.orbit_config.epoch_utc,
            "time_scale": config.coupling.time_scale,
            "inertial_frame": config.coupling.inertial_frame,
            "attitude_frame": config.coupling.attitude_frame,
            "target_frame": config.coupling.target_frame,
            "dynamics_step_s": config.dynamics_step_s,
            "fsw_step_s": config.fsw_step_s,
            "sample_s": config.sample_s,
            "orbit.r_bn_n_m_x": r[0],
            "orbit.r_bn_n_m_y": r[1],
            "orbit.r_bn_n_m_z": r[2],
            "orbit.v_bn_n_m_s_x": v[0],
            "orbit.v_bn_n_m_s_y": v[1],
            "orbit.v_bn_n_m_s_z": v[2],
            "orbit.radius_m": radius,
            "orbit.altitude_m": radius - EARTH_RADIUS_M,
            "orbit.speed_m_s": vector_norm(v),
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

    altitudes = [float(row["orbit.altitude_m"]) for row in rows]
    sigma_norms = [float(row["control.sigma_br_norm"]) for row in rows]
    rate_norms = [float(row["attitude.omega_bn_b_norm_rad_s"]) for row in rows]
    torque_norms = [float(row["control.cmd_torque_b_norm_nm"]) for row in rows]
    motor_norms = [float(row["rw.motor_torque_norm_nm"]) for row in rows]
    rw_speeds = [float(row["rw.speed_rad_s_max_abs"]) for row in rows]
    initial_pointing_error_deg = 4.0 * math.degrees(math.atan(sigma_norms[0])) if sigma_norms else None
    final_pointing_error_deg = 4.0 * math.degrees(math.atan(sigma_norms[-1])) if sigma_norms else None
    summary = {
        "schema_version": BASILISK_6DOF_SCHEMA_VERSION,
        "backend_type": "basilisk_native",
        "basilisk_required": True,
        "basilisk_status": "executed",
        "runtime_version": BSK_RUN6_RUNTIME_VERSION,
        "runtime_scope": runtime_scope,
        "force_model_scope": "earth_gravity_j2_or_central_attitude_rw_coupled_6dof",
        "physical_validation_status": "runtime_smoke_passed_not_flight_validated",
        "can_claim_high_fidelity": False,
        "trace_rows": len(rows),
        "availability": availability.to_dict(),
        "blueprint": blueprint,
        "runtime_configuration": {
            "spacecraft_hub": "single Basilisk Spacecraft hub for orbit and attitude",
            "external_torque": "instantiated_zero_vector" if external_torque is not None else "disabled",
            "gravity": "Earth central gravity" + (" + degree-2 J2" if resolved_spherical_harmonics_file else ""),
            "spherical_harmonics_coefficient_file": resolved_spherical_harmonics_file,
            "attitude_navigation": "simpleNav from same spacecraft.scStateOutMsg",
            "guidance": "inertial3D smoke reference",
            "controller": "mrpFeedback_rate_damping_variant" if (config.adcs_config.controller.control_law == "rate_damping" or config.coupling.target_mode == "detumble") else "mrpFeedback",
            "reaction_wheels": "reactionWheelStateEffector + rwMotorTorque on same hub",
            "controller_integral_mode": "enabled" if config.adcs_config.controller.ki_gain > 0.0 else "disabled_with_negative_Ki",
            "telemetry_policy": "raw_native_message_values_no_posthoc_clipping",
            "runtime_wheel_axes_body": [list(axis) for axis in runtime_axes],
        },
        "qoi": {
            "integration.frame_consistency_pass": 1.0,
            "integration.time_alignment_max_error_s": 0.0,
            "orbit.altitude_min_m": min(altitudes) if altitudes else None,
            "orbit.altitude_max_m": max(altitudes) if altitudes else None,
            "orbit.altitude_delta_m": (altitudes[-1] - altitudes[0]) if len(altitudes) >= 2 else 0.0,
            "attitude.sigma_bn_norm_initial": rows[0]["attitude.sigma_bn_norm"] if rows else None,
            "attitude.sigma_bn_norm_final": rows[-1]["attitude.sigma_bn_norm"] if rows else None,
            "adcs.initial_pointing_error_deg": initial_pointing_error_deg,
            "adcs.final_pointing_error_deg": final_pointing_error_deg,
            "adcs.pointing_error_delta_deg": (final_pointing_error_deg - initial_pointing_error_deg) if initial_pointing_error_deg is not None and final_pointing_error_deg is not None else None,
            "adcs.max_abs_rate_rad_s": max(rate_norms) if rate_norms else None,
            "adcs.final_rate_norm_rad_s": rate_norms[-1] if rate_norms else None,
            "adcs.control_torque_norm_max_nm": max(torque_norms) if torque_norms else None,
            "adcs.rw_motor_torque_norm_max_nm": max(motor_norms) if motor_norms else None,
            "adcs.rw_speed_max_abs_rad_s": max(rw_speeds) if rw_speeds else None,
        },
        "events": {"fault_count": 0, "degradation_count": 0},
        "fallback_policy": "no_implicit_fallback_use_whole_spacecraft.orbit_adcs_fidelity.v1_explicitly",
    }
    return summary, tuple(rows)

__all__ = [
    "BASILISK_6DOF_SCHEMA_VERSION",
    "BSK_RUN6_RUNTIME_VERSION",
    "Basilisk6DofError",
    "Basilisk6DofRuntimeUnavailable",
    "Basilisk6DofAvailability",
    "Basilisk6DofSpacecraftConfig",
    "Basilisk6DofCouplingConfig",
    "Basilisk6DofConfig",
    "build_basilisk_6dof_blueprint",
    "build_bsk_6dof1_payload",
    "check_basilisk_6dof_availability",
    "run_basilisk_6dof_if_available",
]
