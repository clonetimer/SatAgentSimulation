"""pdu component builder module.

Provides both Python model config builders and Basilisk-native component factories.

Basilisk integration notes
---------------------------
This is a Basilisk Python module, not a Python-only post-processor.  The PDU
inherits ``_sysModel.SysModel``, is added to a Basilisk task, reads Basilisk
architecture messages, and writes ``DeviceStatusMsg`` outputs that can enable or
shed downstream load nodes.

It is intentionally classified as a Basilisk-scheduled custom module rather than
a native Basilisk EPS/PDU C++ module.  The current wheel environment supports
running this module inside ``SimulationBaseClass`` without requiring a source
build/SWIG workflow.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import PduConfig, PduLoadSheddingConfig
from .faults import apply_pdu_faults
from .faults import FaultSpec

from dataclasses import dataclass, asdict
from typing import Dict




@dataclass(frozen=True)
class PduResult:
    demand_after_w: float
    shed: tuple
    overload_remaining: bool


def apply_load_shedding(loads, c):
    active = dict(loads)
    demand = sum(max(0, float(v)) for v in active.values())
    shed = []
    for name in c.shed_order:
        if demand <= c.bus_max_w:
            break
        if name in active:
            demand -= max(0, float(active.pop(name)))
            shed.append(name)
    return PduResult(demand, tuple(shed), demand > c.bus_max_w)


def simulate_load_sequence(seq, c):
    return tuple(apply_load_shedding(x, c) for x in seq)


def _build_nominal_pdu_config_base_impl(
    bus_max_w: float = 50.0,
    shed_order: tuple = ("payload", "comm"),
    fault_specs: list[FaultSpec] | None = None,
) -> PduConfig:
    """Build a nominal PduConfig with default values."""
    return PduConfig(
        bus_max_w=bus_max_w,
        shed_order=shed_order,
    )


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
        location='src/components/pdu/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )


def basilisk_available() -> bool:
    """Return True when Basilisk PDU modules are available."""
    return _messaging is not None and _sysModel is not None


def require_basilisk() -> None:
    """Raise RuntimeError if Basilisk PDU modules are unavailable."""
    if not basilisk_available():
        raise RuntimeError("Basilisk messaging/sysModel modules are unavailable")


if basilisk_available():
    @dataclass(frozen=True)
    class PduDecisionTraceRow:
        time_s: float
        soc: float
        payload_requested: bool
        adcs_requested: bool
        comm_requested: bool
        heater_requested: bool
        payload_enabled: bool
        adcs_enabled: bool
        comm_enabled: bool
        heater_enabled: bool
        load_shed_active: bool
        shed_reason: str


    class ConstantDeviceRequest(_sysModel.SysModel):
        """Basilisk-scheduled fixed DeviceCmd message source."""

        def __init__(self, model_tag: str, requested: bool = True):
            super().__init__()
            self.ModelTag = model_tag
            self.requested = bool(requested)
            self.deviceCmdOutMsg = _messaging.DeviceCmdMsg()

        def Reset(self, CurrentSimNanos: int) -> None:
            self.UpdateState(CurrentSimNanos)

        def UpdateState(self, CurrentSimNanos: int) -> None:
            payload = _messaging.DeviceCmdMsgPayload()
            payload.deviceCmd = int(self.requested)
            self.deviceCmdOutMsg.write(payload, CurrentSimNanos, self.moduleID)


    class PduLoadSheddingSysModel(_sysModel.SysModel):
        """Basilisk Python SysModel implementing PDU/load-shedding policy."""

        def __init__(self, config: PduLoadSheddingConfig | None = None):
            super().__init__()
            self.ModelTag = "pduLoadSheddingSysModel"
            self.config = config or PduLoadSheddingConfig()
            self.batteryStatusInMsg = _messaging.PowerStorageStatusMsgReader()
            self.payloadRequestInMsg = _messaging.DeviceCmdMsgReader()
            self.adcsRequestInMsg = _messaging.DeviceCmdMsgReader()
            self.commRequestInMsg = _messaging.DeviceCmdMsgReader()
            self.heaterRequestInMsg = _messaging.DeviceCmdMsgReader()
            self.payloadStatusOutMsg = _messaging.DeviceStatusMsg()
            self.adcsStatusOutMsg = _messaging.DeviceStatusMsg()
            self.commStatusOutMsg = _messaging.DeviceStatusMsg()
            self.heaterStatusOutMsg = _messaging.DeviceStatusMsg()
            self.trace: list[PduDecisionTraceRow] = []

        def _soc(self) -> float:
            try:
                status = self.batteryStatusInMsg()
            except Exception:
                return max(0.0, min(1.0, float(self.config.fallback_initial_soc)))
            capacity = float(getattr(status, "storageCapacity", 0.0))
            level = float(getattr(status, "storageLevel", 0.0))
            if capacity <= 0.0:
                return max(0.0, min(1.0, float(self.config.fallback_initial_soc)))
            return max(0.0, min(1.0, level / capacity))

        @staticmethod
        def _requested(reader: _messaging.DeviceCmdMsgReader) -> bool:
            try:
                return bool(reader().deviceCmd)
            except Exception:
                return False

        @staticmethod
        def _write_status(out_msg: _messaging.DeviceStatusMsg, enabled: bool, t: int, module_id: int) -> None:
            payload = _messaging.DeviceStatusMsgPayload()
            payload.deviceStatus = int(enabled)
            out_msg.write(payload, t, module_id)

        def Reset(self, CurrentSimNanos: int) -> None:
            self.trace.clear()
            self.UpdateState(CurrentSimNanos)

        def UpdateState(self, CurrentSimNanos: int) -> None:
            from Basilisk.utilities import macros

            soc = self._soc()
            payload_req = self._requested(self.payloadRequestInMsg)
            adcs_req = self._requested(self.adcsRequestInMsg)
            comm_req = self._requested(self.commRequestInMsg)
            heater_req = self._requested(self.heaterRequestInMsg)

            payload_enabled = payload_req and soc >= self.config.payload_min_soc
            comm_enabled = comm_req and soc >= self.config.comm_min_soc
            heater_enabled = heater_req and soc >= self.config.heater_min_soc
            adcs_enabled = adcs_req and soc >= self.config.adcs_min_soc

            shed_reasons = []
            if payload_req and not payload_enabled:
                shed_reasons.append("payload_low_soc")
            if comm_req and not comm_enabled:
                shed_reasons.append("comm_low_soc")
            if heater_req and not heater_enabled:
                shed_reasons.append("heater_low_soc")
            if adcs_req and not adcs_enabled:
                shed_reasons.append("adcs_low_soc")
            if soc < self.config.recovery_soc and shed_reasons:
                shed_reasons.append("charge_recovery_required")
            reason = "none" if not shed_reasons else "|".join(shed_reasons)

            self._write_status(self.payloadStatusOutMsg, payload_enabled, CurrentSimNanos, self.moduleID)
            self._write_status(self.adcsStatusOutMsg, adcs_enabled, CurrentSimNanos, self.moduleID)
            self._write_status(self.commStatusOutMsg, comm_enabled, CurrentSimNanos, self.moduleID)
            self._write_status(self.heaterStatusOutMsg, heater_enabled, CurrentSimNanos, self.moduleID)
            self.trace.append(PduDecisionTraceRow(
                time_s=float(CurrentSimNanos) * macros.NANO2SEC,
                soc=soc,
                payload_requested=payload_req,
                adcs_requested=adcs_req,
                comm_requested=comm_req,
                heater_requested=heater_req,
                payload_enabled=payload_enabled,
                adcs_enabled=adcs_enabled,
                comm_enabled=comm_enabled,
                heater_enabled=heater_enabled,
                load_shed_active=bool(shed_reasons),
                shed_reason=reason,
            ))


    def pdu_trace_to_dicts(trace: list[PduDecisionTraceRow]) -> list[Dict[str, object]]:
        return [asdict(row) for row in trace]

# Component fault/degradation compatibility wrappers
from .degradation import PDUDegradation, PDUDegradationRate
from .degradation import apply_pdu_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_pdu_config_base = _build_nominal_pdu_config_base_impl

def build_nominal_pdu_config(
    *args,
    degradation: PDUDegradation | None = None,
    degradation_rate: PDUDegradationRate | None = None,
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
    config = _build_nominal_pdu_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_pdu_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_pdu_faults(config, fault_specs)
    return config

