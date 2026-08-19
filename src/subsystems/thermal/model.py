"""Thermal subsystem model.

This subsystem composes simple thermal nodes into a multi-node spacecraft
thermal management layer.  It is intentionally independent from Basilisk and
is suitable for subsystem-stage tests of heat input, heater control, limit
flags, and thermal history.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from components.heater.builder import HeaterConfig
from components.thermal_node.builder import ThermalNodeConfig, ThermalNodeState, step_thermal_node

from .schemas import ThermalConfig, ThermalProfileResult, ThermalState, ThermalStepInput, ThermalStepResult


def initialize_thermal_state(cfg: ThermalConfig) -> ThermalState:
    """Create thermal state from configuration."""

    nodes: dict[str, ThermalNodeState] = {}
    for name, node_cfg in cfg.nodes.items():
        nodes[name] = ThermalNodeState(float(cfg.initial_temp_k_by_node.get(name, node_cfg.ambient_k)))
    heater_state = {name: False for name in cfg.heaters.keys()}
    return ThermalState(nodes=nodes, heater_on_by_node=heater_state, time_s=0.0)


def _clamp_shadow(shadow_factor: float) -> float:
    return max(0.0, min(1.0, float(shadow_factor)))


def _component_to_node(component: str, cfg: ThermalConfig) -> str | None:
    if component in cfg.component_node_map:
        return str(cfg.component_node_map[component])
    if component in cfg.nodes:
        return component
    return None


def _component_heat_by_node(cfg: ThermalConfig, step: ThermalStepInput) -> tuple[dict[str, float], dict[str, float]]:
    node_heat = {name: 0.0 for name in cfg.nodes}
    component_heat: dict[str, float] = {}
    # Mode baseline heat is already node-level heat.
    for node, power in cfg.mode_power_w_by_node.get(step.mode, {}).items():
        if node in node_heat:
            heat = max(0.0, float(power))
            node_heat[node] += heat
            component_heat[f"mode:{step.mode}:{node}"] = heat
    for component, power in step.component_power_w.items():
        node = _component_to_node(component, cfg)
        if node is None or node not in node_heat:
            continue
        efficiency = max(0.0, float(cfg.heat_efficiency_by_component.get(component, 1.0)))
        heat = max(0.0, float(power)) * efficiency
        node_heat[node] += heat
        component_heat[component] = heat
    shadow = _clamp_shadow(step.shadow_factor)
    for node, power in cfg.sunlight_heat_w_by_node.items():
        if node in node_heat:
            heat = max(0.0, float(power)) * shadow
            node_heat[node] += heat
            component_heat[f"sunlight:{node}"] = heat
    return node_heat, component_heat


def _next_heater_state(temp_k: float, prev_on: bool, heater: HeaterConfig, enabled: bool) -> bool:
    if not enabled or heater.max_power_w <= 0.0 or heater.setpoint_k <= 0.0:
        return False
    off = heater.setpoint_k + heater.hysteresis_k if heater.hysteresis_k > 0 else heater.setpoint_k
    if prev_on:
        return temp_k < off
    return temp_k < heater.setpoint_k


def _heater_power_by_node(cfg: ThermalConfig, state: ThermalState, step: ThermalStepInput) -> tuple[dict[str, float], dict[str, bool]]:
    heater_power = {name: 0.0 for name in cfg.nodes}
    heater_on: dict[str, bool] = dict(state.heater_on_by_node)
    for node, heater in cfg.heaters.items():
        if node not in cfg.nodes:
            continue
        temp = state.nodes[node].temp_k
        prev = bool(state.heater_on_by_node.get(node, False))
        on = _next_heater_state(temp, prev, heater, step.heater_enabled)
        heater_on[node] = on
        if on:
            heater_power[node] += max(0.0, float(heater.max_power_w))
    return heater_power, heater_on


def _limits(cfg: ThermalConfig, temps: dict[str, float]) -> tuple[tuple[str, ...], tuple[str, ...], bool]:
    under = tuple(name for name, limit in cfg.under_temp_limit_k_by_node.items() if name in temps and temps[name] < float(limit))
    over = tuple(name for name, limit in cfg.over_temp_limit_k_by_node.items() if name in temps and temps[name] > float(limit))
    safe = bool(set(under).intersection(cfg.safe_request_under_temp_nodes) or set(over).intersection(cfg.safe_request_over_temp_nodes))
    return under, over, safe


def _node_cfg_with_ambient(node_cfg: ThermalNodeConfig, ambient_k: float | None) -> ThermalNodeConfig:
    if ambient_k is None:
        return node_cfg
    return replace(node_cfg, ambient_k=float(ambient_k))


def step_thermal(state: ThermalState, cfg: ThermalConfig, step: ThermalStepInput) -> tuple[ThermalState, ThermalStepResult]:
    """Advance the thermal subsystem one step."""

    if step.dt_s <= 0.0:
        raise ValueError("ThermalStepInput.dt_s must be positive")
    node_heat, component_heat = _component_heat_by_node(cfg, step)
    heater_power, heater_on = _heater_power_by_node(cfg, state, step)
    total_heat = {name: node_heat.get(name, 0.0) + heater_power.get(name, 0.0) for name in cfg.nodes}
    next_nodes: dict[str, ThermalNodeState] = {}
    for node, node_cfg in cfg.nodes.items():
        effective_cfg = _node_cfg_with_ambient(node_cfg, step.ambient_k_by_node.get(node))
        next_nodes[node] = step_thermal_node(state.nodes[node], effective_cfg, total_heat[node], step.dt_s)
    next_state = ThermalState(nodes=next_nodes, heater_on_by_node=heater_on, time_s=state.time_s + step.dt_s)
    temps = {name: node_state.temp_k for name, node_state in next_nodes.items()}
    under, over, safe = _limits(cfg, temps)
    result = ThermalStepResult(
        time_s=next_state.time_s,
        mode=step.mode,
        node_temperature_k=temps,
        node_heat_power_w=total_heat,
        component_heat_power_w=component_heat,
        heater_power_w=heater_power,
        heater_on_by_node={name: bool(heater_on.get(name, False)) for name in cfg.nodes},
        under_temp_flags=under,
        over_temp_flags=over,
        thermal_safe_request=safe,
    )
    return next_state, result


def simulate_thermal_profile(initial_state: ThermalState, cfg: ThermalConfig, steps: Iterable[ThermalStepInput]) -> tuple[ThermalState, ThermalProfileResult, tuple[ThermalStepResult, ...]]:
    """Run a multi-step thermal profile."""

    state = initial_state
    rows: list[ThermalStepResult] = []
    for step in steps:
        state, result = step_thermal(state, cfg, step)
        rows.append(result)
    profile = ThermalProfileResult(
        time_s=tuple(r.time_s for r in rows),
        node_temperature_k=tuple(r.node_temperature_k for r in rows),
        node_heat_power_w=tuple(r.node_heat_power_w for r in rows),
        heater_power_w=tuple(r.heater_power_w for r in rows),
        under_temp_flags=tuple(r.under_temp_flags for r in rows),
        over_temp_flags=tuple(r.over_temp_flags for r in rows),
        thermal_safe_request=tuple(r.thermal_safe_request for r in rows),
    )
    return state, profile, tuple(rows)
