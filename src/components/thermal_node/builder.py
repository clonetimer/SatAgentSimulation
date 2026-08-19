"""thermal_node component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import ThermalNodeConfig, ThermalNodeScheduledConfig
from .faults import apply_thermal_node_faults
from .faults import FaultSpec

from dataclasses import dataclass




@dataclass(frozen=True)
class ThermalNodeState:
    temp_k: float = 300.0


@dataclass(frozen=True)
class ThermalProfileResult:
    time_s: tuple
    temp_k: tuple


def step_thermal_node(s, c, power_w, dt_s):
    if dt_s < 0:
        raise ValueError('dt_s')
    alpha = min(1.0, dt_s / max(c.tau_s, 1e-12))
    target = c.ambient_k + c.heat_gain_k_per_w * power_w
    temp = max(c.min_temp_k, min(c.max_temp_k, s.temp_k + alpha * (target - s.temp_k)))
    return ThermalNodeState(temp)


def simulate_thermal_power_profile(s, c, profile, dt_s):
    if dt_s <= 0:
        raise ValueError('dt_s')
    t = [0.0]
    temp = [s.temp_k]
    for i, p in enumerate(profile):
        s = step_thermal_node(s, c, p, dt_s)
        t.append((i + 1) * dt_s)
        temp.append(s.temp_k)
    return ThermalProfileResult(tuple(t), tuple(temp))


def _build_nominal_thermal_node_config_base_impl(ambient_k: float = 300, tau_s: float = 10, heat_gain_k_per_w: float = 1, min_temp_k: float = 0.0, max_temp_k: float = 1000.0, fault_specs: list[FaultSpec] | None = None) -> ThermalNodeConfig:
    """Build a nominal ThermalNodeConfig with all parameters defaulted."""
    return ThermalNodeConfig(
        ambient_k=ambient_k,
        tau_s=tau_s,
        heat_gain_k_per_w=heat_gain_k_per_w,
        min_temp_k=min_temp_k,
        max_temp_k=max_temp_k,
    )


_messaging = None
_sysModel = None
_macros = None

try:
    from Basilisk.architecture import messaging, sysModel
    from Basilisk.utilities import macros
    _messaging = messaging
    _sysModel = sysModel
    _macros = macros
except ImportError as exc:
    record_runtime_diagnostic(
        code='OPTIONAL_DEPENDENCY_IMPORT_UNAVAILABLE',
        category=DiagnosticCategory.OPTIONAL_DEPENDENCY_PROBE,
        location='src/components/thermal_node/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )


_ThermalSysModelBase = _sysModel.SysModel if _sysModel is not None else object


def basilisk_scheduled_thermal_available() -> bool:
    """Return True when Basilisk scheduling modules are available."""
    return _messaging is not None and _sysModel is not None and _macros is not None


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def require_basilisk_scheduled_thermal() -> None:
    """Raise RuntimeError if Basilisk scheduling modules are unavailable."""
    if not basilisk_scheduled_thermal_available():
        raise RuntimeError("Basilisk messaging/sysModel/macros modules are unavailable")




@dataclass(frozen=True)
class ThermalNodeScheduledTraceRow:
    time_s: float
    node_name: str
    heat_input_w: float
    heater_on: bool
    cooling_on: bool
    heater_power_w: float
    cooling_power_w: float
    passive_cooling_w: float
    temp_c: float
    min_margin_c: float
    max_margin_c: float
    thermal_safe: bool
    mode_recommendation: str


class ConstantThermalPowerInput(_ThermalSysModelBase):
    """Basilisk-scheduled constant component heat source."""

    def __init__(self, heat_power_w: float, tag: str = "constantThermalPowerInput"):
        super().__init__()
        self.ModelTag = tag
        self.heat_power_w = float(heat_power_w)
        self.heatOutMsg = _messaging.PowerNodeUsageMsg()

    def Reset(self, CurrentSimNanos: int) -> None:
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        payload = _messaging.PowerNodeUsageMsgPayload()
        payload.netPower = -abs(self.heat_power_w)
        self.heatOutMsg.write(payload, CurrentSimNanos, self.moduleID)


class ThermalNodeScheduledSysModel(_ThermalSysModelBase):
    """Basilisk-scheduled lumped thermal node with heater/cooling state.

    Inputs
    ------
    heatInMsg: PowerNodeUsageMsg
        Component heat input represented as a load-like power term.

    Outputs
    -------
    thermalStatusOutMsg: DeviceStatusMsg
        1 when safe, 0 when unsafe.
    heaterStatusOutMsg: DeviceStatusMsg
        1 when heater is active.
    coolingStatusOutMsg: DeviceStatusMsg
        1 when cooling action is active.
    """

    def __init__(self, cfg: ThermalNodeScheduledConfig):
        super().__init__()
        self.ModelTag = f"{cfg.node_name}ThermalNode"
        self.cfg = cfg
        self.heatInMsg = _messaging.PowerNodeUsageMsgReader()
        self.thermalStatusOutMsg = _messaging.DeviceStatusMsg()
        self.heaterStatusOutMsg = _messaging.DeviceStatusMsg()
        self.coolingStatusOutMsg = _messaging.DeviceStatusMsg()
        self.temp_c = float(cfg.initial_temp_c)
        self.last_ns: int | None = None
        self.heater_on = False
        self.cooling_on = False
        self._safe_latched = True
        self.trace: list[ThermalNodeScheduledTraceRow] = []

    def Reset(self, CurrentSimNanos: int) -> None:
        self.temp_c = float(self.cfg.initial_temp_c)
        self.last_ns = None
        self.heater_on = False
        self.cooling_on = False
        self._safe_latched = True
        self.trace.clear()
        self.UpdateState(CurrentSimNanos)

    def _update_actuators(self) -> None:
        cfg = self.cfg
        if self.temp_c < cfg.heater_on_below_c:
            self.heater_on = True
        elif self.temp_c > cfg.heater_off_above_c:
            self.heater_on = False
        if self.temp_c > cfg.cooling_on_above_c:
            self.cooling_on = True
        elif self.temp_c < cfg.cooling_off_below_c:
            self.cooling_on = False

    def _update_safe_latch(self) -> bool:
        cfg = self.cfg
        if self.temp_c > cfg.max_safe_temp_c or self.temp_c < cfg.min_safe_temp_c:
            self._safe_latched = False
        elif (cfg.min_safe_temp_c + cfg.hysteresis_c) <= self.temp_c <= (cfg.max_safe_temp_c - cfg.hysteresis_c):
            self._safe_latched = True
        return self._safe_latched

    def UpdateState(self, CurrentSimNanos: int) -> None:
        if self.last_ns is None:
            dt = 0.0
        else:
            dt = max(0.0, float(CurrentSimNanos - self.last_ns) * _macros.NANO2SEC)
        self.last_ns = CurrentSimNanos

        heat_w = abs(float(self.heatInMsg().netPower))
        self._update_actuators()
        heater_w = self.cfg.heater_power_w if self.heater_on else 0.0
        cooling_w = self.cfg.cooling_power_w if self.cooling_on else 0.0
        passive_cooling_w = self.cfg.conductance_w_per_c * (self.temp_c - self.cfg.ambient_temp_c)
        net_w = heat_w + heater_w - cooling_w - passive_cooling_w
        self.temp_c += net_w * dt / max(1e-9, self.cfg.thermal_capacity_j_per_c)

        safe = self._update_safe_latch()
        max_margin = float(self.cfg.max_safe_temp_c) - self.temp_c
        min_margin = self.temp_c - float(self.cfg.min_safe_temp_c)
        recommendation = "NOMINAL" if safe else "THERMAL_POWER_LOAD_MANAGEMENT"

        safe_msg = _messaging.DeviceStatusMsgPayload(); safe_msg.deviceStatus = int(safe)
        heater_msg = _messaging.DeviceStatusMsgPayload(); heater_msg.deviceStatus = int(self.heater_on)
        cooling_msg = _messaging.DeviceStatusMsgPayload(); cooling_msg.deviceStatus = int(self.cooling_on)
        self.thermalStatusOutMsg.write(safe_msg, CurrentSimNanos, self.moduleID)
        self.heaterStatusOutMsg.write(heater_msg, CurrentSimNanos, self.moduleID)
        self.coolingStatusOutMsg.write(cooling_msg, CurrentSimNanos, self.moduleID)

        self.trace.append(ThermalNodeScheduledTraceRow(
            time_s=float(CurrentSimNanos) * _macros.NANO2SEC,
            node_name=self.cfg.node_name,
            heat_input_w=heat_w,
            heater_on=bool(self.heater_on),
            cooling_on=bool(self.cooling_on),
            heater_power_w=heater_w,
            cooling_power_w=cooling_w,
            passive_cooling_w=passive_cooling_w,
            temp_c=self.temp_c,
            min_margin_c=min_margin,
            max_margin_c=max_margin,
            thermal_safe=bool(safe),
            mode_recommendation=recommendation,
        ))


ThermalNodeScheduledTraceRow.thermal_margin_c = property(lambda self: self.max_margin_c)

# Component fault/degradation compatibility wrappers
from .degradation import ThermalNodeDegradation, ThermalNodeDegradationRate
from .degradation import apply_thermal_node_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_thermal_node_config_base = _build_nominal_thermal_node_config_base_impl

def build_nominal_thermal_node_config(
    *args,
    degradation: ThermalNodeDegradation | None = None,
    degradation_rate: ThermalNodeDegradationRate | None = None,
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
    config = _build_nominal_thermal_node_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_thermal_node_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_thermal_node_faults(config, fault_specs)
    return config

