"""power_sink component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import PowerSinkConfig
from .faults import apply_power_sink_faults
from .faults import FaultSpec

from dataclasses import dataclass


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
        location='src/components/power_sink/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


@dataclass(frozen=True)
class PowerSinkState:
    power_w: float
    mode: str = ''


def demand_w(c, mode='', enabled=True):
    return 0.0 if not enabled else float((c.mode_power_w or {}).get(mode, c.base_w))


def _build_nominal_power_sink_config_base_impl(name: str = "load", base_w: float = 8, mode_power_w: dict[str, float] | None = None, fault_specs: list[FaultSpec] | None = None) -> PowerSinkConfig:
    """Build a nominal PowerSinkConfig with all parameters defaulted."""
    if mode_power_w is None:
        mode_power_w = {"safe": 2}
    return PowerSinkConfig(name=name, base_w=base_w, mode_power_w=mode_power_w)


def require_basilisk_power_sink() -> None:
    try:
        from Basilisk.simulation import simplePowerSink  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk simplePowerSink is unavailable: {exc}") from exc


def build_simple_power_sink(model_tag: str, node_power_w: float):
    """Create a Basilisk ``SimplePowerSink``.

    Basilisk's field name is ``nodePowerOut``.  Positive values behave as a
    source for ``SimpleBattery``; negative values behave as a sink/load.

    Parameters
    ----------
    model_tag : str
        Module name for logging and debugging.
    node_power_w : float
        Power output in watts. Positive = source, negative = sink/load.

    Returns
    -------
    simplePowerSink.SimplePowerSink
        A configured power sink/source node.
    """
    require_basilisk_power_sink()
    from Basilisk.simulation import simplePowerSink
    node = simplePowerSink.SimplePowerSink()
    node.ModelTag = model_tag
    node.nodePowerOut = float(node_power_w)
    return node


def build_dynamic_power_sink(model_tag: str, node_power_w: float):
    """Create a Basilisk ``SimplePowerSink`` with dynamic power support.

    This is an alias for ``build_simple_power_sink`` that signals the
    caller intends to update ``nodePowerOut`` dynamically at runtime.

    Parameters
    ----------
    model_tag : str
        Module name for logging and debugging.
    node_power_w : float
        Initial power output in watts.

    Returns
    -------
    simplePowerSink.SimplePowerSink
        A configured power sink/source node.
    """
    return build_simple_power_sink(model_tag, node_power_w)

# Component fault/degradation compatibility wrappers
from .degradation import PowerSinkDegradation, PowerSinkDegradationRate
from .degradation import apply_power_sink_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_power_sink_config_base = _build_nominal_power_sink_config_base_impl

def build_nominal_power_sink_config(
    *args,
    degradation: PowerSinkDegradation | None = None,
    degradation_rate: PowerSinkDegradationRate | None = None,
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
    config = _build_nominal_power_sink_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_power_sink_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_power_sink_faults(config, fault_specs)
    return config

