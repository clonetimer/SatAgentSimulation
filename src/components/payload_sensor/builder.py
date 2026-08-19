"""payload_sensor component builder module.

Provides both Python model config/result dataclasses, pure computation
functions, and Basilisk-native SysModel wrapper classes.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import PayloadSensorConfig
from .faults import apply_payload_sensor_faults
from .faults import FaultSpec

from dataclasses import dataclass

# R26-R46A lightweight schema anchor; full pydantic schema remains future work.






@dataclass(frozen=True)
class PayloadSensorResult:
    valid_observation: bool
    generated_bits: float
    quality_score: float
    limit_violation: bool


def compute_payload_sensor(
    dt_s: float,
    pointing_error_deg: float,
    cloud_fraction: float,
    config: PayloadSensorConfig,
) -> PayloadSensorResult:
    pointing_quality = max(0.0, 1.0 - pointing_error_deg / max(config.max_pointing_error_deg, 1e-9))
    cloud_quality = max(0.0, 1.0 - cloud_fraction)
    quality = pointing_quality * cloud_quality
    valid = quality >= config.min_quality_score
    bits = config.nominal_data_rate_bps * max(dt_s, 0.0) * quality if valid else 0.0
    return PayloadSensorResult(
        valid_observation=valid,
        generated_bits=bits,
        quality_score=quality,
        limit_violation=not valid,
    )


def _build_nominal_payload_sensor_config_base_impl(nominal_data_rate_bps: float = 250_000.0, max_pointing_error_deg: float = 0.25, min_quality_score: float = 0.5, fault_specs: list[FaultSpec] | None = None) -> PayloadSensorConfig:
    """Build a nominal PayloadSensorConfig with default values."""
    return PayloadSensorConfig(
        nominal_data_rate_bps=nominal_data_rate_bps,
        max_pointing_error_deg=max_pointing_error_deg,
        min_quality_score=min_quality_score,
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
        location='src/components/payload_sensor/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def require_basilisk() -> None:
    """Raise RuntimeError if Basilisk modules are unavailable."""
    if not basilisk_available():
        raise RuntimeError("Basilisk messaging/sysModel modules are unavailable")


class PayloadSensorBasilisk(_sysModel.SysModel if basilisk_available() else object):
    """Basilisk-scheduled payload sensor model.

    This module:
    - Reads pointing error from ADCS
    - Reads cloud fraction from ground/thermal
    - Computes observation quality and data generation
    - Writes sensor status and data generation
    """

    def __init__(self, model_tag: str = "PayloadSensor", config: PayloadSensorConfig | None = None):
        if basilisk_available():
            super().__init__()
        self.ModelTag = model_tag
        self.config = config or PayloadSensorConfig()

        self._pointing_error_deg = 0.0
        self._cloud_fraction = 0.0
        self._dt_s = 1.0
        self._payload_enabled = True
        self._last_result = PayloadSensorResult(
            valid_observation=True,
            generated_bits=0.0,
            quality_score=1.0,
            limit_violation=False,
        )

        if basilisk_available():
            self.attitudeErrorInMsg = _messaging.AttMsgReader()
            self.environmentInMsg = _messaging.EnvMsgReader()
            self.payloadStatusOutMsg = _messaging.PayloadStatusMsg()
            self.dataGenOutMsg = _messaging.DataNodeMsg()

    def Reset(self, CurrentSimNanos: int) -> None:
        """Reset payload sensor state."""
        self._pointing_error_deg = 0.0
        self._cloud_fraction = 0.0
        self._payload_enabled = True
        self._last_result = PayloadSensorResult(
            valid_observation=True,
            generated_bits=0.0,
            quality_score=1.0,
            limit_violation=False,
        )

    def UpdateState(self, CurrentSimNanos: int) -> None:
        """Execute one simulation step."""
        self._read_inputs()
        result = compute_payload_sensor(
            dt_s=self._dt_s,
            pointing_error_deg=self._pointing_error_deg,
            cloud_fraction=self._cloud_fraction,
            config=self.config,
        )
        self._last_result = result
        self._write_outputs()

    def _read_inputs(self) -> None:
        """Read pointing error and cloud fraction from messages."""
        if not basilisk_available():
            return

        try:
            att_data = self.attitudeErrorInMsg()
            if att_data is not None and hasattr(att_data, "sigma_BN"):
                sigma = att_data.sigma_BN
                if hasattr(sigma, "__iter__"):
                    import math
                    self._pointing_error_deg = math.degrees(math.sqrt(sum(x*x for x in sigma)))
        except Exception as exc:
            record_runtime_diagnostic(
                code='COMPONENT_INPUT_MESSAGE_READ_FAILED',
                category=DiagnosticCategory.MESSAGE_READ_FAILURE,
                location='src/components/payload_sensor/builder.py:_read_inputs:01',
                exception=exc,
                strict=None,
            )

        try:
            env_data = self.environmentInMsg()
            if env_data is not None and hasattr(env_data, "cloudCover"):
                self._cloud_fraction = float(env_data.cloudCover)
        except Exception as exc:
            record_runtime_diagnostic(
                code='COMPONENT_INPUT_MESSAGE_READ_FAILED',
                category=DiagnosticCategory.MESSAGE_READ_FAILURE,
                location='src/components/payload_sensor/builder.py:_read_inputs:02',
                exception=exc,
                strict=None,
            )

    def _write_outputs(self) -> None:
        """Write sensor status and data generation to output messages."""
        if not basilisk_available():
            return

        status_msg = self.payloadStatusOutMsg.zeroMsgPayload
        status_msg.sensorOn = self._payload_enabled
        status_msg.validObservation = self._last_result.valid_observation
        status_msg.qualityScore = self._last_result.quality_score
        self.payloadStatusOutMsg.write(status_msg, CurrentSimNanos=0)

        data_msg = self.dataGenOutMsg.zeroMsgPayload
        data_msg.dataRate = self._last_result.generated_bits / max(self._dt_s, 1e-9)
        self.dataGenOutMsg.write(data_msg, CurrentSimNanos=0)

    def set_payload_enabled(self, enabled: bool) -> None:
        """Enable or disable payload (for mission mode control)."""
        self._payload_enabled = bool(enabled)

    def set_pointing_error(self, error_deg: float) -> None:
        """Set pointing error (for standalone testing)."""
        self._pointing_error_deg = float(error_deg)

    def set_cloud_fraction(self, fraction: float) -> None:
        """Set cloud fraction (for standalone testing)."""
        self._cloud_fraction = float(fraction)

    def set_dt(self, dt_s: float) -> None:
        """Set time step for standalone testing."""
        self._dt_s = float(dt_s)

    @property
    def last_result(self) -> PayloadSensorResult:
        """Get last computation result."""
        return self._last_result

    @property
    def data_rate_bps(self) -> float:
        """Get current data generation rate."""
        if self._dt_s <= 0:
            return 0.0
        return self._last_result.generated_bits / self._dt_s


def create_payload_sensor_basilisk(
    model_tag: str = "PayloadSensor",
    config: PayloadSensorConfig | None = None,
) -> PayloadSensorBasilisk:
    """Factory function to create a Basilisk payload sensor."""
    require_basilisk()
    return PayloadSensorBasilisk(model_tag=model_tag, config=config)

# Component fault/degradation compatibility wrappers
from .degradation import PayloadSensorDegradation, PayloadSensorDegradationRate
from .degradation import apply_payload_sensor_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_payload_sensor_config_base = _build_nominal_payload_sensor_config_base_impl

def build_nominal_payload_sensor_config(
    *args,
    degradation: PayloadSensorDegradation | None = None,
    degradation_rate: PayloadSensorDegradationRate | None = None,
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
    config = _build_nominal_payload_sensor_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_payload_sensor_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_payload_sensor_faults(config, fault_specs)
    return config

