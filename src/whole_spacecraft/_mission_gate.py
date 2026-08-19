"""Whole-spacecraft model-layer helpers."""
from __future__ import annotations

from math import atan, pi, sqrt
from typing import Any

from .schemas import WholeSpacecraftMissionGateTraceRow

try:
    from Basilisk.architecture import messaging, sysModel  # noqa: F401
    from Basilisk.utilities import macros  # noqa: F401
except ImportError:
    messaging = None  # type: ignore[assignment]
    sysModel = None  # type: ignore[assignment]
    macros = None  # type: ignore[assignment]

_MissionGateBase = sysModel.SysModel if sysModel is not None else object


class WholeSpacecraftMissionGate(_MissionGateBase):
    """Basilisk-scheduled mission gate for payload generation and downlink.

    The gate is an integration bridge owned by the whole-spacecraft layer.  It
    may drive either the legacy fixed-baud SimpleTransmitter path or the newer
    native ODH path by publishing a LinkBudgetMsg consumed by
    ``downlinkHandling.DownlinkHandling``.  It does not implement RF or packet
    physics itself.
    """

    def __init__(
        self,
        *,
        battery_status_msg,
        thermal_status_msg,
        attitude_guid_msg,
        instrument,
        transmitter,
        payload_nominal_baud_bps: float,
        transmitter_nominal_baud_bps: float,
        access_window_s: float,
        access_period_s: float,
        max_pointing_error_deg: float,
        battery_enable_threshold: float = 0.45,
        downlink_link_budget_msg: Any | None = None,
        downlink_bit_rate_request_bps: float = 0.0,
        downlink_cnr_linear: float = 1.0e9,
        downlink_distance_m: float = 500_000.0,
        downlink_bandwidth_hz: float = 1.0e6,
        downlink_frequency_hz: float = 2.2e9,
        native_access_msg: Any | None = None,
        native_link_budget_source_msg: Any | None = None,
        native_link_budget_cnr_floor_linear: float = 0.0,
        payload_power_status_msg: Any | None = None,
        comm_power_status_msg: Any | None = None,
    ):
        if messaging is None or macros is None or sysModel is None:
            raise RuntimeError("Basilisk is required to instantiate WholeSpacecraftMissionGate")
        super().__init__()
        self.ModelTag = "wholeSpacecraftMissionGate"
        self.batteryStatusInMsg = messaging.PowerStorageStatusMsgReader()
        self.batteryStatusInMsg.subscribeTo(battery_status_msg)
        self.thermalStatusInMsg = messaging.DeviceStatusMsgReader()
        self.thermalStatusInMsg.subscribeTo(thermal_status_msg)
        self.attitudeGuidInMsg = messaging.AttGuidMsgReader()
        self.attitudeGuidInMsg.subscribeTo(attitude_guid_msg)
        self.nativeAccessInMsg = messaging.AccessMsgReader()
        self.native_access_enabled = native_access_msg is not None
        if native_access_msg is not None:
            self.nativeAccessInMsg.subscribeTo(native_access_msg)
        self.nativeLinkBudgetInMsg = messaging.LinkBudgetMsgReader()
        self.native_link_budget_enabled = native_link_budget_source_msg is not None
        if native_link_budget_source_msg is not None:
            self.nativeLinkBudgetInMsg.subscribeTo(native_link_budget_source_msg)
        self.payloadPowerStatusInMsg = messaging.DeviceStatusMsgReader()
        self.payload_power_status_enabled = payload_power_status_msg is not None
        if payload_power_status_msg is not None:
            self.payloadPowerStatusInMsg.subscribeTo(payload_power_status_msg)
        self.commPowerStatusInMsg = messaging.DeviceStatusMsgReader()
        self.comm_power_status_enabled = comm_power_status_msg is not None
        if comm_power_status_msg is not None:
            self.commPowerStatusInMsg.subscribeTo(comm_power_status_msg)
        self.instrument = instrument
        self.transmitter = transmitter
        self.payload_nominal_baud_bps = float(payload_nominal_baud_bps)
        self.transmitter_nominal_baud_bps = float(transmitter_nominal_baud_bps)
        self.access_window_s = float(access_window_s)
        self.access_period_s = float(access_period_s)
        self.max_pointing_error_deg = float(max_pointing_error_deg)
        self.battery_enable_threshold = float(battery_enable_threshold)
        self.downlink_link_budget_msg = downlink_link_budget_msg
        self.downlink_bit_rate_request_bps = max(0.0, float(downlink_bit_rate_request_bps))
        self.downlink_cnr_linear = max(0.0, float(downlink_cnr_linear))
        self.downlink_distance_m = max(0.0, float(downlink_distance_m))
        self.downlink_bandwidth_hz = max(0.0, float(downlink_bandwidth_hz))
        self.downlink_frequency_hz = max(0.0, float(downlink_frequency_hz))
        self.native_link_budget_cnr_floor_linear = max(0.0, float(native_link_budget_cnr_floor_linear))
        self.trace: list[WholeSpacecraftMissionGateTraceRow] = []

    def Reset(self, CurrentSimNanos: int) -> None:
        self.trace.clear()
        self.UpdateState(CurrentSimNanos)

    def _write_link_budget(self, *, has_access: bool, current_sim_nanos: int) -> None:
        if self.downlink_link_budget_msg is None:
            return
        source = None
        if self.native_link_budget_enabled:
            try:
                source = self.nativeLinkBudgetInMsg()
            except Exception:
                source = None
        payload = messaging.LinkBudgetMsgPayload()
        if source is not None:
            payload.antennaName1 = str(getattr(source, "antennaName1", "spacecraft") or "spacecraft")
            payload.antennaName2 = str(getattr(source, "antennaName2", "ground") or "ground")
            payload.distance = float(getattr(source, "distance", self.downlink_distance_m) or self.downlink_distance_m)
            payload.bandwidth = float(getattr(source, "bandwidth", self.downlink_bandwidth_hz) or self.downlink_bandwidth_hz)
            payload.frequency = float(getattr(source, "frequency", self.downlink_frequency_hz) or self.downlink_frequency_hz)
            cnr1 = max(0.0, float(getattr(source, "CNR1", 0.0) or 0.0))
            cnr2 = max(0.0, float(getattr(source, "CNR2", 0.0) or 0.0))
        else:
            payload.antennaName1 = "spacecraft"
            payload.antennaName2 = "ground"
            payload.distance = self.downlink_distance_m
            payload.bandwidth = self.downlink_bandwidth_hz
            payload.frequency = self.downlink_frequency_hz
            cnr1 = cnr2 = 0.0
        if has_access and source is not None:
            payload.antennaState1 = int(getattr(source, "antennaState1", 2))
            payload.antennaState2 = int(getattr(source, "antennaState2", 1))
        elif has_access:
            # Compatibility path: spacecraft antenna 1 transmits to ground
            # receiver antenna 2 using Basilisk AntennaStateEnum values.
            payload.antennaState1 = 2
            payload.antennaState2 = 1
        else:
            payload.antennaState1 = 0
            payload.antennaState2 = 0
        if has_access:
            if source is not None:
                # Preserve the native LinkBudget result.  A floor is applied only
                # when the caller explicitly configures one; the compatibility
                # fallback CNR must not silently overwrite real RF physics.
                cnr_floor = self.native_link_budget_cnr_floor_linear
            else:
                cnr_floor = max(self.downlink_cnr_linear, self.native_link_budget_cnr_floor_linear)
            payload.CNR1 = max(cnr1, cnr_floor)
            payload.CNR2 = max(cnr2, cnr_floor)
        else:
            payload.CNR1 = 0.0
            payload.CNR2 = 0.0
        self.downlink_link_budget_msg.write(payload, current_sim_nanos, self.moduleID)

    def UpdateState(self, CurrentSimNanos: int) -> None:
        time_s = float(CurrentSimNanos) * macros.NANO2SEC
        battery = self.batteryStatusInMsg()
        thermal = self.thermalStatusInMsg()
        attitude = self.attitudeGuidInMsg()

        storage_j = float(getattr(battery, "storageLevel", 0.0))
        capacity_j = float(getattr(battery, "storageCapacity", 0.0))
        battery_soc = storage_j / capacity_j if capacity_j > 0.0 else 0.0
        thermal_safe = bool(getattr(thermal, "deviceStatus", 1))
        sigma_br = [float(x) for x in getattr(attitude, "sigma_BR", [0.0, 0.0, 0.0])]
        attitude_error_norm = sqrt(sum(x * x for x in sigma_br))
        # Modified Rodrigues Parameters map to the principal rotation angle via
        # ||sigma|| = tan(phi/4).  The previous V11 bridge used
        # ``norm(sigma) * 180`` which mixed unit conversion and MRP geometry.
        attitude_error_deg = 4.0 * atan(attitude_error_norm) * 180.0 / pi
        if self.native_access_enabled:
            try:
                access = self.nativeAccessInMsg()
                geometric_access = bool(int(getattr(access, "hasAccess", 0)))
            except Exception:
                geometric_access = False
        else:
            phase_s = time_s % self.access_period_s if self.access_period_s > 0.0 else 0.0
            geometric_access = bool(phase_s <= self.access_window_s)
        pointing_ok = bool(attitude_error_deg <= self.max_pointing_error_deg)
        try:
            payload_power_available = (
                bool(self.payloadPowerStatusInMsg().deviceStatus)
                if self.payload_power_status_enabled else True
            )
        except Exception:
            payload_power_available = False
        try:
            comm_power_available = (
                bool(self.commPowerStatusInMsg().deviceStatus)
                if self.comm_power_status_enabled else True
            )
        except Exception:
            comm_power_available = False
        has_access = bool(geometric_access and pointing_ok and comm_power_available)
        payload_enabled = bool(
            battery_soc >= self.battery_enable_threshold
            and thermal_safe
            and pointing_ok
            and payload_power_available
        )

        self.instrument.nodeBaudRate = self.payload_nominal_baud_bps if payload_enabled else 0.0
        # The fixed-baud SimpleTransmitter path is kept for compatibility with
        # older coupling checks.  Native ODH closure uses DownlinkHandling, so
        # callers can pass transmitter_nominal_baud_bps=0 and use the link
        # budget message below as the active path.
        self.transmitter.nodeBaudRate = -self.transmitter_nominal_baud_bps if has_access else 0.0
        self._write_link_budget(has_access=has_access, current_sim_nanos=int(CurrentSimNanos))
        downlink_rate = self.downlink_bit_rate_request_bps if (has_access and self.downlink_link_budget_msg is not None) else (self.transmitter_nominal_baud_bps if has_access else 0.0)

        self.trace.append(
            WholeSpacecraftMissionGateTraceRow(
                time_s=time_s,
                payload_enabled=payload_enabled,
                has_access=has_access,
                thermal_safe=thermal_safe,
                battery_soc=battery_soc,
                attitude_error_norm=attitude_error_norm,
                attitude_error_deg=attitude_error_deg,
                payload_generated_bps=self.payload_nominal_baud_bps if payload_enabled else 0.0,
                downlink_requested_rate_bps=downlink_rate,
                # Compatibility alias for gate-requested rate.  The whole-
                # spacecraft trace replaces this with native delivered rate.
                downlink_rate_bps=downlink_rate,
                payload_power_available=payload_power_available,
                comm_power_available=comm_power_available,
            )
        )


__all__ = ["WholeSpacecraftMissionGate"]
