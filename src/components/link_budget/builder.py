"""link_budget component builder module.

Provides both Python link budget model and Basilisk-native transmitter component factory.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic
from .schemas import LinkBudgetConfig, LinkBudgetNativeConfig
from .faults import apply_link_budget_faults
from .faults import FaultSpec

from dataclasses import dataclass
from math import erfc, log10, pi, sqrt

from ..dynamic_models import expand


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
        location='src/components/link_budget/builder.py:<module>:01',
        exception=exc,
        strict=False,
    )




@dataclass(frozen=True)
class LinkBudgetResult:
    effective_rate_bps: float
    ber: float
    ebn0_db: float
    fspl_db: float


@dataclass(frozen=True)
class LinkBudgetProfileResult:
    slant_range_m: tuple
    effective_rate_bps: tuple
    ebn0_db: tuple


def basilisk_available() -> bool:
    """Return True when Basilisk messaging/sysModel modules are available."""
    return _messaging is not None and _sysModel is not None


def compute_link_budget(c, slant_range_m):
    wl = 299792458.0 / c.freq_hz
    fspl = 20 * log10(4 * pi * max(1, slant_range_m) / wl)
    pr = 10 * log10(max(c.tx_power_w, 1e-12)) + c.tx_gain_dbi + c.rx_gain_dbi - fspl - c.misc_loss_db
    eb = pr - (-228.6 + 10 * log10(c.noise_temp_k)) - 10 * log10(max(1, c.raw_rate_bps))
    ber = max(0, min(1, 0.5 * erfc(sqrt(max(0, 10 ** (eb / 10))))))
    return LinkBudgetResult(c.raw_rate_bps * (1 - ber) * c.downlink_eff, ber, eb, fspl)


def simulate_range_profile(c, ranges):
    vals = [compute_link_budget(c, r) for r in ranges]
    return LinkBudgetProfileResult(tuple(ranges), tuple(v.effective_rate_bps for v in vals), tuple(v.ebn0_db for v in vals))


def _build_nominal_link_budget_config_base_impl(raw_rate_bps: float = 1000.0, tx_power_w: float = 1.0, tx_gain_dbi: float = 0.0, rx_gain_dbi: float = 0.0, misc_loss_db: float = 0.0, freq_hz: float = 2.2e9, noise_temp_k: float = 500.0, downlink_eff: float = 1.0, fault_specs: list[FaultSpec] | None = None) -> LinkBudgetConfig:
    """Build a nominal LinkBudgetConfig with default values."""
    return LinkBudgetConfig(
        raw_rate_bps=raw_rate_bps,
        tx_power_w=tx_power_w,
        tx_gain_dbi=tx_gain_dbi,
        rx_gain_dbi=rx_gain_dbi,
        misc_loss_db=misc_loss_db,
        freq_hz=freq_hz,
        noise_temp_k=noise_temp_k,
        downlink_eff=downlink_eff,
    )


def require_basilisk_transmitter() -> None:
    try:
        from Basilisk.simulation import simpleTransmitter  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk simpleTransmitter is unavailable: {exc}") from exc


def build_simple_transmitter(
    model_tag: str,
    baud_bps: float,
    data_name: str = "downlink",
    transmit_power_w: float | None = None,
    packet_size_bits: int = 0,
):
    """Create a Basilisk ``SimpleTransmitter`` communication component.

    Parameters
    ----------
    model_tag : str
        Module name for logging and debugging.
    baud_bps : float
        Transmission baud rate in bits per second.
    data_name : str, optional
        Name of the data stream (default "downlink").
    transmit_power_w : float, optional
        Transmit power in watts.
    packet_size_bits : int, optional
        Packet size in bits (default 0 for continuous).

    Returns
    -------
    simpleTransmitter.SimpleTransmitter
        A configured transmitter model.
    """
    if transmit_power_w is not None:
        raise ValueError(
            "Basilisk SimpleTransmitter 2.11.0 has no transmitPower field; "
            "configure RF power through SimpleAntenna/AntennaPower"
        )
    packet_size_bits = int(packet_size_bits)
    if packet_size_bits < 0:
        raise ValueError("packet_size_bits must be non-negative")

    require_basilisk_transmitter()
    from Basilisk.simulation import simpleTransmitter
    transmitter = simpleTransmitter.SimpleTransmitter()
    transmitter.ModelTag = model_tag
    transmitter.nodeBaudRate = max(0.0, float(baud_bps))
    transmitter.nodeDataName = str(data_name)
    transmitter.packetSize = packet_size_bits
    return transmitter

# Component fault/degradation compatibility wrappers
from .degradation import LinkBudgetDegradation, LinkBudgetDegradationRate
from .degradation import apply_link_budget_degradation, compute_degradation_state
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_link_budget_config_base = _build_nominal_link_budget_config_base_impl

def build_nominal_link_budget_config(
    *args,
    degradation: LinkBudgetDegradation | None = None,
    degradation_rate: LinkBudgetDegradationRate | None = None,
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
    config = _build_nominal_link_budget_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_link_budget_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_link_budget_faults(config, fault_specs)
    return config



# ---------------------------------------------------------------------------
# Basilisk native LinkBudget path (COMMDATA-RF-NATIVE-1)
# ---------------------------------------------------------------------------


def require_basilisk_link_budget() -> None:
    try:
        from Basilisk.simulation import linkBudget  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk linkBudget is unavailable: {exc}") from exc


def build_link_budget_native(config: LinkBudgetNativeConfig | None = None):
    """Create a Basilisk ``linkBudget.LinkBudget`` module."""
    require_basilisk_link_budget()
    from Basilisk.simulation import linkBudget

    cfg = config or LinkBudgetNativeConfig()
    module = linkBudget.LinkBudget()
    module.ModelTag = str(cfg.model_tag)
    module.pointingLoss = bool(cfg.pointing_loss_enabled)
    module.freqLoss = bool(cfg.frequency_loss_enabled)
    module.atmosAtt = bool(cfg.atmospheric_attenuation_enabled)
    return module


def wire_link_budget_antennas(link_budget_module, antenna_1, antenna_2) -> None:
    """Wire two native SimpleAntenna output logs into native LinkBudget."""
    try:
        link_budget_module.antennaInPayload_1.subscribeTo(antenna_1.antennaOutMsg)
        link_budget_module.antennaInPayload_2.subscribeTo(antenna_2.antennaOutMsg)
    except Exception as exc:
        raise RuntimeError("Native RF link wiring failed: antenna logs could not subscribe to LinkBudget") from exc
