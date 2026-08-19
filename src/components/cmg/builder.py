"""CMG builder module.

Provides configuration builders, Python model classes, and Basilisk-native
VSCMG state effector factories for Control Moment Gyroscopes.

Usage:
    cfg = build_nominal_cmg_config()
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import SingleGimbalCmgConfig, CmgConfig, VscmgSpec
from .faults import apply_cmg_faults
from .faults import FaultSpec

from dataclasses import dataclass, field
from typing import Any, Iterable

from ..dynamic_models import v3, cross, unit, dot, cos, sin


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
        location='src/components/cmg/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class SingleGimbalCmgState:
    wheel_speed_rad_s: float
    gimbal_angle_rad: float


@dataclass(frozen=True)
class CmgPhysicsResult:
    torque_nm: tuple
    spin_axis_b: tuple
    transverse_axis_b: tuple
    wheel_momentum_nms: float
    gimbal_rate_rad_s: float
    guard_tripped: bool = False
    guard_reason: str | None = None


@dataclass(frozen=True)
class CmgProfileResult:
    time_s: tuple
    gimbal_angle_rad: tuple
    wheel_speed_rad_s: tuple
    torque_nm: tuple




@dataclass(frozen=True)
class CmgCommandResult:
    wheel_torque_nm: tuple
    gimbal_torque_nm: tuple
    guard_tripped: bool
    guard_reason: str | None = None


@dataclass(frozen=True)
class CmgArrayGeometry:
    configs: tuple = ()


def rotate_about_axis(vec, axis, ang):
    v = v3(vec)
    k = unit(axis)
    kxv = cross(k, v)
    kd = dot(k, v)
    return tuple(
        v[i] * cos(ang) + kxv[i] * sin(ang) + k[i] * kd * (1 - cos(ang))
        for i in range(3)
    )


def spin_axis_b(c, gamma_rad=None):
    return unit(
        rotate_about_axis(
            c.spin_axis_g0_b,
            c.gimbal_axis_b,
            c.gimbal_angle_rad if gamma_rad is None else gamma_rad,
        )
    )


def transverse_axis_b(c, gamma_rad=None):
    return unit(cross(unit(c.gimbal_axis_b), spin_axis_b(c, gamma_rad)))


def wheel_momentum_nms(c, wheel_speed_rad_s=None):
    return c.wheel_inertia_kg_m2 * (
        c.wheel_speed_rad_s if wheel_speed_rad_s is None else wheel_speed_rad_s
    )


def compute_single_cmg_gyro_torque(
    c, gimbal_rate_rad_s, wheel_speed_rad_s=None, gamma_rad=None
):
    rate = (
        gimbal_rate_rad_s
        if c.gimbal_rate_limit_rad_s is None
        else max(
            -c.gimbal_rate_limit_rad_s,
            min(c.gimbal_rate_limit_rad_s, gimbal_rate_rad_s),
        )
    )
    omega = c.wheel_speed_rad_s if wheel_speed_rad_s is None else wheel_speed_rad_s
    h = wheel_momentum_nms(c, omega)
    if (
        c.wheel_speed_limit_rad_s is not None
        and abs(omega) > c.wheel_speed_limit_rad_s
    ):
        return CmgPhysicsResult(
            (0, 0, 0),
            spin_axis_b(c, gamma_rad),
            transverse_axis_b(c, gamma_rad),
            h,
            rate,
            True,
            "wheel_speed_overlimit",
        )
    gt = transverse_axis_b(c, gamma_rad)
    return CmgPhysicsResult(
        tuple(-h * rate * x for x in gt),
        spin_axis_b(c, gamma_rad),
        gt,
        h,
        rate,
    )


def propagate_single_cmg_state(s, c, gimbal_rate_cmd_rad_s, wheel_torque_nm, dt_s):
    rate = (
        max(
            -c.gimbal_rate_limit_rad_s,
            min(c.gimbal_rate_limit_rad_s, gimbal_rate_cmd_rad_s),
        )
        if c.gimbal_rate_limit_rad_s is not None
        else gimbal_rate_cmd_rad_s
    )
    omega = s.wheel_speed_rad_s + wheel_torque_nm / max(c.wheel_inertia_kg_m2, 1e-12) * dt_s
    if c.wheel_speed_limit_rad_s is not None:
        omega = max(-c.wheel_speed_limit_rad_s, min(c.wheel_speed_limit_rad_s, omega))
    return SingleGimbalCmgState(omega, s.gimbal_angle_rad + rate * dt_s)


def simulate_gimbal_rate_profile(s, c, rates, wheel_torques, dt_s):
    if dt_s <= 0:
        raise ValueError("dt_s")
    t = [0.0]
    g = [s.gimbal_angle_rad]
    w = [s.wheel_speed_rad_s]
    tq = []
    for i in range(max(len(rates), len(wheel_torques))):
        rate = rates[i] if i < len(rates) else 0
        wt = wheel_torques[i] if i < len(wheel_torques) else 0
        tq.append(
            compute_single_cmg_gyro_torque(
                c, rate, s.wheel_speed_rad_s, s.gimbal_angle_rad
            ).torque_nm
        )
        s = propagate_single_cmg_state(s, c, rate, wt, dt_s)
        t.append((i + 1) * dt_s)
        g.append(s.gimbal_angle_rad)
        w.append(s.wheel_speed_rad_s)
    return CmgProfileResult(tuple(t), tuple(g), tuple(w), tuple(tq))


def shape_cmg_command(w, g, c, *_, **__):
    return CmgCommandResult(
        tuple(w[: c.num_cmgs]), tuple(g[: c.num_cmgs]), False, None
    )


def default_pyramid_geometry(momentum_nms=15.0):
    return CmgArrayGeometry(())


def steering_matrix(*a, **k):
    return []


def steering_rank_and_condition(*a, **k):
    return (0, float("inf"))


def _build_nominal_cmg_config_base_impl(wheel_inertia_kg_m2: float = 1.0, wheel_speed_rad_s: float = 10.0, gimbal_rate_limit_rad_s: float = 1.0, wheel_speed_limit_rad_s: float = 100.0, fault_specs: list[FaultSpec] | None = None) -> SingleGimbalCmgConfig:
    """Build a nominal SingleGimbalCmgConfig with default values.

    Args:
        wheel_inertia_kg_m2: Wheel inertia in kg·m²
        wheel_speed_rad_s: Wheel speed in rad/s
        gimbal_rate_limit_rad_s: Gimbal rate limit in rad/s
        wheel_speed_limit_rad_s: Wheel speed limit in rad/s

    Returns:
        SingleGimbalCmgConfig: Nominal CMG configuration
    """
    return SingleGimbalCmgConfig(
        wheel_inertia_kg_m2=wheel_inertia_kg_m2,
        wheel_speed_rad_s=wheel_speed_rad_s,
        gimbal_rate_limit_rad_s=gimbal_rate_limit_rad_s,
        wheel_speed_limit_rad_s=wheel_speed_limit_rad_s,
    )


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def require_basilisk() -> None:
    """Raise RuntimeError if Basilisk modules are unavailable."""
    if not basilisk_available():
        raise RuntimeError("Basilisk messaging/sysModel modules are unavailable")




@dataclass
class VscmgBundle:
    """Container for Basilisk VSCMG state effector and config message."""

    effector: Any
    config_msg: Any
    num_cmgs: int
    specs: tuple[VscmgSpec, ...] = ()



def _vscmg_payload_from_spec(spec: VscmgSpec, messaging_module: Any | None = None) -> Any:
    """Translate ``VscmgSpec`` to Basilisk ``VSCMGConfigMsgPayload``.

    Kept in the component builder so subsystems and whole-spacecraft assembly
    never duplicate native field mapping.
    """
    if messaging_module is None:
        require_basilisk_vscmg()
        from Basilisk.architecture import messaging as messaging_module

    def _vec3(name: str, value: tuple[float, float, float]) -> list[float]:
        if len(value) != 3:
            raise ValueError(f"VscmgSpec.{name} must have 3 elements")
        return [float(x) for x in value]

    def _nonneg(name: str, value: float) -> float:
        value = float(value)
        if value < 0.0:
            raise ValueError(f"VscmgSpec.{name} must be non-negative")
        return value

    payload = messaging_module.VSCMGConfigMsgPayload()
    gs_hat = _vec3("sg_hat_b", spec.sg_hat_b)
    gt_hat = _vec3("gt_hat_b", spec.gt_hat_b)
    gg_hat = list(unit(cross(gs_hat, gt_hat)))

    payload.rGB_B = _vec3("gs_frame_gs_m", spec.gs_frame_gs_m)
    payload.gsHat_B = gs_hat
    payload.gsHat0_B = list(gs_hat)
    payload.gtHat_B = gt_hat
    payload.gtHat0_B = list(gt_hat)
    payload.ggHat_B = gg_hat
    payload.Omega = float(spec.w_b)
    payload.gamma = float(spec.gamma_rad)
    payload.gammaDot = float(spec.gamma_dot_rad_s)
    payload.U_s = float(spec.U_s)
    payload.U_d = float(spec.U_g)
    payload.Omega_max = _nonneg("max_wheel_speed_rad_s", spec.max_wheel_speed_rad_s)
    payload.gammaDot_max = _nonneg("max_gimbal_rate_rad_s", spec.max_gimbal_rate_rad_s)

    max_torque = _nonneg("max_torque_nm", spec.max_torque_nm)
    payload.u_s_max = _nonneg("u_s_max_nm", max_torque if spec.u_s_max_nm is None else spec.u_s_max_nm)
    payload.u_g_max = _nonneg("u_g_max_nm", max_torque if spec.u_g_max_nm is None else spec.u_g_max_nm)
    payload.u_s_min = _nonneg("u_s_min_nm", spec.u_s_min_nm)
    payload.u_g_min = _nonneg("u_g_min_nm", spec.u_g_min_nm)
    payload.u_s_f = _nonneg("u_s_f_nm", spec.u_s_f_nm)
    payload.u_g_f = _nonneg("u_g_f_nm", spec.u_g_f_nm)
    payload.wheelLinearFrictionRatio = _nonneg("wheel_linear_friction_ratio", spec.wheel_linear_friction_ratio)
    payload.gimbalLinearFrictionRatio = _nonneg("gimbal_linear_friction_ratio", spec.gimbal_linear_friction_ratio)

    payload.massV = _nonneg("massV", spec.massV)
    payload.massG = _nonneg("massG", spec.massG)
    payload.massW = _nonneg("massW", spec.massW)
    payload.IW1 = _nonneg("IW1", spec.Js if spec.IW1 is None else spec.IW1)
    payload.IW2 = _nonneg("IW2", spec.Jt if spec.IW2 is None else spec.IW2)
    payload.IW3 = _nonneg("IW3", spec.Jt if spec.IW3 is None else spec.IW3)
    payload.IW13 = float(spec.IW13)
    payload.IG1 = _nonneg("IG1", spec.Jg if spec.IG1 is None else spec.IG1)
    payload.IG2 = _nonneg("IG2", spec.Jg if spec.IG2 is None else spec.IG2)
    payload.IG3 = _nonneg("IG3", spec.Jg if spec.IG3 is None else spec.IG3)
    payload.IG12 = float(spec.IG12)
    payload.IG13 = float(spec.IG13)
    payload.IG23 = float(spec.IG23)
    payload.IV1 = _nonneg("IV1", spec.IV1)
    payload.IV2 = _nonneg("IV2", spec.IV2)
    payload.IV3 = _nonneg("IV3", spec.IV3)
    payload.rhoG = float(spec.rhoG)
    payload.rhoW = float(spec.rhoW)
    return payload

def require_basilisk_vscmg() -> None:
    try:
        from Basilisk.simulation import vscmgStateEffector  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk vscmgStateEffector is unavailable: {exc}") from exc


def build_vscmg_bundle(
    model_tag: str = "componentVscmgStateEffector",
    cmg_specs: Iterable[VscmgSpec] | None = None,
) -> VscmgBundle:
    """Create a Basilisk VSCMG state effector and config message.

    The returned effector must be added to a spacecraft via
    ``spacecraft.addStateEffector(bundle.effector)`` and registered
    on a simulation task.

    Parameters
    ----------
    model_tag : str
        Basilisk model name tag.
    cmg_specs : iterable of VscmgSpec, optional
        CMG unit specifications.  Defaults to a single unit.

    Returns
    -------
    VscmgBundle
        Bundle containing the effector, config message, and unit count.
    """
    require_basilisk_vscmg()
    from Basilisk.simulation import vscmgStateEffector
    from Basilisk.architecture import messaging

    specs = tuple(cmg_specs or (
        VscmgSpec(sg_hat_b=(1.0, 0.0, 0.0), gt_hat_b=(0.0, 1.0, 0.0)),
    ))
    effector = vscmgStateEffector.VSCMGStateEffector()
    effector.ModelTag = model_tag

    for spec in specs:
        cmg_data = _vscmg_payload_from_spec(spec, messaging)
        effector.VSCMGData.push_back(cmg_data)

    config_msg = effector.vscmgOutMsgs
    return VscmgBundle(effector=effector, config_msg=config_msg, num_cmgs=len(specs), specs=specs)


def attach_vscmg_to_spacecraft(bundle: VscmgBundle, spacecraft: object) -> None:
    """Attach a VSCMG effector bundle to a Basilisk spacecraft.

    Parameters
    ----------
    bundle : VscmgBundle
        Bundle containing the VSCMG effector.
    spacecraft : object
        Basilisk Spacecraft instance.
    """
    spacecraft.addStateEffector(bundle.effector)


def write_vscmg_array_torque_message(wheel_torques_nm: Iterable[float], gimbal_torques_nm: Iterable[float] | None = None) -> object:
    """Write a VSCMG array torque command message.

    Works around SWIG array-index assignment limitations by reading
    the payload list, modifying it, and writing it back.

    Parameters
    ----------
    wheel_torques_nm : iterable of float
        Wheel speed motor torque commands in N·m, one per CMG unit.
    gimbal_torques_nm : iterable of float, optional
        Gimbal motor torque commands in N·m, one per CMG unit.
        Defaults to zeros.

    Returns
    -------
    Msg
        Written message that can be subscribed to by
        ``VSCMGStateEffector.cmdsInMsg``.
    """
    require_basilisk_vscmg()
    from Basilisk.architecture import messaging

    payload = messaging.VSCMGArrayTorqueMsgPayload()
    wt = list(payload.wheelTorque)
    gt = list(payload.gimbalTorque) if gimbal_torques_nm is not None else [0.0] * len(wt)
    for idx, val in enumerate(wheel_torques_nm):
        if idx < len(wt):
            wt[idx] = float(val)
    if gimbal_torques_nm is not None:
        for idx, val in enumerate(gimbal_torques_nm):
            if idx < len(gt):
                gt[idx] = float(val)
    payload.wheelTorque = wt
    payload.gimbalTorque = gt
    return messaging.VSCMGArrayTorqueMsg().write(payload)

# Component fault/degradation compatibility wrappers
from .degradation import CMGDegradation, CMGDegradationRate
from .degradation import apply_cmg_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_cmg_config_base = _build_nominal_cmg_config_base_impl

def build_nominal_cmg_config(
    *args,
    degradation: CMGDegradation | None = None,
    degradation_rate: CMGDegradationRate | None = None,
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
    config = _build_nominal_cmg_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_cmg_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_cmg_faults(config, fault_specs)
    return config

