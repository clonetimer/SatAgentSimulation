"""PDU component package exports.

The pure-Python configuration helpers are always importable.  Basilisk-scheduled
classes are exported only when the Basilisk architecture modules are available;
this keeps documentation, TaskSpec validation, and offline tooling lightweight.
"""
from __future__ import annotations

from .builder import (  # noqa: F401
    PduConfig,
    PduResult,
    apply_load_shedding,
    basilisk_available,
    build_nominal_pdu_config,
    require_basilisk,
    simulate_load_sequence,
)
from .faults import PDUFaultType  # noqa: F401
from .degradation import PDUDegradation, PDUDegradationRate  # noqa: F401

try:  # pragma: no cover - depends on Basilisk runtime
    from .builder import (  # type: ignore[attr-defined]  # noqa: F401
        ConstantDeviceRequest,
        PduDecisionTraceRow,
        PduLoadSheddingConfig,
        PduLoadSheddingSysModel,
        pdu_trace_to_dicts,
    )
except ImportError:  # pragma: no cover
    ConstantDeviceRequest = None  # type: ignore[assignment]
    PduDecisionTraceRow = None  # type: ignore[assignment]
    PduLoadSheddingConfig = None  # type: ignore[assignment]
    PduLoadSheddingSysModel = None  # type: ignore[assignment]

    def pdu_trace_to_dicts(*_args, **_kwargs):  # type: ignore[no-untyped-def]
        raise RuntimeError("Basilisk PDU trace helpers are unavailable")


__all__ = [
    "PduConfig",
    "PduResult",
    "apply_load_shedding",
    "basilisk_available",
    "build_nominal_pdu_config",
    "require_basilisk",
    "simulate_load_sequence",
    "ConstantDeviceRequest",
    "PduDecisionTraceRow",
    "PduLoadSheddingConfig",
    "PduLoadSheddingSysModel",
    "pdu_trace_to_dicts",
    "PDUFaultType",
    "PDUDegradation",
    "PDUDegradationRate",
]
