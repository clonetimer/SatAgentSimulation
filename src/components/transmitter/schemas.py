"""Schema contracts for the transmitter component.

Configuration/specification dataclasses live here; builders only construct and wire runtime objects.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

@dataclass(frozen=True)
class TwtConfig:
    name: str = "TWT-1"
    max_tx_power_w: float = 100.0
    standby_power_w: float = 5.0
    efficiency: float = 0.65
    max_rate_bps: float = 2_000_000.0
    voltage_v: float = 5000.0
    mass_kg: float = 3.5
    life_hours: float = 120_000.0
    gain_ss_db: float = 50.0
    p_sat_w: float | None = None
    frequency_band: Literal["S", "C", "X", "Ku", "Ka", "Q", "V"] = "X"
    helix_voltage_v: float | None = None
    beam_current_ma: float | None = None

@dataclass(frozen=True)
class SspaConfig:
    name: str = "SSPA-1"
    max_tx_power_w: float = 12.0
    standby_power_w: float = 1.5
    efficiency: float = 0.45
    max_rate_bps: float = 500_000.0
    voltage_v: float = 28.0
    mass_kg: float = 0.8
    life_hours: float = 200_000.0
    gain_db: float = 35.0
    p_1db_w: float | None = None
    p_sat_w: float | None = None
    technology: Literal["GaAs", "GaN", "LDMOS"] = "GaN"
    frequency_band: Literal["S", "C", "X", "Ku", "Ka", "Q", "V"] = "X"

@dataclass(frozen=True)
class GenericAmpConfig:
    name: str = "Generic-Amp"
    max_tx_power_w: float = 10.0
    standby_power_w: float = 1.0
    efficiency: float = 0.50
    max_rate_bps: float = 1_000_000.0
    voltage_v: float = 28.0
    mass_kg: float = 1.0
    life_hours: float = 100_000.0
    gain_db: float = 30.0
    linearity_dB: float = 30.0

@dataclass
class RfAmplifierConfig:
    amp_type: Literal["twt", "sspa", "generic"] = "sspa"
    twt: TwtConfig | None = None
    sspa: SspaConfig | None = None
    generic: GenericAmpConfig | None = None
    input_power_w: float = 0.001
    min_input_power_w: float = 1e-6
    max_input_power_w: float = 0.1

    def get_effective_config(self) -> TwtConfig | SspaConfig | GenericAmpConfig:
        if self.amp_type == "twt":
            return self.twt or TwtConfig()
        elif self.amp_type == "sspa":
            return self.sspa or SspaConfig()
        return self.generic or GenericAmpConfig()

    def to_transmitter_config(self):
        if self.amp_type == "twt":
            cfg = self.twt or TwtConfig()
            return TransmitterConfig(
                max_tx_power_w=cfg.max_tx_power_w,
                standby_power_w=cfg.standby_power_w,
                efficiency=cfg.efficiency,
                max_rate_bps=cfg.max_rate_bps,
                amp_type="twt",
                voltage_v=cfg.voltage_v,
                mass_kg=cfg.mass_kg,
                life_hours=cfg.life_hours,
                gain_dB=cfg.gain_ss_db,
                p_sat_w=cfg.p_sat_w,
                input_power_w=self.input_power_w,
                min_input_power_w=self.min_input_power_w,
                max_input_power_w=self.max_input_power_w,
            )
        elif self.amp_type == "sspa":
            cfg = self.sspa or SspaConfig()
            return TransmitterConfig(
                max_tx_power_w=cfg.max_tx_power_w,
                standby_power_w=cfg.standby_power_w,
                efficiency=cfg.efficiency,
                max_rate_bps=cfg.max_rate_bps,
                amp_type="sspa",
                voltage_v=cfg.voltage_v,
                mass_kg=cfg.mass_kg,
                life_hours=cfg.life_hours,
                gain_dB=cfg.gain_db,
                p_1db_w=cfg.p_1db_w,
                p_sat_w=cfg.p_sat_w,
                input_power_w=self.input_power_w,
                min_input_power_w=self.min_input_power_w,
                max_input_power_w=self.max_input_power_w,
            )
        else:
            cfg = self.generic or GenericAmpConfig()
            return TransmitterConfig(
                max_tx_power_w=cfg.max_tx_power_w,
                standby_power_w=cfg.standby_power_w,
                efficiency=cfg.efficiency,
                max_rate_bps=cfg.max_rate_bps,
                amp_type="generic",
                voltage_v=cfg.voltage_v,
                mass_kg=cfg.mass_kg,
                life_hours=cfg.life_hours,
                gain_dB=cfg.gain_db,
                linearity_dB=cfg.linearity_dB,
                input_power_w=self.input_power_w,
                min_input_power_w=self.min_input_power_w,
                max_input_power_w=self.max_input_power_w,
            )

@dataclass(frozen=True)
class TransmitterConfig:
    max_tx_power_w: float = 12.0
    standby_power_w: float = 1.5
    efficiency: float = 0.55
    max_rate_bps: float = 1_000_000.0
    amp_type: Literal["twt", "sspa", "generic"] = "sspa"
    voltage_v: float = 28.0
    mass_kg: float = 1.0
    life_hours: float = 200000.0
    linearity_dB: float = 30.0
    gain_dB: float = 30.0
    input_power_w: float = 0.001
    min_input_power_w: float = 1e-6
    max_input_power_w: float = 0.1
    p_sat_w: float | None = None
    p_1db_w: float | None = None
    gain_compression_dB: float = 1.0
