"""RF Power Amplifier (TWT/SSPA) builder module.

This module provides configuration builders for TWT and SSPA amplifiers
following the standard builder pattern used in this project.

Usage:
    # Build TWT configuration
    cfg = build_twt_config(name="Ku-Band-TWT", frequency_band="Ku")

    # Build SSPA configuration
    cfg = build_sspa_config(name="Ka-Band-SSPA", technology="GaN")

    # Build unified amplifier
    cfg = build_rf_amplifier_config(amp_type="twt", twt=build_twt_config())
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import TwtConfig, SspaConfig, GenericAmpConfig, RfAmplifierConfig, TransmitterConfig
from .faults import apply_transmitter_faults
from .faults import FaultSpec

import math
from dataclasses import dataclass
from typing import Literal












@dataclass(frozen=True)
class TransmitterResult:
    transmitter_enabled: bool
    tx_power_w: float
    power_draw_w: float
    effective_rate_bps: float
    limit_violation: bool
    efficiency: float
    amp_type: str
    gain_dB: float
    input_power_w: float
    saturation_level_dB: float


def _twt_output_power(pin_w: float, psat_w: float, gain_ss_db: float) -> float:
    if pin_w <= 0:
        return 0.0
    gain_factor = 10 ** (gain_ss_db / 10.0)
    p_linear = pin_w * gain_factor
    x = p_linear / max(psat_w, 1e-9)
    p_saturated = psat_w * math.tanh(x)
    return min(p_saturated, psat_w)


def _sspa_output_power(pin_w: float, p1db_w: float, gain_db: float, psat_w: float | None) -> float:
    if pin_w <= 0:
        return 0.0
    gain_factor = 10 ** (gain_db / 10.0)
    p_linear = pin_w * gain_factor
    if psat_w is None:
        psat_w = p1db_w * 2.2
    p_sat = psat_w
    x = p_linear / max(p_sat, 1e-9)
    p_saturated = p_sat * (1 - math.exp(-x))
    return min(p_saturated, p_sat)


def _compute_efficiency_with_power(pout_w: float, pmax_w: float, efficiency: float, amp_type: str) -> float:
    power_ratio = pout_w / max(pmax_w, 1e-9)
    if amp_type == "twt":
        eff_factor = 0.6 + 0.4 * power_ratio
    elif amp_type == "sspa":
        eff_factor = 0.7 + 0.25 * power_ratio + 0.05 * math.sin(math.pi * power_ratio)
    else:
        eff_factor = 0.65 + 0.35 * power_ratio
    return min(efficiency * eff_factor, 0.95)


def compute_transmitter(commanded_on: bool, requested_rate_bps: float, available_power_w: float, config: TransmitterConfig) -> TransmitterResult:
    enabled = bool(commanded_on and available_power_w >= config.standby_power_w)
    if not enabled:
        return TransmitterResult(
            False, 0.0, 0.0, 0.0,
            limit_violation=bool(commanded_on),
            efficiency=0.0,
            amp_type=config.amp_type,
            gain_dB=config.gain_dB,
            input_power_w=0.0,
            saturation_level_dB=0.0,
        )

    pin = max(config.min_input_power_w, min(config.max_input_power_w, config.input_power_w))

    if config.amp_type == "twt":
        psat = config.p_sat_w or config.max_tx_power_w
        pout = _twt_output_power(pin, psat, config.gain_dB)
        saturation_db = 10 * math.log10(max(pout, 1e-12) / max(psat, 1e-9))
    elif config.amp_type == "sspa":
        p1db = config.p_1db_w or (config.max_tx_power_w * 0.5)
        psat = config.p_sat_w or (config.max_tx_power_w * 1.1)
        pout = _sspa_output_power(pin, p1db, config.gain_dB, psat)
        saturation_db = 10 * math.log10(max(pout, 1e-12) / max(psat, 1e-9))
    else:
        gain_factor = 10 ** (config.gain_dB / 10.0)
        pout = min(config.max_tx_power_w, pin * gain_factor)
        saturation_db = 10 * math.log10(
            max(pout, 1e-12) / max(config.max_tx_power_w, 1e-9)
        )

    actual_efficiency = _compute_efficiency_with_power(pout, config.max_tx_power_w, config.efficiency, config.amp_type)
    dc_power = pout / max(actual_efficiency, 1e-9)

    if config.amp_type == "twt":
        power_draw = dc_power + config.standby_power_w * 0.5
    else:
        power_draw = dc_power

    if power_draw > available_power_w:
        scale_factor = available_power_w / max(power_draw, 1e-9)
        tx_power = pout * scale_factor
        power_draw = available_power_w
        actual_efficiency = tx_power / max(dc_power, 1e-9)
        saturation_db *= scale_factor
    else:
        tx_power = pout

    rate = min(config.max_rate_bps, requested_rate_bps) * (tx_power / max(config.max_tx_power_w, 1e-9))
    return TransmitterResult(
        True, tx_power, power_draw, rate,
        limit_violation=rate < requested_rate_bps * 0.25,
        efficiency=actual_efficiency,
        amp_type=config.amp_type,
        gain_dB=config.gain_dB,
        input_power_w=pin,
        saturation_level_dB=saturation_db,
    )


def build_twt_config(
    name: str = "TWT-1",
    max_power_w: float = 100.0,
    efficiency: float = 0.65,
    gain_db: float = 50.0,
    frequency_band: Literal["S", "C", "X", "Ku", "Ka", "Q", "V"] = "X",
    input_power_w: float | None = None,
) -> TwtConfig:
    if not 1.0 <= max_power_w <= 1000.0:
        raise ValueError(f"TWT max_power_w must be 1-1000W, got {max_power_w}")
    if not 0.3 <= efficiency <= 0.75:
        raise ValueError(f"TWT efficiency must be 0.3-0.75, got {efficiency}")
    if not 30.0 <= gain_db <= 60.0:
        raise ValueError(f"TWT gain_db must be 30-60 dB, got {gain_db}")

    return TwtConfig(
        name=name,
        max_tx_power_w=max_power_w,
        efficiency=efficiency,
        gain_ss_db=gain_db,
        frequency_band=frequency_band,
        p_sat_w=max_power_w * 0.95,
    )


def build_sspa_config(
    name: str = "SSPA-1",
    max_power_w: float = 12.0,
    efficiency: float = 0.45,
    gain_db: float = 35.0,
    technology: Literal["GaAs", "GaN", "LDMOS"] = "GaN",
    frequency_band: Literal["S", "C", "X", "Ku", "Ka", "Q", "V"] = "X",
    input_power_w: float = 0.012,
) -> SspaConfig:
    if not 0.5 <= max_power_w <= 100.0:
        raise ValueError(f"SSPA max_power_w must be 0.5-100W, got {max_power_w}")
    if not 0.25 <= efficiency <= 0.60:
        raise ValueError(f"SSPA efficiency must be 0.25-0.60, got {efficiency}")
    if not 25.0 <= gain_db <= 55.0:
        raise ValueError(f"SSPA gain_db must be 25-55 dB, got {gain_db}")

    return SspaConfig(
        name=name,
        max_tx_power_w=max_power_w,
        efficiency=efficiency,
        gain_db=gain_db,
        technology=technology,
        frequency_band=frequency_band,
        p_1db_w=max_power_w * 0.5,
        p_sat_w=max_power_w * 1.1,
    )


def build_generic_amp_config(
    name: str = "Generic-Amp",
    max_power_w: float = 10.0,
    efficiency: float = 0.50,
    gain_db: float = 30.0,
) -> GenericAmpConfig:
    return GenericAmpConfig(
        name=name,
        max_tx_power_w=max_power_w,
        efficiency=efficiency,
        gain_db=gain_db,
    )


def build_rf_amplifier_config(
    amp_type: Literal["twt", "sspa", "generic"] = "sspa",
    twt: TwtConfig | None = None,
    sspa: SspaConfig | None = None,
    generic: GenericAmpConfig | None = None,
    input_power_w: float = 0.001,
    min_input_power_w: float = 1e-6,
    max_input_power_w: float = 0.1,
) -> RfAmplifierConfig:
    return RfAmplifierConfig(
        amp_type=amp_type,
        twt=twt,
        sspa=sspa,
        generic=generic,
        input_power_w=input_power_w,
        min_input_power_w=min_input_power_w,
        max_input_power_w=max_input_power_w,
    )


def _build_twt_transmitter_config_base_impl(
    name: str = "TWT-Transmitter",
    max_power_w: float = 100.0,
    efficiency: float = 0.65,
    gain_db: float = 50.0,
    frequency_band: Literal["S", "C", "X", "Ku", "Ka", "Q", "V"] = "X",
    input_power_w: float | None = None,
) -> TransmitterConfig:
    psat = max_power_w * 0.95
    if input_power_w is None:
        gain_factor = 10 ** (gain_db / 10.0)
        target_ratio = 0.92
        x_target = math.atanh(target_ratio)
        input_power_w = psat * x_target / gain_factor

    return TransmitterConfig(
        max_tx_power_w=max_power_w,
        standby_power_w=5.0,
        efficiency=efficiency,
        max_rate_bps=2_000_000.0,
        amp_type="twt",
        voltage_v=5000.0,
        mass_kg=3.5,
        life_hours=120_000.0,
        gain_dB=gain_db,
        p_sat_w=psat,
        input_power_w=input_power_w,
    )


def _build_sspa_transmitter_config_base_impl(
    name: str = "SSPA-Transmitter",
    max_power_w: float = 12.0,
    efficiency: float = 0.45,
    gain_db: float = 35.0,
    technology: Literal["GaAs", "GaN", "LDMOS"] = "GaN",
    input_power_w: float | None = None,
) -> TransmitterConfig:
    p1db = max_power_w * 0.5
    psat = max_power_w * 1.1
    if input_power_w is None:
        gain_factor = 10 ** (gain_db / 10.0)
        target_ratio = 0.90
        x_target = -math.log(1 - target_ratio)
        input_power_w = psat * x_target / gain_factor

    return TransmitterConfig(
        max_tx_power_w=max_power_w,
        standby_power_w=1.5,
        efficiency=efficiency,
        max_rate_bps=500_000.0,
        amp_type="sspa",
        voltage_v=28.0,
        mass_kg=0.8,
        life_hours=200_000.0,
        gain_dB=gain_db,
        p_1db_w=p1db,
        p_sat_w=psat,
        input_power_w=input_power_w,
    )


class RfAmplifierBuilder:
    def __init__(self):
        self._amp_type: Literal["twt", "sspa", "generic"] = "sspa"
        self._twt: TwtConfig | None = None
        self._sspa: SspaConfig | None = None
        self._generic: GenericAmpConfig | None = None
        self._input_power_w: float = 0.001
        self._min_input_power_w: float = 1e-6
        self._max_input_power_w: float = 0.1

    def with_twt(
        self,
        name: str = "TWT-1",
        max_power_w: float = 100.0,
        efficiency: float = 0.65,
        gain_db: float = 50.0,
        frequency_band: Literal["S", "C", "X", "Ku", "Ka", "Q", "V"] = "X",
    ) -> "RfAmplifierBuilder":
        self._amp_type = "twt"
        self._twt = build_twt_config(name, max_power_w, efficiency, gain_db, frequency_band)
        return self

    def with_sspa(
        self,
        name: str = "SSPA-1",
        max_power_w: float = 12.0,
        efficiency: float = 0.45,
        gain_db: float = 35.0,
        technology: Literal["GaAs", "GaN", "LDMOS"] = "GaN",
    ) -> "RfAmplifierBuilder":
        self._amp_type = "sspa"
        self._sspa = build_sspa_config(name, max_power_w, efficiency, gain_db, technology)
        return self

    def with_generic(
        self,
        name: str = "Generic-Amp",
        max_power_w: float = 10.0,
        efficiency: float = 0.50,
        gain_db: float = 30.0,
    ) -> "RfAmplifierBuilder":
        self._amp_type = "generic"
        self._generic = build_generic_amp_config(name, max_power_w, efficiency, gain_db)
        return self

    def with_input_power(
        self,
        input_power_w: float,
        min_power: float = 1e-6,
        max_power: float = 0.1,
    ) -> "RfAmplifierBuilder":
        self._input_power_w = input_power_w
        self._min_input_power_w = min_power
        self._max_input_power_w = max_power
        return self

    def build(self) -> RfAmplifierConfig:
        return build_rf_amplifier_config(
            amp_type=self._amp_type,
            twt=self._twt,
            sspa=self._sspa,
            generic=self._generic,
            input_power_w=self._input_power_w,
            min_input_power_w=self._min_input_power_w,
            max_input_power_w=self._max_input_power_w,
        )

    def build_transmitter_config(self) -> TransmitterConfig:
        return self.build().to_transmitter_config()


# Basilisk integration
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
        location='src/components/transmitter/builder.py:<module>:01',
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


class TransmitterBasilisk(_sysModel.SysModel if basilisk_available() else object):
    """Basilisk-scheduled transmitter model.

    This module:
    - Reads commanded transmission state
    - Reads available power from EPS
    - Computes RF transmission power draw
    - Writes transmitter status to downstream Comm/Data
    """

    def __init__(self, model_tag: str = "Transmitter", config: TransmitterConfig | None = None):
        if basilisk_available():
            super().__init__()
        self.ModelTag = model_tag
        self.config = config or TransmitterConfig()

        self.commanded_on = False
        self.requested_rate_bps = self.config.max_rate_bps
        self._last_result = TransmitterResult(
            transmitter_enabled=False,
            tx_power_w=0.0,
            power_draw_w=0.0,
            effective_rate_bps=0.0,
            limit_violation=False,
            efficiency=0.0,
            amp_type=self.config.amp_type,
            gain_dB=self.config.gain_dB,
            input_power_w=0.0,
            saturation_level_dB=0.0,
        )

        if basilisk_available():
            self.powerSinkInMsg = _messaging.PowerSinkMsgReader()
            self.transmitterStatusOutMsg = _messaging.TransmitterMsg()

    def Reset(self, CurrentSimNanos: int) -> None:
        """Reset transmitter state."""
        self.commanded_on = False
        self.requested_rate_bps = self.config.max_rate_bps
        self._last_result = TransmitterResult(
            transmitter_enabled=False,
            tx_power_w=0.0,
            power_draw_w=0.0,
            effective_rate_bps=0.0,
            limit_violation=False,
            efficiency=0.0,
            amp_type=self.config.amp_type,
            gain_dB=self.config.gain_dB,
            input_power_w=0.0,
            saturation_level_dB=0.0,
        )

    def UpdateState(self, CurrentSimNanos: int) -> None:
        """Execute one simulation step."""
        available_power_w = self._read_available_power()
        result = compute_transmitter(
            commanded_on=self.commanded_on,
            requested_rate_bps=self.requested_rate_bps,
            available_power_w=available_power_w,
            config=self.config,
        )
        self._last_result = result
        self._write_status()

    def _read_available_power(self) -> float:
        """Read available power from power sink input."""
        if not basilisk_available():
            return self.config.max_tx_power_w
        power_data = self.powerSinkInMsg()
        if power_data is None:
            return self.config.max_tx_power_w
        return float(power_data.power)

    def _write_status(self) -> None:
        """Write transmitter status to output message."""
        if not basilisk_available():
            return
        msg_payload = self.transmitterStatusOutMsg.zeroMsgPayload
        msg_payload.transmitterOn = self._last_result.transmitter_enabled
        msg_payload.txPowerUsed = self._last_result.tx_power_w
        msg_payload.dataRate = self._last_result.effective_rate_bps
        self.transmitterStatusOutMsg.write(msg_payload, CurrentSimNanos=0)

    def set_command(self, commanded_on: bool) -> None:
        """Set transmitter command state."""
        self.commanded_on = bool(commanded_on)

    def set_data_rate(self, rate_bps: float) -> None:
        """Set requested transmission data rate."""
        self.requested_rate_bps = max(0.0, float(rate_bps))

    @property
    def last_result(self) -> TransmitterResult:
        """Get last computation result."""
        return self._last_result

    @property
    def power_draw_w(self) -> float:
        """Get current power draw in watts."""
        return self._last_result.power_draw_w


def create_transmitter_basilisk(
    model_tag: str = "Transmitter",
    config: TransmitterConfig | None = None,
) -> TransmitterBasilisk:
    """Factory function to create a Basilisk transmitter."""
    require_basilisk()
    return TransmitterBasilisk(model_tag=model_tag, config=config)


def connect_power_sink(
    transmitter: TransmitterBasilisk,
    power_sink_msg_name: str,
) -> None:
    """Connect transmitter to power sink message."""
    if basilisk_available():
        transmitter.powerSinkInMsg.subscribeTo(
            _messaging.FindMessageHandle(power_sink_msg_name)
        )

# Component fault/degradation compatibility wrappers
from .degradation import TransmitterDegradation, TransmitterDegradationRate
from .degradation import apply_transmitter_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_twt_transmitter_config_base = _build_twt_transmitter_config_base_impl

def build_twt_transmitter_config(
    *args,
    degradation: TransmitterDegradation | None = None,
    degradation_rate: TransmitterDegradationRate | None = None,
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
    config = _build_twt_transmitter_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_transmitter_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_transmitter_faults(config, fault_specs)
    return config

_build_sspa_transmitter_config_base = _build_sspa_transmitter_config_base_impl

def build_sspa_transmitter_config(
    *args,
    degradation: TransmitterDegradation | None = None,
    degradation_rate: TransmitterDegradationRate | None = None,
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
    config = _build_sspa_transmitter_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_transmitter_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_transmitter_faults(config, fault_specs)
    return config
