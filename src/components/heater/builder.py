"""heater component builder module.

Provides both Python model config builders and Basilisk-native component factories.

Basilisk integration notes
---------------------------
Basilisk does not have a native heater module; this provides a wrapper that:
- Reads node temperature from thermal subsystem
- Computes heater on/off based on setpoint and hysteresis
- Writes heater power request to EPS
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import HeaterConfig

from dataclasses import dataclass

from .degradation import HeaterDegradation
from .degradation import apply_heater_degradation
from .faults import apply_heater_faults
from .faults import FaultSpec
from .faults import HeaterFaultType

# R26-R46A lightweight schema anchor; full pydantic schema remains future work.






@dataclass(frozen=True)
class HeaterResult:
    heater_on: bool
    heater_power_w: float
    temperature_error_k: float
    limit_violation: bool


def step_heater(node_temp_k: float, config: HeaterConfig) -> HeaterResult:
    error = config.setpoint_k - node_temp_k
    heater_on = error > config.hysteresis_k
    power = config.max_power_w if heater_on else 0.0
    return HeaterResult(heater_on=heater_on, heater_power_w=power, temperature_error_k=error, limit_violation=False)


def _build_nominal_heater_config_base_impl(max_power_w: float = 25.0, setpoint_k: float = 285.0, hysteresis_k: float = 2.0, degradation: HeaterDegradation | None = None, fault_specs: list[FaultSpec] | None = None) -> HeaterConfig:
    """Build a nominal HeaterConfig with default values.

    Args:
        max_power_w: Maximum heater power in watts
        setpoint_k: Temperature setpoint in Kelvin
        hysteresis_k: Hysteresis band in Kelvin
        degradation: Optional degradation state to apply

    Returns:
        HeaterConfig: Nominal heater configuration
    """
    config = HeaterConfig(
        max_power_w=max_power_w,
        setpoint_k=setpoint_k,
        hysteresis_k=hysteresis_k,
    )
    if degradation is not None:
        config = apply_heater_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_heater_faults(config, fault_specs)
    return config


def apply_heater_config_faults(config: HeaterConfig, fault_specs: list[FaultSpec]) -> HeaterConfig:
    """Apply fault specifications to heater configuration."""
    from dataclasses import replace

    new_config = config

    for spec in fault_specs:
        if not isinstance(spec.fault_type, HeaterFaultType):
            continue

        if spec.fault_type == HeaterFaultType.Failure:
            new_config = replace(
                new_config,
                max_power_w=0.0
            )

        elif spec.fault_type == HeaterFaultType.OpenCircuit:
            new_config = replace(
                new_config,
                max_power_w=0.0
            )

        elif spec.fault_type == HeaterFaultType.Overheating:
            new_config = replace(
                new_config,
                setpoint_k=new_config.setpoint_k * (1.0 + spec.magnitude * 0.2)
            )

        elif spec.fault_type == HeaterFaultType.Stuck:
            if spec.magnitude > 0.5:
                new_config = replace(
                    new_config,
                    max_power_w=config.max_power_w
                )

    return new_config


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
        location='src/components/heater/builder.py:<module>:01',
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


class HeaterBasilisk(_sysModel.SysModel if basilisk_available() else object):
    """Basilisk-scheduled heater model.

    This module:
    - Reads node temperature from thermal node input
    - Computes heater on/off state based on setpoint
    - Writes heater power request to EPS

    Attributes
    ----------
    ModelTag : str
        Basilisk model name
    config : HeaterConfig
        Heater configuration
    node_temp_k : float
        Current node temperature (Kelvin)
    """

    def __init__(self, model_tag: str = "Heater", config: HeaterConfig | None = None):
        if basilisk_available():
            super().__init__()
        self.ModelTag = model_tag
        self.config = config or HeaterConfig()

        self.node_temp_k = self.config.setpoint_k
        self._last_result = HeaterResult(
            heater_on=False,
            heater_power_w=0.0,
            temperature_error_k=0.0,
            limit_violation=False,
        )

        if basilisk_available():
            self.thermalNodeInMsg = _messaging.ThermalNodeMsgReader()
            self.heaterPowerOutMsg = _messaging.HeaterPowerMsg()

    def Reset(self, CurrentSimNanos: int) -> None:
        """Reset heater state."""
        self.node_temp_k = self.config.setpoint_k
        self._last_result = HeaterResult(
            heater_on=False,
            heater_power_w=0.0,
            temperature_error_k=0.0,
            limit_violation=False,
        )

    def UpdateState(self, CurrentSimNanos: int) -> None:
        """Execute one simulation step."""
        self.node_temp_k = self._read_node_temperature()
        result = step_heater(
            node_temp_k=self.node_temp_k,
            config=self.config,
        )
        self._last_result = result
        self._write_power_request()

    def _read_node_temperature(self) -> float:
        """Read node temperature from thermal node input."""
        if not basilisk_available():
            return self.node_temp_k
        temp_data = self.thermalNodeInMsg()
        if temp_data is None:
            return self.node_temp_k
        if hasattr(temp_data, "temperature"):
            return float(temp_data.temperature)
        if hasattr(temp_data, "nodeTemperature"):
            return float(temp_data.nodeTemperature)
        return self.node_temp_k

    def _write_power_request(self) -> None:
        """Write heater power request to output message."""
        if not basilisk_available():
            return
        msg_payload = self.heaterPowerOutMsg.zeroMsgPayload
        msg_payload.heaterOn = self._last_result.heater_on
        msg_payload.heaterPower = self._last_result.heater_power_w
        self.heaterPowerOutMsg.write(msg_payload, CurrentSimNanos=0)

    def set_node_temperature(self, temp_k: float) -> None:
        """Set node temperature (for standalone testing)."""
        self.node_temp_k = float(temp_k)

    @property
    def last_result(self) -> HeaterResult:
        """Get last computation result."""
        return self._last_result

    @property
    def heater_power_w(self) -> float:
        """Get current heater power in watts."""
        return self._last_result.heater_power_w

    @property
    def is_on(self) -> bool:
        """Check if heater is currently on."""
        return self._last_result.heater_on


def create_heater_basilisk(
    model_tag: str = "Heater",
    config: HeaterConfig | None = None,
) -> HeaterBasilisk:
    """Factory function to create a Basilisk heater."""
    require_basilisk()
    return HeaterBasilisk(model_tag=model_tag, config=config)

# Component fault/degradation compatibility wrappers
from .degradation import HeaterDegradationRate
from .degradation import compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_heater_config_base = _build_nominal_heater_config_base_impl

def build_nominal_heater_config(
    *args,
    degradation: HeaterDegradation | None = None,
    degradation_rate: HeaterDegradationRate | None = None,
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
    config = _build_nominal_heater_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_heater_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_heater_faults(config, fault_specs)
    return config

