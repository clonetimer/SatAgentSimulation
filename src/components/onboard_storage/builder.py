"""onboard_storage component builder module.

Provides both Python model config builders and Basilisk-native component factories.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import OnboardStorageConfig
from .faults import apply_onboard_storage_faults
from .faults import FaultSpec

from dataclasses import dataclass

# R26-R46A lightweight schema anchor; full pydantic schema remains future work.



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
        location='src/components/onboard_storage/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class OnboardStorageState:
    stored_bits: float = 0.0
    overflow_bits: float = 0.0
    downlinked_bits: float = 0.0


def step_onboard_storage(
    state: OnboardStorageState,
    config: OnboardStorageConfig,
    generated_bits: float,
    downlinked_bits: float,
) -> OnboardStorageState:
    available = max(0.0, state.stored_bits + generated_bits - downlinked_bits)
    overflow = max(0.0, available - config.capacity_bits)
    stored = min(config.capacity_bits, available)
    return OnboardStorageState(
        stored_bits=stored,
        overflow_bits=state.overflow_bits + overflow,
        downlinked_bits=state.downlinked_bits + max(downlinked_bits, 0.0),
    )


def _build_nominal_onboard_storage_config_base_impl(capacity_bits: float = 20_000_000.0, high_watermark: float = 0.9, fault_specs: list[FaultSpec] | None = None) -> OnboardStorageConfig:
    """Build a nominal OnboardStorageConfig with default values."""
    return OnboardStorageConfig(
        capacity_bits=capacity_bits,
        high_watermark=high_watermark,
    )


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def require_basilisk() -> None:
    """Raise RuntimeError if Basilisk modules are unavailable."""
    if not basilisk_available():
        raise RuntimeError("Basilisk messaging/sysModel modules are unavailable")


class OnboardStorageBasilisk(_sysModel.SysModel if basilisk_available() else object):
    """Basilisk-scheduled onboard storage model.

    This module:
    - Reads data generation from payload sensor
    - Reads downlink rate from comm
    - Computes storage level
    - Writes storage status
    """

    def __init__(self, model_tag: str = "OnboardStorage", config: OnboardStorageConfig | None = None):
        if basilisk_available():
            super().__init__()
        self.ModelTag = model_tag
        self.config = config or OnboardStorageConfig()

        self._state = OnboardStorageState()
        self._generated_bits = 0.0
        self._downlinked_bits = 0.0
        self._dt_s = 1.0

        if basilisk_available():
            self.dataGenInMsg = _messaging.DataNodeMsgReader()
            self.downlinkInMsg = _messaging.DataNodeMsgReader()
            self.storageStatusOutMsg = _messaging.StorageStatusMsg()

    def Reset(self, CurrentSimNanos: int) -> None:
        """Reset storage state."""
        self._state = OnboardStorageState()
        self._generated_bits = 0.0
        self._downlinked_bits = 0.0

    def UpdateState(self, CurrentSimNanos: int) -> None:
        """Execute one simulation step."""
        self._read_inputs()
        self._state = step_onboard_storage(
            state=self._state,
            config=self.config,
            generated_bits=self._generated_bits,
            downlinked_bits=self._downlinked_bits,
        )
        self._write_status()

    def _read_inputs(self) -> None:
        """Read data generation and downlink from messages."""
        if not basilisk_available():
            return

        try:
            gen_data = self.dataGenInMsg()
            if gen_data is not None and hasattr(gen_data, "dataRate"):
                self._generated_bits = float(gen_data.dataRate) * self._dt_s
        except Exception as exc:
            record_runtime_diagnostic(
                code='COMPONENT_INPUT_MESSAGE_READ_FAILED',
                category=DiagnosticCategory.MESSAGE_READ_FAILURE,
                location='src/components/onboard_storage/builder.py:_read_inputs:01',
                exception=exc,
                strict=None,
            )

        try:
            dlnk_data = self.downlinkInMsg()
            if dlnk_data is not None and hasattr(dlnk_data, "dataRate"):
                self._downlinked_bits = float(dlnk_data.dataRate) * self._dt_s
        except Exception as exc:
            record_runtime_diagnostic(
                code='COMPONENT_INPUT_MESSAGE_READ_FAILED',
                category=DiagnosticCategory.MESSAGE_READ_FAILURE,
                location='src/components/onboard_storage/builder.py:_read_inputs:02',
                exception=exc,
                strict=None,
            )

    def _write_status(self) -> None:
        """Write storage status to output message."""
        if not basilisk_available():
            return
        msg_payload = self.storageStatusOutMsg.zeroMsgPayload
        msg_payload.storedData = self._state.stored_bits
        msg_payload.dataCapacity = self.config.capacity_bits
        msg_payload.dataAvailable = self._state.stored_bits > 0
        self.storageStatusOutMsg.write(msg_payload, CurrentSimNanos=0)

    def set_data_generation(self, bits: float) -> None:
        """Set generated data bits (for standalone testing)."""
        self._generated_bits = float(bits)

    def set_downlink(self, bits: float) -> None:
        """Set downlinked bits (for standalone testing)."""
        self._downlinked_bits = float(bits)

    def set_dt(self, dt_s: float) -> None:
        """Set time step for standalone testing."""
        self._dt_s = float(dt_s)

    @property
    def state(self) -> OnboardStorageState:
        """Get current storage state."""
        return self._state

    @property
    def fill_ratio(self) -> float:
        """Get current fill ratio."""
        if self.config.capacity_bits <= 0:
            return 0.0
        return self._state.stored_bits / self.config.capacity_bits


def create_onboard_storage_basilisk(
    model_tag: str = "OnboardStorage",
    config: OnboardStorageConfig | None = None,
) -> OnboardStorageBasilisk:
    """Factory function to create a Basilisk onboard storage."""
    require_basilisk()
    return OnboardStorageBasilisk(model_tag=model_tag, config=config)

# Component fault/degradation compatibility wrappers
from .degradation import OnboardStorageDegradation, OnboardStorageDegradationRate
from .degradation import apply_onboard_storage_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_onboard_storage_config_base = _build_nominal_onboard_storage_config_base_impl

def build_nominal_onboard_storage_config(
    *args,
    degradation: OnboardStorageDegradation | None = None,
    degradation_rate: OnboardStorageDegradationRate | None = None,
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
    config = _build_nominal_onboard_storage_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_onboard_storage_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_onboard_storage_faults(config, fault_specs)
    return config

