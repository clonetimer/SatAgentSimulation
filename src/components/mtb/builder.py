"""mtb component builder module.

Provides both Python model config builders and Basilisk-native component factories.

Basilisk integration notes
---------------------------
1. The MTB is a **dynamic effector** — attach to a spacecraft with
   ``sc.addDynamicEffector(bundle.effector)``.  ``computeForceTorque``
   is called automatically by the spacecraft integrator, so
   ``AddModelToTask`` is not strictly required for torque output.
2. Torque follows ``torque = m × B`` where ``m`` is the dipole moment
   commanded via ``mtbCmdInMsg`` and ``B`` is the magnetic field from
   ``magInMsg``.
3. Dipole commands are clamped to ``maxMtbDipoles`` from the config
   message.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import MtbConfig, MtbSpec
from .faults import apply_mtb_faults
from .faults import FaultSpec

from dataclasses import dataclass
from typing import Iterable

from ..dynamic_models import v3, cross, dot, norm, unit, expand


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
        location='src/components/mtb/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class MtbState:
    dipole_am2: tuple = (0, 0, 0)


@dataclass(frozen=True)
class MtbProfileResult:
    time_s: tuple
    dipole_am2: tuple
    torque_nm: tuple


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def shape_mtb_command(raw, c):
    return tuple(
        max(-c.dipole_limit_am2[i], min(c.dipole_limit_am2[i], float(raw[i]) if i < len(raw) else 0))
        for i in range(c.num_axes)
    )


def update_mtb_dipole(raw, s, c, dt_s):
    cmd = shape_mtb_command(raw, c)
    if c.lag_tau_s <= 0 or dt_s <= 0:
        return MtbState(cmd)
    a = dt_s / (c.lag_tau_s + dt_s)
    prev = expand(s.dipole_am2, c.num_axes, 0)
    return MtbState(tuple(prev[i] + a * (cmd[i] - prev[i]) for i in range(c.num_axes)))


def compute_mtb_torque_nm(m, b):
    return cross(m, b)


def simulate_dipole_profile(s, c, profile, b, dt_s):
    t = [0.0]
    d = [s.dipole_am2]
    tq = [compute_mtb_torque_nm(s.dipole_am2, b)]
    for i, cmd in enumerate(profile):
        s = update_mtb_dipole(cmd, s, c, dt_s)
        t.append((i + 1) * dt_s)
        d.append(s.dipole_am2)
        tq.append(compute_mtb_torque_nm(s.dipole_am2, b))
    return MtbProfileResult(tuple(t), tuple(d), tuple(tq))


def project_torque_perpendicular_to_field(tau, b):
    b = v3(b)
    tau = v3(tau)
    b2 = dot(b, b)
    if b2 <= 0:
        return ((0, 0, 0), tuple(tau))
    ps = dot(tau, b) / b2
    par = [ps * x for x in b]
    perp = [tau[i] - par[i] for i in range(3)]
    return tuple(perp), tuple(par)


@dataclass(frozen=True)
class MtbMappingResult:
    dipole_am2: tuple
    achievable_torque_nm: tuple
    rejected_parallel_torque_nm: tuple
    saturated: bool
    zero_field_guard: bool


def map_torque_to_dipole(tau, b, c=None):
    c = c or MtbConfig()
    b = v3(b)
    b2 = dot(b, b)
    if b2 <= 1e-18:
        return MtbMappingResult((0, 0, 0), (0, 0, 0), tuple(v3(tau)), False, True)
    ach, rej = project_torque_perpendicular_to_field(tau, b)
    raw = tuple(x / b2 for x in cross(b, ach))
    cmd = shape_mtb_command(raw, c)
    return MtbMappingResult(cmd, compute_mtb_torque_nm(cmd, b), rej, False, False)


def _build_nominal_mtb_config_base_impl(
    num_axes: int = 3,
    dipole_limit_am2: tuple = (1, 1, 1),
    lag_tau_s: float = 1.0,
    fault_specs: list[FaultSpec] | None = None,
) -> MtbConfig:
    """Build a nominal MtbConfig with default values."""
    return MtbConfig(
        num_axes=num_axes,
        dipole_limit_am2=dipole_limit_am2,
        lag_tau_s=lag_tau_s,
    )




@dataclass
class MtbBundle:
    """Container for an MTB effector and its associated messages.

    Attributes
    ----------
    effector : MtbEffector
        The MTB dynamic effector instance.
    config_msg : Msg
        ``MTBArrayConfigMsg`` with bar count, axes, and max dipoles.
    cmd_msg : Msg
        ``MTBCmdMsg`` with commanded dipole moments.
    field_msg : Msg
        ``MagneticFieldMsg`` with the inertial magnetic field vector.
    bar_count : int
        Number of MTB bars in the bundle.
    """
    effector: object
    config_msg: object
    cmd_msg: object
    field_msg: object
    bar_count: int
    specs: tuple[MtbSpec, ...] = ()
    commanded_dipoles_am2: tuple[float, ...] = ()


def require_basilisk_mtb() -> None:
    try:
        from Basilisk.simulation import MtbEffector  # noqa: F401
        from Basilisk.architecture import messaging  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk MTB modules are unavailable: {exc}") from exc


def build_mtb_effector_bundle(
    model_tag: str = "componentMtbEffector",
    specs: Iterable[MtbSpec] | None = None,
    cmd_dipoles_am2: Iterable[float] | None = None,
    magnetic_field_t: tuple[float, float, float] = (2.0e-5, -1.0e-5, 3.0e-5),
    bar_count: int | None = None,
    max_dipole_am2: float = 0.2,
) -> MtbBundle:
    """Create an MtbEffector plus command/config/field messages.

    Parameters
    ----------
    model_tag : str, optional
        Module name for logging and debugging.
    specs : iterable of MtbSpec, optional
        MTB bar specifications.  Defaults to three orthogonal bars
        along the body x/y/z axes with 0.2 A·m² max dipole each.
    cmd_dipoles_am2 : iterable of float, optional
        Initial commanded dipole moments in A·m², one per bar.
        Defaults to zero.
    magnetic_field_t : tuple of float, optional
        Initial inertial-frame magnetic field in tesla.  Defaults to
        a representative LEO value (2e-5, -1e-5, 3e-5) T.
    bar_count : int, optional
        Number of MTB bars (backward-compatible parameter).
        If provided and specs is None, creates orthogonal bars.
    max_dipole_am2 : float, optional
        Maximum dipole moment per bar (backward-compatible parameter).
        Used when bar_count is provided.

    Returns
    -------
    MtbBundle
        Bundle containing the effector and all associated messages.
        Attach to spacecraft with ``sc.addDynamicEffector(bundle.effector)``.
    """
    require_basilisk_mtb()
    from Basilisk.simulation import MtbEffector
    from Basilisk.architecture import messaging

    if specs is None and bar_count is not None:
        axes = [(1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)]
        bars = tuple(MtbSpec(axes[i % 3], max_dipole_am2) for i in range(bar_count))
    elif specs is None:
        bars = (
            MtbSpec((1.0, 0.0, 0.0)),
            MtbSpec((0.0, 1.0, 0.0)),
            MtbSpec((0.0, 0.0, 1.0)),
        )
    else:
        bars = tuple(specs)
    effector = MtbEffector.MtbEffector()
    effector.ModelTag = model_tag

    cfg = messaging.MTBArrayConfigMsgPayload()
    cfg.numMTB = len(bars)
    gt_vals = []
    max_vals = []
    for bar in bars:
        gt_vals.extend([float(x) for x in bar.axis_b])
        max_vals.append(float(bar.max_dipole_am2))
    cfg.GtMatrix_B = gt_vals
    cfg.maxMtbDipoles = max_vals
    config_msg = messaging.MTBArrayConfigMsg().write(cfg)
    effector.mtbParamsInMsg.subscribeTo(config_msg)

    cmd_values = tuple(float(x) for x in (cmd_dipoles_am2 or [0.0 for _ in bars]))
    cmd_msg = write_mtb_command_message(cmd_values)
    effector.mtbCmdInMsg.subscribeTo(cmd_msg)

    field = messaging.MagneticFieldMsgPayload()
    field.magField_N = list(magnetic_field_t)
    field_msg = messaging.MagneticFieldMsg().write(field)
    effector.magInMsg.subscribeTo(field_msg)

    return MtbBundle(
        effector=effector,
        config_msg=config_msg,
        cmd_msg=cmd_msg,
        field_msg=field_msg,
        bar_count=len(bars),
        specs=bars,
        commanded_dipoles_am2=cmd_values,
    )



def write_mtb_command_message(dipoles_am2: Iterable[float]) -> object:
    """Write an MTBCmdMsg with commanded dipole moments.

    This helper is the component-owned native input-message path for runtime
    MTB derating/failure semantics.
    """
    require_basilisk_mtb()
    from Basilisk.architecture import messaging

    payload = messaging.MTBCmdMsgPayload()
    payload.mtbDipoleCmds = [float(x) for x in dipoles_am2]
    return messaging.MTBCmdMsg().write(payload)


def update_mtb_command_message(bundle: MtbBundle, dipoles_am2: Iterable[float]) -> object:
    """Replace the bundle command message and resubscribe the native effector."""
    values = tuple(float(x) for x in dipoles_am2)
    msg = write_mtb_command_message(values)
    bundle.cmd_msg = msg
    bundle.commanded_dipoles_am2 = values
    bundle.effector.mtbCmdInMsg.subscribeTo(msg)
    return msg

def attach_mtb_to_spacecraft(bundle: MtbBundle, spacecraft: object) -> None:
    """Attach an MTB effector bundle to a Basilisk spacecraft.

    This helper encapsulates the correct attachment sequence for MTB
    dynamic effectors.

    Parameters
    ----------
    bundle : MtbBundle
        Bundle containing the MTB effector and associated messages.
    spacecraft : object
        Basilisk Spacecraft instance.
    """
    spacecraft.addDynamicEffector(bundle.effector)

# Component fault/degradation compatibility wrappers
from .degradation import MTBDegradation, MTBDegradationRate
from .degradation import apply_mtb_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_mtb_config_base = _build_nominal_mtb_config_base_impl

def build_nominal_mtb_config(
    *args,
    degradation: MTBDegradation | None = None,
    degradation_rate: MTBDegradationRate | None = None,
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
    config = _build_nominal_mtb_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_mtb_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_mtb_faults(config, fault_specs)
    return config

