"""Communication and data-handling subsystem model.

This subsystem composes single-unit component models:

    data generation -> data queue -> ground access -> link budget -> downlink

It is deliberately independent from Basilisk and does not propagate an orbit.
The caller supplies spacecraft position or an access override for each step.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

from typing import Iterable

from components.data_queue.builder import DataQueueState, step_data_queue
from components.link_budget.builder import compute_link_budget
from components.ground_station.builder import compute_ground_access
from components.transmitter.builder import compute_transmitter

from .schemas import (
    CommDataConfig,
    CommDataProfileResult,
    CommDataState,
    CommDataStepInput,
    CommDataStepResult,
)


def initialize_comm_data_state(cfg: CommDataConfig) -> CommDataState:
    """Create initial Comm/Data state."""

    return CommDataState(queue=DataQueueState(queue_bits=0.0), time_s=0.0)


def generation_rate_for_mode(cfg: CommDataConfig, mode: str, override_bps: float | None = None) -> float:
    """Return data generation rate for the current mode."""

    if override_bps is not None:
        return max(0.0, float(override_bps))
    return max(0.0, float(cfg.generated_bps_by_mode.get(mode, cfg.default_generated_bps)))


def _cap_downlink_rate(rate_bps: float, cfg: CommDataConfig) -> float:
    rate = max(0.0, float(rate_bps))
    if cfg.max_downlink_bps is not None:
        rate = min(rate, max(0.0, float(cfg.max_downlink_bps)))
    return rate


def step_comm_data(state: CommDataState, cfg: CommDataConfig, step: CommDataStepInput) -> tuple[CommDataState, CommDataStepResult]:
    """Advance Comm/Data one step."""

    if step.dt_s <= 0.0:
        raise ValueError("CommDataStepInput.dt_s must be positive")

    access = compute_ground_access(cfg.station_pos_ecef_m, step.spacecraft_pos_ecef_m, cfg.ground_station)
    has_access = bool(access.has_access if step.access_override is None else step.access_override)
    slant_range = float(step.slant_range_m if step.slant_range_m is not None else access.slant_range_m)
    generated_bps = generation_rate_for_mode(cfg, step.mode, step.generated_bps)

    allowed_by_access = has_access or not cfg.require_access_for_downlink
    allowed_by_eps = step.eps_allows_downlink or not cfg.require_eps_permission
    can_downlink = bool(step.downlink_requested and allowed_by_access and allowed_by_eps)

    available_tx_power = getattr(step, 'available_tx_power_w', 500.0)
    tx_result = compute_transmitter(
        can_downlink,
        cfg.link.raw_rate_bps,
        available_tx_power,
        cfg.transmitter,
    )

    link_cfg = cfg.link
    if tx_result.tx_power_w > 0:
        try:
            link_cfg = _replace_tx_power(cfg.link, tx_result.tx_power_w)
        except Exception as exc:
            record_runtime_diagnostic(
                code='COMM_TX_POWER_COMPATIBILITY_FALLBACK',
                category=DiagnosticCategory.INPUT_COMPATIBILITY_FALLBACK,
                location='src/subsystems/comm_data/model.py:step_comm_data:01',
                exception=exc,
                strict=False,
            )

    link = compute_link_budget(link_cfg, slant_range)

    downlink_rate = _cap_downlink_rate(link.effective_rate_bps, cfg) if can_downlink else 0.0

    prev_queue = state.queue
    next_queue = step_data_queue(prev_queue, cfg.queue, generated_bps, downlink_rate, step.dt_s)
    downlinked_step = next_queue.downlinked_bits - prev_queue.downlinked_bits
    dropped_step = next_queue.dropped_bits - prev_queue.dropped_bits
    next_state = CommDataState(queue=next_queue, time_s=state.time_s + step.dt_s)
    result = CommDataStepResult(
        time_s=next_state.time_s,
        mode=step.mode,
        generated_bps=generated_bps,
        generated_bits=generated_bps * step.dt_s,
        has_access=has_access,
        elevation_deg=float(access.elevation_deg),
        slant_range_m=slant_range,
        link_effective_rate_bps=float(link.effective_rate_bps),
        downlink_rate_bps=float(downlink_rate),
        downlinked_bits_step=float(downlinked_step),
        dropped_bits_step=float(dropped_step),
        queue_bits=float(next_queue.queue_bits),
        cumulative_downlinked_bits=float(next_queue.downlinked_bits),
        cumulative_dropped_bits=float(next_queue.dropped_bits),
        eps_allows_downlink=bool(step.eps_allows_downlink),
        downlink_requested=bool(step.downlink_requested),
        ebn0_db=float(link.ebn0_db),
        tx_power_w=float(tx_result.tx_power_w),
        tx_power_draw_w=float(tx_result.power_draw_w),
        tx_efficiency=float(tx_result.efficiency),
    )
    return next_state, result


def _replace_tx_power(link_cfg, new_tx_power_w: float):
    """Replace tx_power_w in a LinkBudgetConfig if possible."""
    from dataclasses import replace
    return replace(link_cfg, tx_power_w=new_tx_power_w)


def simulate_comm_data_profile(initial_state: CommDataState, cfg: CommDataConfig, steps: Iterable[CommDataStepInput]) -> tuple[CommDataState, CommDataProfileResult, tuple[CommDataStepResult, ...]]:
    """Run a multi-step Comm/Data profile."""

    state = initial_state
    rows: list[CommDataStepResult] = []
    for step in steps:
        state, result = step_comm_data(state, cfg, step)
        rows.append(result)
    profile = CommDataProfileResult(
        time_s=tuple(r.time_s for r in rows),
        queue_bits=tuple(r.queue_bits for r in rows),
        generated_bits=tuple(r.generated_bits for r in rows),
        downlinked_bits_step=tuple(r.downlinked_bits_step for r in rows),
        dropped_bits_step=tuple(r.dropped_bits_step for r in rows),
        cumulative_downlinked_bits=tuple(r.cumulative_downlinked_bits for r in rows),
        cumulative_dropped_bits=tuple(r.cumulative_dropped_bits for r in rows),
        has_access=tuple(r.has_access for r in rows),
        downlink_rate_bps=tuple(r.downlink_rate_bps for r in rows),
        link_effective_rate_bps=tuple(r.link_effective_rate_bps for r in rows),
        ebn0_db=tuple(r.ebn0_db for r in rows),
    )
    return state, profile, tuple(rows)
