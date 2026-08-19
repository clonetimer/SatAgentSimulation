"""Enhanced battery electrical and aging model.

This module is intentionally a Python prototype layer.  It does not replace
Basilisk ``simpleBattery``; instead it derives voltage/current/internal
resistance/ohmic heat/SOH states from an energy-bucket battery state so the
project can validate the semantics before any C/C++ migration.

This is a superset of the basic BatteryConfig in dynamic_models.py, providing:
- Open-circuit voltage (OCV) vs SOC
- Internal resistance modeling
- Temperature-dependent aging
- Capacity and resistance SOH tracking
- Coulombic efficiency
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from math import exp, isfinite
from typing import Sequence


@dataclass(frozen=True)
class BatteryElectricalAgingConfig:
    """Enhanced battery configuration with electrical and aging parameters."""
    nominal_capacity_wh: float = 120.0
    initial_soc: float = 0.65
    ocv_min_v: float = 22.0
    ocv_max_v: float = 33.6
    nominal_internal_resistance_ohm: float = 0.08
    min_terminal_voltage_v: float = 18.0
    max_terminal_voltage_v: float = 36.0
    max_abs_current_a: float = 20.0
    coulombic_efficiency: float = 0.995
    capacity_soh_init: float = 1.0
    resistance_soh_init: float = 1.0
    # Conservative semi-empirical aging coefficients.  These are representative
    # placeholders and must be traceable before engineering use.
    capacity_fade_per_kah: float = 0.003
    resistance_growth_per_kah: float = 0.006
    calendar_fade_per_day_25c: float = 1.0e-5
    thermal_accel_q10: float = 2.0
    reference_temp_c: float = 25.0
    min_capacity_soh: float = 0.5
    max_resistance_soh: float = 3.0


@dataclass(frozen=True)
class BatteryElectricalAgingState:
    """Enhanced battery state including SOH."""
    soc: float
    capacity_soh: float
    resistance_soh: float
    throughput_ah: float = 0.0
    calendar_age_s: float = 0.0
    storage_wh: float | None = None


@dataclass(frozen=True)
class BatteryElectricalAgingStepResult:
    """Result from one enhanced battery step."""
    time_s: float
    soc: float
    battery_storage_wh: float
    effective_capacity_wh: float
    capacity_soh: float
    resistance_soh: float
    ocv_v: float
    terminal_voltage_v: float
    current_a: float
    internal_resistance_ohm: float
    ohmic_heat_w: float
    power_balance_w: float
    voltage_safe: bool
    current_limited: bool
    throughput_ah: float
    calendar_age_s: float


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(x)))


def initial_state(cfg: BatteryElectricalAgingConfig) -> BatteryElectricalAgingState:
    """Initialize enhanced battery state from config."""
    soc = _clamp(cfg.initial_soc, 0.0, 1.0)
    cap_soh = _clamp(cfg.capacity_soh_init, cfg.min_capacity_soh, 1.0)
    r_soh = _clamp(cfg.resistance_soh_init, 1.0, cfg.max_resistance_soh)
    return BatteryElectricalAgingState(
        soc=soc,
        capacity_soh=cap_soh,
        resistance_soh=r_soh,
        storage_wh=cfg.nominal_capacity_wh * cap_soh * soc,
    )


def ocv_from_soc(soc: float, cfg: BatteryElectricalAgingConfig) -> float:
    """Smooth OCV approximation.

    It is deliberately simple and monotone: a linear term plus a small S-shaped
    shoulder.  It is not a chemistry-specific Li-ion OCV curve.
    """
    z = _clamp(soc, 0.0, 1.0)
    shoulder = 0.04 * (1.0 / (1.0 + exp(-12.0 * (z - 0.5))) - 0.5)
    return cfg.ocv_min_v + (cfg.ocv_max_v - cfg.ocv_min_v) * _clamp(z + shoulder, 0.0, 1.0)


def effective_capacity_wh(state: BatteryElectricalAgingState, cfg: BatteryElectricalAgingConfig) -> float:
    """Calculate effective capacity considering SOH."""
    return max(1.0e-9, cfg.nominal_capacity_wh * _clamp(state.capacity_soh, cfg.min_capacity_soh, 1.0))


def effective_resistance_ohm(state: BatteryElectricalAgingState, cfg: BatteryElectricalAgingConfig) -> float:
    """Calculate effective internal resistance considering SOH."""
    return max(1.0e-9, cfg.nominal_internal_resistance_ohm * _clamp(state.resistance_soh, 1.0, cfg.max_resistance_soh))


def solve_current_from_power(
    power_balance_w: float,
    ocv_v: float,
    resistance_ohm: float,
    cfg: BatteryElectricalAgingConfig
) -> tuple[float, float, bool]:
    """Solve terminal current for signed battery power.

    ``power_balance_w`` is positive for discharge load on the battery and
    negative for charging.  The terminal equation is V = OCV - I*R and P = V*I.
    """
    p = float(power_balance_w)
    if abs(p) < 1.0e-12:
        return 0.0, float(ocv_v), False
    r = max(float(resistance_ohm), 1.0e-12)
    v = max(float(ocv_v), 1.0e-9)
    i = p / v
    limited = False
    for _ in range(20):
        f = i * (v - i * r) - p
        df = v - 2.0 * i * r
        if abs(df) < 1.0e-9:
            break
        i_next = i - f / df
        if not isfinite(i_next):
            break
        i = i_next
    if abs(i) > cfg.max_abs_current_a:
        i = cfg.max_abs_current_a if i > 0 else -cfg.max_abs_current_a
        limited = True
    terminal_v = v - i * r
    terminal_v = _clamp(terminal_v, cfg.min_terminal_voltage_v * 0.5, cfg.max_terminal_voltage_v * 1.5)
    return float(i), float(terminal_v), limited


def temperature_acceleration(temp_c: float, cfg: BatteryElectricalAgingConfig) -> float:
    """Q10 rule: every +10C roughly multiplies degradation by q10."""
    return cfg.thermal_accel_q10 ** ((float(temp_c) - cfg.reference_temp_c) / 10.0)


def step_battery_electrical_aging(
    state: BatteryElectricalAgingState,
    cfg: BatteryElectricalAgingConfig,
    power_balance_w: float,
    dt_s: float,
    temp_c: float = 25.0,
    time_s: float = 0.0,
) -> tuple[BatteryElectricalAgingState, BatteryElectricalAgingStepResult]:
    """Execute one step of enhanced battery simulation."""
    if dt_s <= 0:
        raise ValueError("dt_s must be positive")
    cap_wh = effective_capacity_wh(state, cfg)
    r_ohm = effective_resistance_ohm(state, cfg)
    ocv_v = ocv_from_soc(state.soc, cfg)
    current_a, terminal_v, current_limited = solve_current_from_power(power_balance_w, ocv_v, r_ohm, cfg)
    ohmic_heat_w = current_a * current_a * r_ohm
    delta_wh = -float(power_balance_w) * float(dt_s) / 3600.0
    if delta_wh > 0:
        delta_wh *= cfg.coulombic_efficiency
    storage_prev = cap_wh * _clamp(state.soc, 0.0, 1.0) if state.storage_wh is None else _clamp(state.storage_wh, 0.0, cap_wh)
    storage_next = _clamp(storage_prev + delta_wh, 0.0, cap_wh)
    throughput_ah_inc = abs(current_a) * float(dt_s) / 3600.0
    throughput_kah_inc = throughput_ah_inc / 1000.0
    accel = temperature_acceleration(temp_c, cfg)
    calendar_days = float(dt_s) / 86400.0
    cap_loss = cfg.capacity_fade_per_kah * throughput_kah_inc * accel + cfg.calendar_fade_per_day_25c * calendar_days * accel
    r_growth = cfg.resistance_growth_per_kah * throughput_kah_inc * accel
    cap_soh_next = _clamp(state.capacity_soh - cap_loss, cfg.min_capacity_soh, 1.0)
    r_soh_next = _clamp(state.resistance_soh + r_growth, 1.0, cfg.max_resistance_soh)
    cap_next_wh = max(1.0e-9, cfg.nominal_capacity_wh * cap_soh_next)
    storage_next = _clamp(storage_next, 0.0, cap_next_wh)
    soc_next = storage_next / cap_next_wh
    next_state = BatteryElectricalAgingState(
        soc=soc_next,
        capacity_soh=cap_soh_next,
        resistance_soh=r_soh_next,
        throughput_ah=state.throughput_ah + throughput_ah_inc,
        calendar_age_s=state.calendar_age_s + float(dt_s),
        storage_wh=storage_next,
    )
    result = BatteryElectricalAgingStepResult(
        time_s=float(time_s) + float(dt_s),
        soc=soc_next,
        battery_storage_wh=storage_next,
        effective_capacity_wh=cap_next_wh,
        capacity_soh=cap_soh_next,
        resistance_soh=r_soh_next,
        ocv_v=ocv_v,
        terminal_voltage_v=terminal_v,
        current_a=current_a,
        internal_resistance_ohm=r_ohm,
        ohmic_heat_w=ohmic_heat_w,
        power_balance_w=float(power_balance_w),
        voltage_safe=(cfg.min_terminal_voltage_v <= terminal_v <= cfg.max_terminal_voltage_v),
        current_limited=current_limited,
        throughput_ah=next_state.throughput_ah,
        calendar_age_s=next_state.calendar_age_s,
    )
    return next_state, result


def simulate_power_profile(
    cfg: BatteryElectricalAgingConfig,
    power_balance_w: Sequence[float],
    dt_s: float,
    temp_c_profile: Sequence[float] | None = None,
) -> list[BatteryElectricalAgingStepResult]:
    """Simulate enhanced battery over a power profile."""
    state = initial_state(cfg)
    rows: list[BatteryElectricalAgingStepResult] = []
    time_s = 0.0
    temps = temp_c_profile or [cfg.reference_temp_c for _ in power_balance_w]
    for idx, p in enumerate(power_balance_w):
        temp_c = temps[idx] if idx < len(temps) else temps[-1]
        state, result = step_battery_electrical_aging(state, cfg, p, dt_s, temp_c=temp_c, time_s=time_s)
        rows.append(result)
        time_s += float(dt_s)
    return rows


def row_to_dict(row: BatteryElectricalAgingStepResult) -> dict[str, float | bool]:
    """Convert step result to dictionary."""
    return asdict(row)
