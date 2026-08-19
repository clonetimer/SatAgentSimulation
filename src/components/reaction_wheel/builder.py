"""Basilisk builder helpers for the reaction wheel component."""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

import math
from typing import Iterable

from .schemas import ReactionWheelBundle, ReactionWheelSpec


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
        location='src/components/reaction_wheel/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def require_basilisk_reaction_wheel() -> None:
    try:
        from Basilisk.simulation import reactionWheelStateEffector  # noqa: F401
        from Basilisk.utilities import simIncludeRW  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk reaction wheel modules are unavailable: {exc}") from exc


def _validated_friction_kwargs(spec: ReactionWheelSpec) -> dict[str, object]:
    """Translate component schema friction fields to ``rwFactory.create`` kwargs."""

    if not isinstance(spec.use_rw_friction, bool):
        raise TypeError("ReactionWheelSpec.use_rw_friction must be bool")
    for name in ("fCoulomb", "fStatic", "cViscous"):
        value = float(getattr(spec, name))
        if value < 0.0:
            raise ValueError(f"ReactionWheelSpec.{name} must be non-negative")
    if not spec.use_rw_friction:
        return {"useRWfriction": False}

    beta_static = float(spec.betaStatic)
    if beta_static == 0.0:
        raise ValueError("ReactionWheelSpec.betaStatic cannot be zero; use a positive value to enable Stribeck friction or a negative value to disable it")
    return {
        "useRWfriction": True,
        "fCoulomb": float(spec.fCoulomb),
        "fStatic": float(spec.fStatic),
        "betaStatic": beta_static,
        "cViscous": float(spec.cViscous),
    }



def _validated_common_rw_kwargs(spec: ReactionWheelSpec) -> dict[str, object]:
    """Translate non-friction ``ReactionWheelSpec`` fields to rwFactory kwargs."""
    if len(spec.position_b_m) != 3:
        raise ValueError("ReactionWheelSpec.position_b_m must have 3 elements")
    if bool(spec.use_min_torque) and float(spec.u_min_nm) <= 0.0:
        raise ValueError("ReactionWheelSpec.u_min_nm must be positive when use_min_torque=True")
    if spec.label is not None and len(str(spec.label)) > 5:
        raise ValueError("ReactionWheelSpec.label must be at most 5 characters for Basilisk rwFactory")
    kwargs: dict[str, object] = {
        "useMinTorque": bool(spec.use_min_torque),
        "P_max": float(spec.max_power_w),
        "rWB_B": [float(x) for x in spec.position_b_m],
    }
    if bool(spec.use_min_torque):
        kwargs["u_min"] = float(spec.u_min_nm)
    if spec.label is not None:
        kwargs["label"] = str(spec.label)
    return kwargs

def build_reaction_wheel_bundle(
    model_tag: str = "componentReactionWheelStateEffector",
    wheel_specs: Iterable[ReactionWheelSpec] | None = None,
) -> ReactionWheelBundle:
    """Create a Basilisk reaction-wheel state effector and config message.

    The component layer is the canonical owner of RW construction.  Subsystems
    supply ``ReactionWheelSpec`` values; they do not recreate Basilisk RW
    objects or remap native friction parameters themselves.
    """

    require_basilisk_reaction_wheel()
    from Basilisk.simulation import reactionWheelStateEffector
    from Basilisk.utilities import simIncludeRW

    specs = tuple(
        wheel_specs
        or (
            ReactionWheelSpec((1.0, 0.0, 0.0)),
            ReactionWheelSpec((0.0, 1.0, 0.0)),
            ReactionWheelSpec((0.0, 0.0, 1.0)),
        )
    )
    if not specs:
        raise ValueError("at least one ReactionWheelSpec is required")

    factory = simIncludeRW.rwFactory()
    for spec in specs:
        omega_max_rpm = float(spec.omega_max_rad_s) * 60.0 / (2.0 * math.pi)
        common_kwargs: dict[str, object] = {
            "Omega": float(spec.initial_speed_rpm),
            "useMaxTorque": True,
            "RWModel": reactionWheelStateEffector.BalancedWheels,
            **_validated_common_rw_kwargs(spec),
            **_validated_friction_kwargs(spec),
        }
        if str(spec.model).lower() == "custom":
            factory.create(
                str(spec.model),
                list(spec.axis_b),
                Omega_max=float(omega_max_rpm),
                Js=float(spec.wheel_js),
                u_max=float(spec.u_max_nm),
                **common_kwargs,
            )
        else:
            # Standard manufacturer models retain their model-specific
            # Omega_max; rwFactory derives Js from maxMomentum and that limit.
            factory.create(
                str(spec.model),
                list(spec.axis_b),
                maxMomentum=float(spec.max_momentum_nms),
                u_max=float(spec.u_max_nm),
                **common_kwargs,
            )

    effector = reactionWheelStateEffector.ReactionWheelStateEffector()
    effector.ModelTag = model_tag
    config_msg = factory.getConfigMessage()
    wheel_count = factory.getNumOfDevices() if hasattr(factory, "getNumOfDevices") else len(specs)
    return ReactionWheelBundle(factory=factory, effector=effector, config_msg=config_msg, wheel_count=wheel_count)


def attach_reaction_wheels_to_spacecraft(
    bundle: ReactionWheelBundle,
    spacecraft: object,
    model_tag: str = "RW_cluster",
) -> None:
    """Attach reaction wheels from a bundle to a Basilisk spacecraft."""
    bundle.factory.addToSpacecraft(model_tag, bundle.effector, spacecraft)


def write_rw_torque_message(torques_nm: Iterable[float]) -> object:
    """Write an ArrayMotorTorqueMsg with the given wheel torques."""
    from Basilisk.architecture import messaging

    payload = messaging.ArrayMotorTorqueMsgPayload()
    values = list(payload.motorTorque)
    for idx, torque in enumerate(torques_nm):
        values[idx] = float(torque)
    payload.motorTorque = values
    return messaging.ArrayMotorTorqueMsg().write(payload)


def build_reaction_wheel_power_node(
    model_tag: str = "ReactionWheelPower",
    *,
    base_power_w: float = 0.0,
    wheel_speed_rad_s: float = 100.0,
    wheel_torque_nm: float = 0.0,
    p_max_w: float = -1.0,
):
    """Create a Basilisk ``ReactionWheelPower`` node driven by an RW log msg.

    This helper gives EPS a native power-node contract for RW load coupling.
    Integrated ADCS simulations can replace the internally generated
    ``RWConfigLogMsg`` with a live reaction-wheel effector output through the
    returned module's ``rwStateInMsg``.
    """
    from Basilisk.architecture import messaging
    from Basilisk.simulation import ReactionWheelPower

    payload = messaging.RWConfigLogMsgPayload()
    payload.Omega = float(wheel_speed_rad_s)
    payload.u_current = float(wheel_torque_nm)
    payload.U_s = float(wheel_torque_nm)
    payload.P_max = float(p_max_w)
    rw_msg = messaging.RWConfigLogMsg().write(payload)
    node = ReactionWheelPower.ReactionWheelPower()
    node.ModelTag = str(model_tag)
    node.basePowerNeed = abs(float(base_power_w))
    node.rwStateInMsg.subscribeTo(rw_msg)
    return node, rw_msg
