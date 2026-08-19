"""Propulsion subsystem model.

The v3.2 propulsion subsystem composes thruster and fuel tank component models:

    burn request -> EPS/SOC/fuel permission -> thruster on-time -> impulse
    -> propellant consumption -> tank mass/pressure update

This is still subsystem-stage logic.  It does not propagate an orbit or apply
forces to spacecraft dynamics; those belong to a later Basilisk bridge and
mission/spacecraft stage.
"""
from __future__ import annotations

from typing import Iterable

from components.thruster import compute_thruster_pulse, shape_thruster_on_time
from components.fuel_tank import initialize_fuel_tank, step_fuel_tank

from .schemas import (
    PropulsionConfig,
    PropulsionProfileResult,
    PropulsionState,
    PropulsionStepInput,
    PropulsionStepResult,
)


def initialize_propulsion_state(cfg: PropulsionConfig) -> PropulsionState:
    """Create propulsion state from config."""

    return PropulsionState(fuel_tank=initialize_fuel_tank(cfg.fuel_tank), time_s=0.0)


def _inhibition_reason(state: PropulsionState, cfg: PropulsionConfig, step: PropulsionStepInput) -> str | None:
    if not step.burn_requested:
        return "not_requested"
    if step.dt_s <= 0.0:
        return "invalid_dt"
    if step.battery_soc < cfg.min_soc_for_burn:
        return "low_soc"
    if cfg.require_eps_permission and not step.eps_allows_burn:
        return "eps_denied"
    if state.fuel_tank.propellant_mass_kg <= 0.0:
        return "fuel_empty"
    return None


def _scale_tuple(values: tuple[float, ...] | list[float], scale: float) -> tuple[float, ...]:
    return tuple(float(v) * float(scale) for v in values)


def step_propulsion(state: PropulsionState, cfg: PropulsionConfig, step: PropulsionStepInput) -> tuple[PropulsionState, PropulsionStepResult]:
    """Advance propulsion one step."""

    if step.dt_s <= 0.0:
        raise ValueError("PropulsionStepInput.dt_s must be positive")

    reason = _inhibition_reason(state, cfg, step)
    burn_allowed = reason is None
    on_time = tuple(shape_thruster_on_time(cfg.thruster_command, burn_allowed))
    pulse = compute_thruster_pulse(on_time, cfg.thruster_physical)

    # Fuel-limited burn: scale all on-times uniformly if requested propellant
    # exceeds available mass.  This preserves thruster proportions and creates
    # an explicit partial-burn behavior instead of negative fuel.
    if burn_allowed and pulse.propellant_used_kg > state.fuel_tank.propellant_mass_kg > 0.0:
        scale = state.fuel_tank.propellant_mass_kg / pulse.propellant_used_kg
        on_time = _scale_tuple(on_time, scale)
        pulse = compute_thruster_pulse(on_time, cfg.thruster_physical)
        reason = "fuel_limited"
        burn_allowed = True
    elif burn_allowed and pulse.propellant_used_kg <= 0.0:
        reason = "zero_impulse"
        burn_allowed = False

    mdot = pulse.propellant_used_kg / step.dt_s if step.dt_s > 0.0 else 0.0
    next_tank = step_fuel_tank(state.fuel_tank, cfg.fuel_tank, mdot, step.dt_s)
    next_state = PropulsionState(fuel_tank=next_tank, time_s=state.time_s + step.dt_s)
    result = PropulsionStepResult(
        time_s=next_state.time_s,
        mode=step.mode,
        burn_requested=bool(step.burn_requested),
        burn_allowed=bool(burn_allowed),
        inhibition_reason=reason,
        on_time_s=tuple(float(x) for x in on_time),
        impulse_ns=tuple(float(x) for x in pulse.impulse_ns),
        total_impulse_ns=sum(float(x) for x in pulse.impulse_ns),
        total_force_impulse_b_ns=tuple(float(x) for x in pulse.total_force_impulse_b_ns),
        total_torque_impulse_b_nms=tuple(float(x) for x in pulse.total_torque_impulse_b_nms),
        propellant_used_kg=float(pulse.propellant_used_kg),
        propellant_remaining_kg=float(next_tank.propellant_mass_kg),
        tank_pressure_pa=float(next_tank.pressure_pa),
        battery_soc=float(step.battery_soc),
        eps_allows_burn=bool(step.eps_allows_burn),
    )
    return next_state, result


def simulate_propulsion_profile(initial_state: PropulsionState, cfg: PropulsionConfig, steps: Iterable[PropulsionStepInput]) -> tuple[PropulsionState, PropulsionProfileResult, tuple[PropulsionStepResult, ...]]:
    """Run a multi-step propulsion profile."""

    state = initial_state
    rows: list[PropulsionStepResult] = []
    cumulative = 0.0
    cumulative_impulse: list[float] = []
    for step in steps:
        state, result = step_propulsion(state, cfg, step)
        cumulative += result.total_impulse_ns
        cumulative_impulse.append(cumulative)
        rows.append(result)
    profile = PropulsionProfileResult(
        time_s=tuple(r.time_s for r in rows),
        burn_allowed=tuple(r.burn_allowed for r in rows),
        inhibition_reason=tuple(r.inhibition_reason for r in rows),
        total_impulse_ns=tuple(r.total_impulse_ns for r in rows),
        cumulative_impulse_ns=tuple(cumulative_impulse),
        propellant_used_kg=tuple(r.propellant_used_kg for r in rows),
        propellant_remaining_kg=tuple(r.propellant_remaining_kg for r in rows),
        tank_pressure_pa=tuple(r.tank_pressure_pa for r in rows),
    )
    return state, profile, tuple(rows)
