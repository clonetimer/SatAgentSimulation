"""Schema contracts for the antenna component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AntennaConfig:
    """Project-facing antenna configuration.

    The legacy analytical fields remain for compatibility.  The ``native_*``
    fields are consumed by the Basilisk ``SimpleAntenna`` factory introduced in
    COMMDATA-RF-NATIVE-1.
    """

    # Legacy analytical antenna model fields.
    peak_gain_dbi: float = 8.0
    half_power_beamwidth_deg: float = 30.0
    max_pointing_loss_db: float = 18.0
    efficiency: float = 1.0

    # Basilisk SimpleAntenna native fields.
    native_frequency_hz: float = 2.2e9
    native_bandwidth_hz: float = 1.0e6
    native_directivity_db: float = 20.0
    native_hpbw_ratio: float = 1.0
    native_tx_power_w: float = 5.0
    native_rx_power_w: float = 1.0
    native_radiation_efficiency: float = 0.55
    native_equivalent_noise_temp_k: float = 290.0
    native_environment_temp_k: float = 290.0
    native_position_b_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    native_orientation_b: tuple[float, ...] = (0.0, 0.0, 0.0)
    native_available: bool = True
    native_operating_mode: str = "rxtx"
    native_use_haslam_map: bool = False


@dataclass(frozen=True)
class SimpleAntennaNativeConfig:
    """Basilisk ``simpleAntenna.SimpleAntenna`` construction contract."""

    model_tag: str = "SimpleAntenna"
    antenna_name: str = "antenna"
    frequency_hz: float = 2.2e9
    bandwidth_hz: float = 1.0e6
    directivity_db: float = 20.0
    hpbw_ratio: float = 1.0
    tx_power_w: float = 5.0
    rx_power_w: float = 1.0
    radiation_efficiency: float = 0.55
    equivalent_noise_temp_k: float = 290.0
    environment_temp_k: float = 290.0
    position_b_m: tuple[float, float, float] = (0.0, 0.0, 0.0)
    orientation_b: tuple[float, ...] = (0.0, 0.0, 0.0)
    available: bool = True
    operating_mode: str = "rxtx"
    use_haslam_map: bool = False
