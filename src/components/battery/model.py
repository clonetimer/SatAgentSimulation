"""Pure-Python reference model for the battery component."""
from __future__ import annotations

from dataclasses import replace
from typing import Optional

from .degradation import BatteryDegradation
from .degradation import apply_battery_degradation
from .faults import FaultSpec
from .faults import BatteryFaultType
from .schemas import BatteryConfig, BatteryProfileResult, BatteryState


def _temperature_factor(temp_c: float, config: BatteryConfig) -> float:
    if temp_c < config.min_temp_c:
        return max(0.0, 0.1 + (temp_c - config.min_temp_c) * 0.9 / (config.optimal_temp_c - config.min_temp_c))
    if temp_c > config.max_temp_c:
        return max(0.0, 1.0 - (temp_c - config.max_temp_c) / 10.0)
    return 1.0


def _cc_cv_charge_limit(soc: float, config: BatteryConfig) -> float:
    if soc < config.cv_soc_threshold:
        return config.max_charge_current_a * config.nominal_voltage_v
    if soc >= 0.95:
        return config.max_charge_current_a * config.nominal_voltage_v * 0.1
    ratio = (soc - config.cv_soc_threshold) / (0.95 - config.cv_soc_threshold)
    return config.max_charge_current_a * config.nominal_voltage_v * (1.0 - ratio * 0.9)


def initialize_battery(config: BatteryConfig) -> BatteryState:
    capacity_wh = max(0.0, config.capacity_wh)
    soc = max(config.min_soc, min(config.max_soc, config.initial_soc))
    return BatteryState(capacity_wh * soc, capacity_wh, soc, 0.0)


def step_battery(
    state: BatteryState,
    config: BatteryConfig,
    net_power_w: float,
    dt_s: float,
    temp_c: Optional[float] = None,
) -> BatteryState:
    if dt_s < 0:
        raise ValueError("dt_s")
    if net_power_w >= 0:
        if temp_c is not None:
            net_power_w = net_power_w * _temperature_factor(temp_c, config)
        charge_limit_w = _cc_cv_charge_limit(state.soc, config)
        charge_power_w = min(net_power_w, charge_limit_w)
        efficiency = max(config.charge_efficiency, 1e-12)
        storage_wh = max(
            config.min_soc * state.capacity_wh,
            min(config.max_soc * state.capacity_wh, state.storage_wh + charge_power_w * dt_s / 3600 * efficiency),
        )
        shunt_wh = (net_power_w - (storage_wh - state.storage_wh) * 3600 / dt_s / efficiency) * dt_s / 3600 if net_power_w > 0 else 0.0
        shunt_wh = max(0.0, shunt_wh)
    else:
        efficiency = 1 / max(config.discharge_efficiency, 1e-12)
        storage_wh = max(
            config.min_soc * state.capacity_wh,
            min(config.max_soc * state.capacity_wh, state.storage_wh + net_power_w * dt_s / 3600 * efficiency),
        )
        shunt_wh = 0.0
    return BatteryState(
        storage_wh,
        state.capacity_wh,
        storage_wh / state.capacity_wh if state.capacity_wh > 0 else 0.0,
        state.shunt_dissipated_wh + shunt_wh,
    )


def simulate_battery_power_profile(
    state: BatteryState,
    config: BatteryConfig,
    profile: list[float],
    dt_s: float,
    temp_profile: Optional[list[float]] = None,
) -> BatteryProfileResult:
    if dt_s <= 0:
        raise ValueError("dt_s")
    time_s = [0.0]
    soc = [state.soc]
    storage_wh = [state.storage_wh]
    shunt_dissipated_wh = [state.shunt_dissipated_wh]
    current_state = state
    for i, power_w in enumerate(profile):
        temp_c = temp_profile[i] if temp_profile and i < len(temp_profile) else None
        current_state = step_battery(current_state, config, power_w, dt_s, temp_c)
        time_s.append((i + 1) * dt_s)
        soc.append(current_state.soc)
        storage_wh.append(current_state.storage_wh)
        shunt_dissipated_wh.append(current_state.shunt_dissipated_wh)
    return BatteryProfileResult(tuple(time_s), tuple(soc), tuple(storage_wh), tuple(shunt_dissipated_wh))


def _build_nominal_battery_config_base_impl(
    capacity_wh: float = 100.0,
    initial_soc: float = 0.5,
    charge_efficiency: float = 1.0,
    discharge_efficiency: float = 1.0,
    min_soc: float = 0.0,
    max_soc: float = 1.0,
    degradation: BatteryDegradation | None = None,
    fault_specs: list[FaultSpec] | None = None,
) -> BatteryConfig:
    config = BatteryConfig(
        capacity_wh=capacity_wh,
        initial_soc=initial_soc,
        charge_efficiency=charge_efficiency,
        discharge_efficiency=discharge_efficiency,
        min_soc=min_soc,
        max_soc=max_soc,
    )
    if degradation is not None:
        config = apply_battery_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_battery_config_faults(config, fault_specs)
    return config


def apply_battery_config_faults(config: BatteryConfig, fault_specs: list[FaultSpec]) -> BatteryConfig:
    updated = config
    for spec in fault_specs:
        if not isinstance(spec.fault_type, BatteryFaultType):
            continue
        if spec.fault_type == BatteryFaultType.SuddenCapacityLoss:
            updated = replace(updated, capacity_wh=updated.capacity_wh * (1.0 - spec.magnitude))
        elif spec.fault_type == BatteryFaultType.OpenCircuit:
            updated = replace(updated, charge_efficiency=0.0, discharge_efficiency=0.0)
        elif spec.fault_type == BatteryFaultType.ThermalRunaway:
            updated = replace(updated, max_temp_c=100.0, optimal_temp_c=60.0)
    return updated

# Component fault/degradation compatibility wrappers
from .degradation import BatteryDegradationRate
from .degradation import compute_degradation_state
from .faults import apply_battery_faults
from .faults import FaultSpec as _ComponentFaultSpec

_build_nominal_battery_config_base = _build_nominal_battery_config_base_impl

def build_nominal_battery_config(
    *args,
    degradation: BatteryDegradation | None = None,
    degradation_rate: BatteryDegradationRate | None = None,
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
    config = _build_nominal_battery_config_base(*args, **kwargs)
    if degradation is None and degradation_rate is not None and float(years_elapsed) > 0.0:
        degradation = compute_degradation_state(degradation_rate, years_elapsed)
    if degradation is not None:
        config = apply_battery_degradation(config, degradation)
    if fault_specs is not None:
        config = apply_battery_faults(config, fault_specs)
    return config

