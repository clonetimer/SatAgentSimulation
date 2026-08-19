"""radiator component builder module."""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import RadiatorConfig

from dataclasses import dataclass

from .faults import apply_radiator_faults
from .faults import FaultSpec
from .degradation import RadiatorDegradation

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
        location='src/components/radiator/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class RadiatorResult:
    rejected_heat_w: float
    radiator_margin_w: float
    limit_violation: bool


def compute_radiator_rejection(node_temp_k: float, heat_load_w: float, config: RadiatorConfig) -> RadiatorResult:
    sigma = 5.670374419e-8
    raw = config.emissivity * sigma * config.area_m2 * max(node_temp_k ** 4 - config.effective_sink_temp_k ** 4, 0.0)
    rejected = min(config.max_rejection_w, raw)
    margin = rejected - heat_load_w
    return RadiatorResult(rejected_heat_w=rejected, radiator_margin_w=margin, limit_violation=margin < 0.0)


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def require_basilisk() -> None:
    """Raise RuntimeError if Basilisk modules are unavailable."""
    if not basilisk_available():
        raise RuntimeError("Basilisk messaging/sysModel modules are unavailable")


class RadiatorBasilisk(_sysModel.SysModel if basilisk_available() else object):
    """Basilisk-scheduled radiator thermal model.

    This module:
    - Reads node temperature from thermal input
    - Reads heat load from thermal node
    - Computes radiator heat rejection using Stefan-Boltzmann
    - Writes radiator status to thermal subsystem

    Attributes
    ----------
    ModelTag : str
        Basilisk model name
    config : RadiatorConfig
        Radiator configuration
    """

    def __init__(self, model_tag: str = "Radiator", config: RadiatorConfig | None = None):
        if basilisk_available():
            super().__init__()
        self.ModelTag = model_tag
        self.config = config or RadiatorConfig()

        self._node_temp_k = self.config.effective_sink_temp_k + 50.0
        self._heat_load_w = 0.0
        self._last_result = RadiatorResult(
            rejected_heat_w=0.0,
            radiator_margin_w=0.0,
            limit_violation=False,
        )

        if basilisk_available():
            self.thermalNodeInMsg = _messaging.ThermalNodeMsgReader()
            self.heatLoadInMsg = _messaging.PowerMsgReader()
            self.radiatorStatusOutMsg = _messaging.RadiatorMsg()

    def Reset(self, CurrentSimNanos: int) -> None:
        """Reset radiator state."""
        self._node_temp_k = self.config.effective_sink_temp_k + 50.0
        self._heat_load_w = 0.0
        self._last_result = RadiatorResult(
            rejected_heat_w=0.0,
            radiator_margin_w=0.0,
            limit_violation=False,
        )

    def UpdateState(self, CurrentSimNanos: int) -> None:
        """Execute one simulation step."""
        self._read_thermal_inputs()
        result = compute_radiator_rejection(
            node_temp_k=self._node_temp_k,
            heat_load_w=self._heat_load_w,
            config=self.config,
        )
        self._last_result = result
        self._write_status()

    def _read_thermal_inputs(self) -> None:
        """Read thermal inputs from messages."""
        if not basilisk_available():
            return
        
        try:
            temp_data = self.thermalNodeInMsg()
            if temp_data is not None:
                if hasattr(temp_data, "temperature"):
                    self._node_temp_k = float(temp_data.temperature)
                elif hasattr(temp_data, "nodeTemperature"):
                    self._node_temp_k = float(temp_data.nodeTemperature)
        except Exception as exc:
            record_runtime_diagnostic(
                code='COMPONENT_INPUT_MESSAGE_READ_FAILED',
                category=DiagnosticCategory.MESSAGE_READ_FAILURE,
                location='src/components/radiator/builder.py:_read_thermal_inputs:01',
                exception=exc,
                strict=None,
            )
        
        try:
            power_data = self.heatLoadInMsg()
            if power_data is not None and hasattr(power_data, "power"):
                self._heat_load_w = float(power_data.power)
        except Exception as exc:
            record_runtime_diagnostic(
                code='COMPONENT_INPUT_MESSAGE_READ_FAILED',
                category=DiagnosticCategory.MESSAGE_READ_FAILURE,
                location='src/components/radiator/builder.py:_read_thermal_inputs:02',
                exception=exc,
                strict=None,
            )

    def _write_status(self) -> None:
        """Write radiator status to output message."""
        if not basilisk_available():
            return
        msg_payload = self.radiatorStatusOutMsg.zeroMsgPayload
        msg_payload.rejectedHeat = self._last_result.rejected_heat_w
        msg_payload.radiatorMargin = self._last_result.radiator_margin_w
        self.radiatorStatusOutMsg.write(msg_payload, CurrentSimNanos=0)

    def set_node_temperature(self, temp_k: float) -> None:
        """Set node temperature (for standalone testing)."""
        self._node_temp_k = float(temp_k)

    def set_heat_load(self, load_w: float) -> None:
        """Set heat load (for standalone testing)."""
        self._heat_load_w = float(load_w)

    @property
    def last_result(self) -> RadiatorResult:
        """Get last computation result."""
        return self._last_result

    @property
    def rejected_heat_w(self) -> float:
        """Get current rejected heat in watts."""
        return self._last_result.rejected_heat_w


def create_radiator_basilisk(
    model_tag: str = "Radiator",
    config: RadiatorConfig | None = None,
) -> RadiatorBasilisk:
    """Factory function to create a Basilisk radiator."""
    require_basilisk()
    return RadiatorBasilisk(model_tag=model_tag, config=config)


def _build_nominal_radiator_config_base_impl(area_m2: float = 0.35, emissivity: float = 0.82, effective_sink_temp_k: float = 250.0, max_rejection_w: float = 180.0, degradation: RadiatorDegradation | None = None, fault_specs: list[FaultSpec] | None = None) -> RadiatorConfig:
    """Build a nominal RadiatorConfig with all parameters defaulted."""
    config = RadiatorConfig(
        area_m2=area_m2,
        emissivity=emissivity,
        effective_sink_temp_k=effective_sink_temp_k,
        max_rejection_w=max_rejection_w,
    )
    if degradation is not None:
        from dataclasses import replace
        config = replace(
            config,
            emissivity=config.emissivity * (1.0 - degradation.emissivity_loss_pct / 100.0),
            max_rejection_w=config.max_rejection_w * (1.0 - degradation.heat_transfer_coefficient_loss_pct / 100.0),
        )
    return config

# Component fault/degradation compatibility wrappers
from .degradation import RadiatorDegradationRate
from .degradation import apply_radiator_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_radiator_config_base = _build_nominal_radiator_config_base_impl

def build_nominal_radiator_config(
    *args,
    degradation: RadiatorDegradation | None = None,
    degradation_rate: RadiatorDegradationRate | None = None,
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
    config = _build_nominal_radiator_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_radiator_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_radiator_faults(config, fault_specs)
    return config

