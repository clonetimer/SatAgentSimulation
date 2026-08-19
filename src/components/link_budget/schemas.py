"""Schema contracts for the link budget component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass

@dataclass(frozen=True)
class LinkBudgetConfig:
    raw_rate_bps: float = 1000.0
    tx_power_w: float = 1.0
    tx_gain_dbi: float = 0.0
    rx_gain_dbi: float = 0.0
    misc_loss_db: float = 0.0
    freq_hz: float = 2.2e9
    noise_temp_k: float = 500.0
    downlink_eff: float = 1.0


@dataclass(frozen=True)
class LinkBudgetNativeConfig:
    """Basilisk ``linkBudget.LinkBudget`` construction contract."""

    model_tag: str = "NativeLinkBudget"
    pointing_loss_enabled: bool = True
    frequency_loss_enabled: bool = True
    atmospheric_attenuation_enabled: bool = False


@dataclass(frozen=True)
class NativeRfLinkConfig:
    """Configuration for a two-antenna native RF-link harness."""

    model_tag: str = "NativeRfLink"
    spacecraft_radius_m: float = 7_000_000.0
    ground_radius_m: float = 6_378_000.0
    frequency_hz: float = 2.2e9
    bandwidth_hz: float = 1.0e6
    spacecraft_tx_power_w: float = 5.0
    ground_rx_power_w: float = 1.0
    spacecraft_directivity_db: float = 20.0
    ground_directivity_db: float = 20.0
    radiation_efficiency: float = 0.55
    equivalent_noise_temp_k: float = 290.0
    environment_temp_k: float = 290.0
    duration_s: float = 2.0
    step_s: float = 1.0
