"""Electrical Power Subsystem model.

The EPS subsystem composes single-unit component models:

    solar_panel -> PDU/load budget -> battery SOC/storage update

It is intentionally independent from Basilisk and suitable for subsystem-stage
power-balance validation.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from components.battery import initialize_battery, step_battery
from components.solar_panel.builder import SolarPanelState, step_solar_tracking
from components.pdu.builder import apply_load_shedding

from .power_budget import merge_requested_loads, mode_load_requests, served_loads_after_shed, total_power
from .schemas import EpsConfig, EpsProfileResult, EpsState, EpsStepInput, EpsStepResult


def initialize_eps_state(cfg: EpsConfig) -> EpsState:
    """Create EPS state from config."""

    return EpsState(
        battery=initialize_battery(cfg.battery),
        solar_panel=SolarPanelState(cfg.initial_panel_normal_b),
        time_s=0.0,
    )


def _apply_low_soc_preshed(loads_w: dict[str, float], cfg: EpsConfig, battery_soc: float) -> tuple[dict[str, float], tuple[str, ...]]:
    if cfg.low_soc_threshold <= 0.0 or battery_soc > cfg.low_soc_threshold:
        return dict(loads_w), ()
    shed: list[str] = []
    adjusted = dict(loads_w)
    for name in cfg.low_soc_shed_loads:
        if name in adjusted and adjusted[name] > 0.0:
            adjusted[name] = 0.0
            shed.append(name)
    return adjusted, tuple(shed)


def step_eps(state: EpsState, cfg: EpsConfig, step: EpsStepInput) -> tuple[EpsState, EpsStepResult]:
    """Advance EPS one step."""

    if step.dt_s <= 0.0:
        raise ValueError("EpsStepInput.dt_s must be positive")
    sun = step.sun_direction_b if step.sun_direction_b is not None else cfg.default_sun_direction_b
    solar_state, solar_power = step_solar_tracking(
        state.solar_panel,
        cfg.solar_panel,
        sun,
        step.shadow_factor,
        step.dt_s,
    )
    mode_loads = mode_load_requests(cfg.loads, step.mode)
    requested = merge_requested_loads(mode_loads, step.requested_loads_w)
    low_soc_adjusted, low_soc_shed = _apply_low_soc_preshed(requested, cfg, state.battery.soc)
    pdu_result = apply_load_shedding(low_soc_adjusted, cfg.pdu)
    shed = tuple(dict.fromkeys((*low_soc_shed, *pdu_result.shed)))
    served = served_loads_after_shed(requested, shed)
    # PDU may still have overload remaining if protected/remaining loads exceed bus.
    # Use PDU's demand_after_w as the electrical demand imposed on the battery bus.
    load_served_w = float(pdu_result.demand_after_w)
    net_power = float(solar_power) - load_served_w
    battery_state = step_battery(state.battery, cfg.battery, net_power, step.dt_s, step.battery_temp_c)
    shunt_dissipated_w = (battery_state.shunt_dissipated_wh - state.battery.shunt_dissipated_wh) * 3600 / step.dt_s if step.dt_s > 0 else 0.0
    next_state = EpsState(battery=battery_state, solar_panel=solar_state, time_s=state.time_s + step.dt_s)
    result = EpsStepResult(
        time_s=next_state.time_s,
        mode=step.mode,
        solar_power_w=float(solar_power),
        load_requested_w=total_power(requested),
        load_served_w=load_served_w,
        net_power_w=net_power,
        battery_soc=battery_state.soc,
        battery_storage_wh=battery_state.storage_wh,
        shunt_dissipated_w=shunt_dissipated_w,
        battery_temp_c=step.battery_temp_c,
        shed_loads=tuple(pdu_result.shed),
        low_soc_shed_loads=low_soc_shed,
        overload_remaining=bool(pdu_result.overload_remaining),
        load_request_by_name_w=requested,
        load_served_by_name_w=served,
    )
    return next_state, result


def simulate_eps_profile(initial_state: EpsState, cfg: EpsConfig, steps: Iterable[EpsStepInput]) -> tuple[EpsState, EpsProfileResult, tuple[EpsStepResult, ...]]:
    """Run a multi-step EPS profile."""

    state = initial_state
    rows: list[EpsStepResult] = []
    for step in steps:
        state, result = step_eps(state, cfg, step)
        rows.append(result)
    profile = EpsProfileResult(
        time_s=tuple(r.time_s for r in rows),
        solar_power_w=tuple(r.solar_power_w for r in rows),
        load_requested_w=tuple(r.load_requested_w for r in rows),
        load_served_w=tuple(r.load_served_w for r in rows),
        net_power_w=tuple(r.net_power_w for r in rows),
        battery_soc=tuple(r.battery_soc for r in rows),
        battery_storage_wh=tuple(r.battery_storage_wh for r in rows),
        shunt_dissipated_w=tuple(r.shunt_dissipated_w for r in rows),
        battery_temp_c=tuple(r.battery_temp_c for r in rows),
        shed_loads=tuple(r.shed_loads for r in rows),
        low_soc_shed_loads=tuple(r.low_soc_shed_loads for r in rows),
        overload_remaining=tuple(r.overload_remaining for r in rows),
    )
    return state, profile, tuple(rows)
