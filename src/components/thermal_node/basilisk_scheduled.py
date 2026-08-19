"""Component-level Basilisk-scheduled thermal node backend.

This module is intentionally **not** labeled as a native Basilisk thermal
network.  It is a Python SysModel that is scheduled by Basilisk and exchanges
Basilisk architecture messages.  It provides a component-level thermal building
block for subsystem-level thermal runners.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

from dataclasses import dataclass

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
        location='src/components/thermal_node/basilisk_scheduled.py:<module>:01',
        exception=exc,
        strict=False,
    )


_ThermalSysModelBase = _sysModel.SysModel if _sysModel is not None else object


def basilisk_scheduled_thermal_available() -> bool:
    """Return True when Basilisk scheduling modules are available."""
    return _messaging is not None and _sysModel is not None and _macros is not None


def require_basilisk_scheduled_thermal() -> None:
    """Raise RuntimeError if Basilisk scheduling modules are unavailable."""
    if not basilisk_scheduled_thermal_available():
        raise RuntimeError("Basilisk messaging/sysModel/macros modules are unavailable")


@dataclass(frozen=True)
class ThermalNodeScheduledConfig:
    node_name: str = "payload_thermal_node"
    initial_temp_c: float = 22.0
    ambient_temp_c: float = 18.0
    min_safe_temp_c: float = -5.0
    max_safe_temp_c: float = 45.0
    hysteresis_c: float = 2.0
    thermal_capacity_j_per_c: float = 900.0
    conductance_w_per_c: float = 0.35
    heater_power_w: float = 18.0
    cooling_power_w: float = 12.0
    heater_on_below_c: float = 2.0
    heater_off_above_c: float = 6.0
    cooling_on_above_c: float = 40.0
    cooling_off_below_c: float = 36.0


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


class DynamicThermalPowerInput(_ThermalSysModelBase):
    """Basilisk-scheduled dynamic heat source that aggregates power sink messages.

    This component reads power consumption from multiple EPS load sinks and
    converts them to thermal heat input, modulated by heat efficiency factors.
    """

    def __init__(self, tag: str = "dynamicThermalPowerInput"):
        super().__init__()
        self.ModelTag = tag
        self.powerInMsgs: list[_messaging.PowerNodeUsageMsgReader] = []
        self.heat_efficiency_factors: list[float] = []
        self.shadow_factor_input: _messaging.PowerNodeUsageMsgReader | None = None
        self.heatOutMsg = _messaging.PowerNodeUsageMsg()

    def add_power_input(self, power_msg, efficiency_factor: float = 1.0):
        """Add a power sink message to be aggregated as heat input."""
        reader = _messaging.PowerNodeUsageMsgReader()
        reader.subscribeTo(power_msg)
        self.powerInMsgs.append(reader)
        self.heat_efficiency_factors.append(float(efficiency_factor))

    def set_shadow_factor_input(self, shadow_msg):
        """Set the shadow factor input message for solar heating modulation."""
        reader = _messaging.PowerNodeUsageMsgReader()
        reader.subscribeTo(shadow_msg)
        self.shadow_factor_input = reader

    def Reset(self, CurrentSimNanos: int) -> None:
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        total_heat_w = 0.0
        for reader, efficiency in zip(self.powerInMsgs, self.heat_efficiency_factors):
            try:
                msg = reader()
                power_w = abs(float(msg.netPower)) if hasattr(msg, 'netPower') else 0.0
                total_heat_w += power_w * efficiency
            except Exception as exc:
                record_runtime_diagnostic(
                    code='THERMAL_NODE_NATIVE_UPDATE_FAILED',
                    category=DiagnosticCategory.NATIVE_MAPPING_FAILURE,
                    location='src/components/thermal_node/basilisk_scheduled.py:UpdateState:02',
                    exception=exc,
                    strict=None,
                )

        shadow_factor = 1.0
        if self.shadow_factor_input is not None:
            try:
                msg = self.shadow_factor_input()
                shadow_factor = float(msg.netPower) if hasattr(msg, 'netPower') else 1.0
            except Exception as exc:
                record_runtime_diagnostic(
                    code='THERMAL_NODE_NATIVE_UPDATE_FAILED',
                    category=DiagnosticCategory.NATIVE_MAPPING_FAILURE,
                    location='src/components/thermal_node/basilisk_scheduled.py:UpdateState:03',
                    exception=exc,
                    strict=None,
                )

        payload = _messaging.PowerNodeUsageMsgPayload()
        payload.netPower = -abs(total_heat_w * shadow_factor)
        self.heatOutMsg.write(payload, CurrentSimNanos, self.moduleID)


class EclipseShadowFactorConverter(_ThermalSysModelBase):
    """Basilisk-scheduled converter that transforms EclipseMsg to shadow factor.

    The EclipseMsg contains an eclipseFactor field (0=full eclipse, 1=full sun).
    This component converts it to a PowerNodeUsageMsg where netPower equals
    the shadow factor, for use by DynamicThermalPowerInput.
    """

    def __init__(self, tag: str = "eclipseShadowFactorConverter"):
        super().__init__()
        self.ModelTag = tag
        self.eclipseInMsg = _messaging.EclipseMsgReader()
        self.sunlightHeatW = 0.0
        self.shadowFactorOutMsg = _messaging.PowerNodeUsageMsg()

    def set_sunlight_heat_w(self, sunlight_heat_w: float):
        """Set the base sunlight heating power in watts."""
        self.sunlightHeatW = float(sunlight_heat_w)

    def Reset(self, CurrentSimNanos: int) -> None:
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        shadow_factor = 1.0
        try:
            msg = self.eclipseInMsg()
            try:
                shadow_factor = float(msg.illuminationFactor)
            except Exception:
                try:
                    shadow_factor = float(msg.eclipseFactor)
                except Exception:
                    try:
                        shadow_factor = float(msg.shadowFactor)
                    except Exception:
                        shadow_factor = 1.0
        except Exception as exc:
            record_runtime_diagnostic(
                code='THERMAL_NODE_NATIVE_UPDATE_FAILED',
                category=DiagnosticCategory.NATIVE_MAPPING_FAILURE,
                location='src/components/thermal_node/basilisk_scheduled.py:UpdateState:01',
                exception=exc,
                strict=None,
            )

        payload = _messaging.PowerNodeUsageMsgPayload()
        payload.netPower = float(shadow_factor)
        self.shadowFactorOutMsg.write(payload, CurrentSimNanos, self.moduleID)


class ModeThermalInput(_ThermalSysModelBase):
    """Basilisk-scheduled mode-dependent thermal input.

    This component reads the current operational mode and outputs the
    corresponding thermal power based on mode_power_w_by_node configuration.
    """

    def __init__(self, tag: str = "modeThermalInput"):
        super().__init__()
        self.ModelTag = tag
        self.mode_power_w_by_node: dict[str, dict[str, float]] = {}
        self.current_mode = "safePoint"
        self.heatOutMsg = _messaging.PowerNodeUsageMsg()

    def set_mode_power_map(self, mode_power_w_by_node: dict[str, dict[str, float]]):
        """Set the mode-to-power mapping."""
        self.mode_power_w_by_node = mode_power_w_by_node

    def set_current_mode(self, mode: str):
        """Set the current operational mode."""
        self.current_mode = mode

    def Reset(self, CurrentSimNanos: int) -> None:
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        mode_power = self.mode_power_w_by_node.get(self.current_mode, {})
        total_heat_w = sum(float(v) for v in mode_power.values())

        payload = _messaging.PowerNodeUsageMsgPayload()
        payload.netPower = -abs(total_heat_w)
        self.heatOutMsg.write(payload, CurrentSimNanos, self.moduleID)


class HeaterPowerFeedback(_ThermalSysModelBase):
    """Basilisk-scheduled heater power feedback to EPS.

    This component reads heater status messages and outputs the corresponding
    power consumption as a load to the EPS battery, enabling energy-consistent
    simulation where heater activation affects battery SOC.
    """

    def __init__(self, tag: str = "heaterPowerFeedback"):
        super().__init__()
        self.ModelTag = tag
        self.heaterInMsgs: list[_messaging.DeviceStatusMsgReader] = []
        self.heater_power_w_list: list[float] = []
        self.powerOutMsg = _messaging.PowerNodeUsageMsg()

    def add_heater_input(self, heater_status_msg, heater_power_w: float):
        """Add a heater status message and its power consumption."""
        reader = _messaging.DeviceStatusMsgReader()
        reader.subscribeTo(heater_status_msg)
        self.heaterInMsgs.append(reader)
        self.heater_power_w_list.append(float(heater_power_w))

    def Reset(self, CurrentSimNanos: int) -> None:
        self.UpdateState(CurrentSimNanos)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        total_power_w = 0.0
        for reader, power_w in zip(self.heaterInMsgs, self.heater_power_w_list):
            try:
                msg = reader()
                if bool(getattr(msg, 'deviceStatus', 0)):
                    total_power_w += power_w
            except Exception as exc:
                record_runtime_diagnostic(
                    code='THERMAL_NODE_NATIVE_UPDATE_FAILED',
                    category=DiagnosticCategory.NATIVE_MAPPING_FAILURE,
                    location='src/components/thermal_node/basilisk_scheduled.py:UpdateState:04',
                    exception=exc,
                    strict=None,
                )

        payload = _messaging.PowerNodeUsageMsgPayload()
        payload.netPower = -abs(total_power_w)
        self.powerOutMsg.write(payload, CurrentSimNanos, self.moduleID)


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
        # Direct runtime mutation hooks used by whole-spacecraft thermal faults.
        self.heater_forced_state: bool | None = None
        self.heater_power_scale: float = 1.0
        self.cooling_power_scale: float = 1.0
        self.fault_heat_bias_w: float = 0.0
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
        if self.heater_forced_state is not None:
            self.heater_on = bool(self.heater_forced_state)
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
        heater_w = self.cfg.heater_power_w * max(0.0, float(self.heater_power_scale)) if self.heater_on else 0.0
        cooling_w = self.cfg.cooling_power_w * max(0.0, float(self.cooling_power_scale)) if self.cooling_on else 0.0
        passive_cooling_w = self.cfg.conductance_w_per_c * (self.temp_c - self.cfg.ambient_temp_c)
        net_w = heat_w + heater_w + float(self.fault_heat_bias_w) - cooling_w - passive_cooling_w
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

# Compatibility property for earlier thermal summary code.
ThermalNodeScheduledTraceRow.thermal_margin_c = property(lambda self: self.max_margin_c)
