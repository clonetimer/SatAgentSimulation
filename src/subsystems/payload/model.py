"""Payload subsystem model.

This module turns the payload instrument into a subsystem-level object with
explicit EPS, thermal and ADCS pointing gates.  It does not integrate the
payload into the whole-spacecraft runner; that belongs to spacecraft interface.
"""
from __future__ import annotations

from components.payload.builder import step_payload_instrument
from components.payload.builder import PayloadInstrumentStepInput

from .schemas import (
    PayloadSubsystemConfig,
    PayloadSubsystemProfileResult,
    PayloadSubsystemState,
    PayloadSubsystemStepInput,
    PayloadSubsystemStepResult,
)


def initialize_payload_state(cfg: PayloadSubsystemConfig | None = None) -> PayloadSubsystemState:
    return PayloadSubsystemState()


def _scaled_rate(cfg: PayloadSubsystemConfig, mode: str) -> float:
    scale = float(cfg.mode_data_rate_scale.get(mode, 1.0))
    return max(0.0, cfg.instrument.data_rate_bps * scale)


def step_payload_subsystem(
    state: PayloadSubsystemState,
    cfg: PayloadSubsystemConfig,
    step: PayloadSubsystemStepInput,
) -> tuple[PayloadSubsystemState, PayloadSubsystemStepResult]:
    if step.dt_s <= 0.0:
        raise ValueError("PayloadSubsystemStepInput.dt_s must be positive")
    instrument_step = PayloadInstrumentStepInput(
        dt_s=step.dt_s,
        mode=step.mode,
        requested=step.payload_requested,
        pointing_error_deg=step.pointing_error_deg,
        eps_allows=step.eps_allows_payload,
        thermal_allows=step.thermal_allows_payload,
        adcs_pointing_ready=step.adcs_pointing_ready,
        data_rate_bps_override=_scaled_rate(cfg, step.mode),
    )
    next_instr_state, instr = step_payload_instrument(state.instrument_state, cfg.instrument, instrument_step)
    next_state = PayloadSubsystemState(instrument_state=next_instr_state, time_s=state.time_s + step.dt_s)
    result = PayloadSubsystemStepResult(
        time_s=next_state.time_s,
        mode=step.mode,
        payload_enabled=instr.enabled,
        payload_power_w=instr.power_w,
        payload_heat_w=instr.heat_w,
        generated_bps=instr.generated_bps,
        generated_bits=instr.generated_bits,
        pointing_error_deg=float(step.pointing_error_deg),
        block_reasons=instr.block_reasons,
        cumulative_data_bits=instr.cumulative_data_bits,
        cumulative_energy_wh=instr.cumulative_energy_wh,
        cumulative_heat_j=instr.cumulative_heat_j,
    )
    return next_state, result


def simulate_payload_subsystem_profile(
    initial_state: PayloadSubsystemState,
    cfg: PayloadSubsystemConfig,
    steps,
) -> tuple[PayloadSubsystemState, PayloadSubsystemProfileResult, tuple[PayloadSubsystemStepResult, ...]]:
    state = initial_state
    rows: list[PayloadSubsystemStepResult] = []
    for step in steps:
        state, result = step_payload_subsystem(state, cfg, step)
        rows.append(result)
    profile = PayloadSubsystemProfileResult(
        time_s=tuple(r.time_s for r in rows),
        payload_enabled=tuple(r.payload_enabled for r in rows),
        payload_power_w=tuple(r.payload_power_w for r in rows),
        payload_heat_w=tuple(r.payload_heat_w for r in rows),
        generated_bps=tuple(r.generated_bps for r in rows),
        generated_bits=tuple(r.generated_bits for r in rows),
        cumulative_data_bits=tuple(r.cumulative_data_bits for r in rows),
        block_reasons=tuple(r.block_reasons for r in rows),
    )
    return state, profile, tuple(rows)
