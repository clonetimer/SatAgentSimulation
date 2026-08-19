"""Payload subsystem schemas."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from components.payload.builder import PayloadInstrumentConfig, PayloadInstrumentState, PayloadInstrumentStepInput, PayloadInstrumentStepResult


@dataclass(frozen=True)
class PayloadSubsystemConfig:
    instrument: PayloadInstrumentConfig = field(default_factory=PayloadInstrumentConfig)
    science_modes: tuple[str, ...] = ("observation", "payload", "NOMINAL_OBSERVATION")
    mode_data_rate_scale: Mapping[str, float] = field(default_factory=lambda: {"observation": 1.0, "payload": 1.0, "NOMINAL_OBSERVATION": 1.0})
    standby_when_blocked: bool = True


@dataclass(frozen=True)
class PayloadBasiliskConfig:
    """Basilisk builder configuration for the payload subsystem."""

    duration_s: float = 60.0
    step_s: float = 1.0
    instrument_baud_bps: float = 250_000.0
    data_name: str = "payload_science"
    initial_mode: str = "observation"


@dataclass(frozen=True)
class PayloadSubsystemState:
    instrument_state: PayloadInstrumentState = field(default_factory=PayloadInstrumentState)
    time_s: float = 0.0


@dataclass(frozen=True)
class PayloadSubsystemStepInput:
    dt_s: float
    mode: str = "observation"
    payload_requested: bool = True
    pointing_error_deg: float = 0.0
    eps_allows_payload: bool = True
    thermal_allows_payload: bool = True
    adcs_pointing_ready: bool = True


@dataclass(frozen=True)
class PayloadSubsystemStepResult:
    time_s: float
    mode: str
    payload_enabled: bool
    payload_power_w: float
    payload_heat_w: float
    generated_bps: float
    generated_bits: float
    pointing_error_deg: float
    block_reasons: tuple[str, ...]
    cumulative_data_bits: float
    cumulative_energy_wh: float
    cumulative_heat_j: float


@dataclass(frozen=True)
class PayloadSubsystemProfileResult:
    time_s: tuple[float, ...]
    payload_enabled: tuple[bool, ...]
    payload_power_w: tuple[float, ...]
    payload_heat_w: tuple[float, ...]
    generated_bps: tuple[float, ...]
    generated_bits: tuple[float, ...]
    cumulative_data_bits: tuple[float, ...]
    block_reasons: tuple[tuple[str, ...], ...]
