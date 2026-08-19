"""Cross-subsystem resource feedback bridges for whole-spacecraft runs.

These Basilisk-scheduled Python modules translate *actual runtime activity*
(data rate, reaction-wheel mechanical work and thruster output) into
``PowerNodeUsageMsg`` values.  The same messages can be attached to the EPS
battery and to the thermal network, which avoids static mode-power proxies for
loads whose activity is already available as a Basilisk message.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import math
from typing import Any, Iterable

try:
    from Basilisk.architecture import messaging, sysModel
except ImportError:  # pragma: no cover - optional runtime dependency
    messaging = None  # type: ignore[assignment]
    sysModel = None  # type: ignore[assignment]


def _require_basilisk() -> None:
    if messaging is None or sysModel is None:
        raise RuntimeError("Basilisk messaging/sysModel modules are required")


class InputReadStatus(StrEnum):
    VALID = "VALID"
    NOT_READY = "NOT_READY"
    ERROR = "ERROR"


def _read_payload(reader: Any | None, current_ns: int, *, optional: bool = False) -> tuple[Any | None, InputReadStatus, str | None]:
    if reader is None:
        return None, InputReadStatus.VALID if optional else InputReadStatus.ERROR, None if optional else "reader is not configured"
    try:
        if hasattr(reader, "isLinked") and not bool(reader.isLinked()):
            return None, InputReadStatus.ERROR, "message reader is not linked"
        if hasattr(reader, "isWritten") and not bool(reader.isWritten()):
            status = InputReadStatus.NOT_READY if int(current_ns) == 0 else InputReadStatus.ERROR
            return None, status, "message has not been written"
        return reader(), InputReadStatus.VALID, None
    except Exception as exc:
        status = InputReadStatus.NOT_READY if int(current_ns) == 0 else InputReadStatus.ERROR
        return None, status, f"{type(exc).__name__}: {exc}"


def _status_enabled(reader: Any | None, current_ns: int, default: bool = True) -> tuple[bool, InputReadStatus, str | None]:
    if reader is None:
        return default, InputReadStatus.VALID, None
    payload, status, error = _read_payload(reader, current_ns)
    if payload is None:
        return False, status, error
    return bool(payload.deviceStatus), status, error


def _write_power(out_msg: Any, power_w: float, t: int, module_id: int) -> None:
    payload = messaging.PowerNodeUsageMsgPayload()
    # Basilisk simpleBattery convention: generation positive, electrical load negative.
    payload.netPower = -max(0.0, float(power_w))
    out_msg.write(payload, t, module_id)


@dataclass(frozen=True)
class ActivityPowerSample:
    time_ns: int
    enabled: bool
    activity: float
    electrical_power_w: float
    input_status: InputReadStatus = InputReadStatus.VALID
    error: str | None = None


if sysModel is not None:
    class DataActivityPowerBridge(sysModel.SysModel):
        """Convert a DataNodeUsage rate into an electrical power sink."""

        def __init__(
            self,
            model_tag: str,
            *,
            nominal_rate_bps: float,
            active_power_w: float,
            idle_power_w: float = 0.0,
            data_msg: Any | None = None,
            enable_status_msg: Any | None = None,
        ) -> None:
            super().__init__()
            self.ModelTag = model_tag
            self.nominal_rate_bps = max(1e-12, abs(float(nominal_rate_bps)))
            self.active_power_w = max(0.0, float(active_power_w))
            self.idle_power_w = max(0.0, float(idle_power_w))
            self.dataInMsg = messaging.DataNodeUsageMsgReader()
            if data_msg is not None:
                self.dataInMsg.subscribeTo(data_msg)
            self.enableInMsg = messaging.DeviceStatusMsgReader()
            self._has_enable = enable_status_msg is not None
            if enable_status_msg is not None:
                self.enableInMsg.subscribeTo(enable_status_msg)
            self.powerOutMsg = messaging.PowerNodeUsageMsg()
            self.trace: list[ActivityPowerSample] = []

        def Reset(self, CurrentSimNanos: int) -> None:
            self.trace.clear()
            self.UpdateState(CurrentSimNanos)

        def UpdateState(self, CurrentSimNanos: int) -> None:
            enabled, enable_status, enable_error = _status_enabled(self.enableInMsg if self._has_enable else None, CurrentSimNanos)
            data, data_status, data_error = _read_payload(self.dataInMsg, CurrentSimNanos)
            baud = abs(float(data.baudRate)) if data is not None else 0.0
            status = InputReadStatus.ERROR if InputReadStatus.ERROR in (enable_status, data_status) else (InputReadStatus.NOT_READY if InputReadStatus.NOT_READY in (enable_status, data_status) else InputReadStatus.VALID)
            error = enable_error or data_error
            activity = min(1.0, max(0.0, baud / self.nominal_rate_bps))
            power = 0.0 if not enabled else self.idle_power_w + self.active_power_w * activity
            _write_power(self.powerOutMsg, power, CurrentSimNanos, self.moduleID)
            self.trace.append(ActivityPowerSample(CurrentSimNanos, enabled, activity, power, status, error))


    class DownlinkActivityPowerBridge(sysModel.SysModel):
        """Convert native DownlinkHandling activity into communication power."""

        def __init__(
            self,
            model_tag: str,
            *,
            nominal_rate_bps: float,
            active_power_w: float,
            idle_power_w: float = 0.0,
            downlink_msg: Any | None = None,
            enable_status_msg: Any | None = None,
        ) -> None:
            super().__init__()
            self.ModelTag = model_tag
            self.nominal_rate_bps = max(1e-12, abs(float(nominal_rate_bps)))
            self.active_power_w = max(0.0, float(active_power_w))
            self.idle_power_w = max(0.0, float(idle_power_w))
            self.downlinkInMsg = messaging.DownlinkHandlingMsgReader()
            if downlink_msg is not None:
                self.downlinkInMsg.subscribeTo(downlink_msg)
            self.enableInMsg = messaging.DeviceStatusMsgReader()
            self._has_enable = enable_status_msg is not None
            if enable_status_msg is not None:
                self.enableInMsg.subscribeTo(enable_status_msg)
            self.powerOutMsg = messaging.PowerNodeUsageMsg()
            self.trace: list[ActivityPowerSample] = []

        def Reset(self, CurrentSimNanos: int) -> None:
            self.trace.clear()
            self.UpdateState(CurrentSimNanos)

        def UpdateState(self, CurrentSimNanos: int) -> None:
            enabled, enable_status, enable_error = _status_enabled(self.enableInMsg if self._has_enable else None, CurrentSimNanos)
            payload, data_status, data_error = _read_payload(self.downlinkInMsg, CurrentSimNanos)
            attempted = 0.0
            link_active = False
            if payload is not None:
                attempted = max(abs(float(getattr(payload, "attemptedDataRate", 0.0))), abs(float(getattr(payload, "deliveredDataRate", 0.0))))
                link_active = bool(getattr(payload, "linkActive", 0))
            status = InputReadStatus.ERROR if InputReadStatus.ERROR in (enable_status, data_status) else (InputReadStatus.NOT_READY if InputReadStatus.NOT_READY in (enable_status, data_status) else InputReadStatus.VALID)
            error = enable_error or data_error
            activity = min(1.0, max(0.0, attempted / self.nominal_rate_bps)) if link_active else 0.0
            power = 0.0 if not enabled else self.idle_power_w + self.active_power_w * activity
            _write_power(self.powerOutMsg, power, CurrentSimNanos, self.moduleID)
            self.trace.append(ActivityPowerSample(CurrentSimNanos, enabled, activity, power, status, error))


    class AdcsControlPowerBridge(sysModel.SysModel):
        """Convert RW mechanical work into additional ADCS electrical power."""

        def __init__(
            self,
            model_tag: str,
            *,
            motor_torque_msg: Any,
            wheel_speed_msg: Any,
            base_power_w: float = 0.0,
            drive_efficiency: float = 0.75,
            max_power_w: float = 120.0,
            enable_status_msg: Any | None = None,
        ) -> None:
            super().__init__()
            self.ModelTag = model_tag
            self.motorTorqueInMsg = messaging.ArrayMotorTorqueMsgReader()
            self.motorTorqueInMsg.subscribeTo(motor_torque_msg)
            self.wheelSpeedInMsg = messaging.RWSpeedMsgReader()
            self.wheelSpeedInMsg.subscribeTo(wheel_speed_msg)
            self.enableInMsg = messaging.DeviceStatusMsgReader()
            self._has_enable = enable_status_msg is not None
            if enable_status_msg is not None:
                self.enableInMsg.subscribeTo(enable_status_msg)
            self.base_power_w = max(0.0, float(base_power_w))
            self.drive_efficiency = min(1.0, max(1e-6, float(drive_efficiency)))
            self.max_power_w = max(self.base_power_w, float(max_power_w))
            self.powerOutMsg = messaging.PowerNodeUsageMsg()
            self.trace: list[ActivityPowerSample] = []

        def Reset(self, CurrentSimNanos: int) -> None:
            self.trace.clear()
            self.UpdateState(CurrentSimNanos)

        def UpdateState(self, CurrentSimNanos: int) -> None:
            enabled, enable_status, enable_error = _status_enabled(self.enableInMsg if self._has_enable else None, CurrentSimNanos)
            torque_payload, torque_status, torque_error = _read_payload(self.motorTorqueInMsg, CurrentSimNanos)
            speed_payload, speed_status, speed_error = _read_payload(self.wheelSpeedInMsg, CurrentSimNanos)
            mechanical_w = 0.0
            if torque_payload is not None and speed_payload is not None:
                torques = tuple(float(v) for v in torque_payload.motorTorque)
                speeds = tuple(float(v) for v in speed_payload.wheelSpeeds)
                mechanical_w = sum(abs(t * w) for t, w in zip(torques, speeds))
            statuses = (enable_status, torque_status, speed_status)
            status = InputReadStatus.ERROR if InputReadStatus.ERROR in statuses else (InputReadStatus.NOT_READY if InputReadStatus.NOT_READY in statuses else InputReadStatus.VALID)
            error = enable_error or torque_error or speed_error
            raw_power = self.base_power_w + mechanical_w / self.drive_efficiency
            power = min(self.max_power_w, raw_power) if enabled else 0.0
            activity = min(1.0, mechanical_w / max(1e-12, self.max_power_w))
            _write_power(self.powerOutMsg, power, CurrentSimNanos, self.moduleID)
            self.trace.append(ActivityPowerSample(CurrentSimNanos, enabled, activity, power, status, error))


    class StatusPowerBridge(sysModel.SysModel):
        """Convert a PDU/device enable status into a fixed electrical load.

        This is intended for loads whose physical activity is represented by an
        enable/disable state rather than a continuous rate, such as survival
        heaters.  It keeps the EPS and thermal paths on the same source message.
        """

        def __init__(
            self,
            model_tag: str,
            *,
            active_power_w: float,
            idle_power_w: float = 0.0,
            enable_status_msg: Any | None = None,
        ) -> None:
            super().__init__()
            self.ModelTag = model_tag
            self.active_power_w = max(0.0, float(active_power_w))
            self.idle_power_w = max(0.0, float(idle_power_w))
            self.enableInMsg = messaging.DeviceStatusMsgReader()
            self._has_enable = enable_status_msg is not None
            if enable_status_msg is not None:
                self.enableInMsg.subscribeTo(enable_status_msg)
            self.powerOutMsg = messaging.PowerNodeUsageMsg()
            self.trace: list[ActivityPowerSample] = []

        def Reset(self, CurrentSimNanos: int) -> None:
            self.trace.clear()
            self.UpdateState(CurrentSimNanos)

        def UpdateState(self, CurrentSimNanos: int) -> None:
            enabled, status, error = _status_enabled(self.enableInMsg if self._has_enable else None, CurrentSimNanos)
            power = self.active_power_w if enabled else self.idle_power_w
            _write_power(self.powerOutMsg, power, CurrentSimNanos, self.moduleID)
            self.trace.append(ActivityPowerSample(CurrentSimNanos, enabled, 1.0 if enabled else 0.0, power, status, error))


    class ThrusterActivityPowerBridge(sysModel.SysModel):
        """Convert native thruster output factors into propulsion electrical power."""

        def __init__(
            self,
            model_tag: str,
            *,
            thruster_output_msgs: Iterable[Any],
            active_power_per_thruster_w: float = 20.0,
            idle_power_w: float = 0.0,
            enable_status_msg: Any | None = None,
        ) -> None:
            super().__init__()
            self.ModelTag = model_tag
            self.thrusterInMsgs = []
            for msg in thruster_output_msgs:
                reader = messaging.THROutputMsgReader()
                reader.subscribeTo(msg)
                self.thrusterInMsgs.append(reader)
            self.enableInMsg = messaging.DeviceStatusMsgReader()
            self._has_enable = enable_status_msg is not None
            if enable_status_msg is not None:
                self.enableInMsg.subscribeTo(enable_status_msg)
            self.active_power_per_thruster_w = max(0.0, float(active_power_per_thruster_w))
            self.idle_power_w = max(0.0, float(idle_power_w))
            self.powerOutMsg = messaging.PowerNodeUsageMsg()
            self.trace: list[ActivityPowerSample] = []

        def Reset(self, CurrentSimNanos: int) -> None:
            self.trace.clear()
            self.UpdateState(CurrentSimNanos)

        def UpdateState(self, CurrentSimNanos: int) -> None:
            enabled, enable_status, enable_error = _status_enabled(self.enableInMsg if self._has_enable else None, CurrentSimNanos)
            factors: list[float] = []
            statuses = [enable_status]
            errors = [enable_error]
            for reader in self.thrusterInMsgs:
                payload, status, error = _read_payload(reader, CurrentSimNanos)
                statuses.append(status); errors.append(error)
                factors.append(min(1.0, max(0.0, float(payload.thrustFactor)))) if payload is not None else factors.append(0.0)
            status = InputReadStatus.ERROR if InputReadStatus.ERROR in statuses else (InputReadStatus.NOT_READY if InputReadStatus.NOT_READY in statuses else InputReadStatus.VALID)
            error = next((item for item in errors if item), None)
            activity = sum(factors)
            power = 0.0 if not enabled else self.idle_power_w + self.active_power_per_thruster_w * activity
            _write_power(self.powerOutMsg, power, CurrentSimNanos, self.moduleID)
            self.trace.append(ActivityPowerSample(CurrentSimNanos, enabled, activity, power, status, error))

    class PowerToThermalAggregationBridge(sysModel.SysModel):
        """Feed actual electrical activity into one Basilisk SensorThermal node.

        The bridge is used by the legacy unified runtime, whose official
        ``SensorThermal`` module exposes power draw as a mutable model parameter
        rather than an input message.  It executes immediately before the
        thermal module and derives heat only from PowerNodeUsage messages.
        """

        def __init__(
            self,
            model_tag: str,
            *,
            power_msgs: Iterable[Any],
            thermal_model: Any,
            heat_efficiencies: Iterable[float] | None = None,
            fixed_heat_w: float = 0.0,
        ) -> None:
            super().__init__()
            self.ModelTag = model_tag
            self.powerInMsgs = []
            for msg in power_msgs:
                reader = messaging.PowerNodeUsageMsgReader()
                reader.subscribeTo(msg)
                self.powerInMsgs.append(reader)
            efficiencies = tuple(float(v) for v in (heat_efficiencies or ()))
            if efficiencies and len(efficiencies) != len(self.powerInMsgs):
                raise ValueError("heat_efficiencies must match power_msgs length")
            self.heat_efficiencies = efficiencies or tuple(1.0 for _ in self.powerInMsgs)
            self.thermal_model = thermal_model
            self.fixed_heat_w = max(0.0, float(fixed_heat_w))
            self.trace: list[ActivityPowerSample] = []

        def Reset(self, CurrentSimNanos: int) -> None:
            self.trace.clear()
            self.UpdateState(CurrentSimNanos)

        def UpdateState(self, CurrentSimNanos: int) -> None:
            heat_w = self.fixed_heat_w
            electrical_w = 0.0
            statuses: list[InputReadStatus] = []
            errors: list[str | None] = []
            for reader, efficiency in zip(self.powerInMsgs, self.heat_efficiencies):
                payload, status, error = _read_payload(reader, CurrentSimNanos)
                statuses.append(status); errors.append(error)
                power_w = max(0.0, -float(payload.netPower)) if payload is not None else 0.0
                electrical_w += power_w
                heat_w += power_w * min(1.0, max(0.0, float(efficiency)))
            status = InputReadStatus.ERROR if InputReadStatus.ERROR in statuses else (InputReadStatus.NOT_READY if InputReadStatus.NOT_READY in statuses else InputReadStatus.VALID)
            error = next((item for item in errors if item), None)
            self.thermal_model.sensorPowerDraw = float(heat_w)
            self.trace.append(ActivityPowerSample(CurrentSimNanos, True, electrical_w, heat_w, status, error))


else:  # pragma: no cover
    DataActivityPowerBridge = DownlinkActivityPowerBridge = AdcsControlPowerBridge = StatusPowerBridge = ThrusterActivityPowerBridge = PowerToThermalAggregationBridge = None


__all__ = [
    "ActivityPowerSample",
    "InputReadStatus",
    "DataActivityPowerBridge",
    "DownlinkActivityPowerBridge",
    "AdcsControlPowerBridge",
    "StatusPowerBridge",
    "ThrusterActivityPowerBridge",
    "PowerToThermalAggregationBridge",
]
