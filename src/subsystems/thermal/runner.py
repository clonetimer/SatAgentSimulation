"""Thermal subsystem runner with support for both legacy and new thermal network."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from components.heater.degradation import HeaterDegradation
from components.radiator.degradation import RadiatorDegradation
from components.fault_spec import FaultSpec
from components.heater.faults import HeaterFaultType

from .builder import apply_thermal_config_faults, build_nominal_thermal_config, build_thermal_basilisk_sim
from .faults import default_fault_scenarios, build_fault_event_specs
from .degradation import ThermalDegradation, default_degradation_scenarios, build_degradation_update_specs
from .network_config import build_nominal_thermal_network_config, apply_degradation_to_network_config
from .schemas import ThermalConfig, ThermalBasiliskConfig
from ..runner_common import scenario_result, run_scenario_batch
from ..runtime_injection import attach_runtime_injection_events, runtime_injection_summary




def _network_backend():
    """Import the Basilisk thermal network backend lazily.

    The multi-node network is still Basilisk-backed; importing this runner should
    not fail in non-Basilisk environments until a network scenario is executed.
    """

    from .thermal_network import run_thermal_network_simulation, write_thermal_network_dataset

    return run_thermal_network_simulation, write_thermal_network_dataset


def _scheduled_backend():
    """Import the Basilisk-scheduled thermal backend lazily.

    This keeps importing thermal.runner possible in environments where Basilisk
    is not installed; executing the scheduled backend still requires Basilisk.
    """

    from .basilisk_scheduled import ThermalScheduledConfig, run_thermal_scheduled_scenario, write_thermal_scheduled_dataset

    return ThermalScheduledConfig, run_thermal_scheduled_scenario, write_thermal_scheduled_dataset


def _thermal_native_config(cfg: ThermalConfig):
    """Convert thermal config to Basilisk native config.
    
    Args:
        cfg: Thermal configuration
    
    Returns:
        ThermalScheduledConfig: Basilisk-compatible scheduled config
    """
    ThermalScheduledConfig, _, _ = _scheduled_backend()
    electronics = cfg.nodes["electronics"]
    return ThermalScheduledConfig(
        initial_temp_c=float(cfg.initial_temp_k_by_node.get("electronics", electronics.ambient_k) - 273.15),
        ambient_temp_c=float(electronics.ambient_k - 273.15),
        min_safe_temp_c=float(cfg.under_temp_limit_k_by_node.get("electronics", 270.0) - 273.15),
        max_safe_temp_c=float(cfg.over_temp_limit_k_by_node.get("electronics", 330.0) - 273.15),
        heat_power_w=float(cfg.mode_power_w_by_node.get("payload", {}).get("electronics", 10.0)),
        heater_power_w=max((float(heater.max_power_w) for heater in cfg.heaters.values()), default=0.0),
    )


def _run_thermal_case(cfg: ThermalConfig) -> dict[str, object]:
    """Run thermal scheduled scenario with given config.
    
    Args:
        cfg: Thermal configuration
    
    Returns:
        dict: Simulation results including temperature, margins, and activation counts
    """
    _, run_thermal_scheduled_scenario, _ = _scheduled_backend()
    summary, rows = run_thermal_scheduled_scenario(_thermal_native_config(cfg))
    return {
        "backend": summary.backend,
        "status": summary.status,
        "steps": summary.sample_count,
        "final_temp_c": summary.final_temp_c,
        "min_low_temp_margin_c": summary.min_low_temp_margin_c,
        "min_high_temp_margin_c": summary.min_high_temp_margin_c,
        "heater_on_steps": summary.heater_activation_count,
        "cooling_on_steps": summary.cooling_activation_count,
        "unsafe_sample_count": summary.unsafe_sample_count,
        "peak_temp_c": max((row.temp_c for row in rows), default=summary.final_temp_c),
        "min_temp_c": min((row.temp_c for row in rows), default=summary.final_temp_c),
    }


def _legacy_run_nominal_case_impl_4() -> dict[str, object]:
    """Run nominal thermal simulation case.
    
    Returns:
        dict: Simulation results for nominal thermal operation
    """
    return _run_thermal_case(build_nominal_thermal_config())


def _legacy_run_degradation_case_impl_4(degradation: ThermalDegradation) -> dict[str, object]:
    """Run thermal simulation with degradation applied.
    
    Args:
        degradation: Thermal degradation parameters
    
    Returns:
        dict: Simulation results including degradation metrics
    """
    cfg = build_nominal_thermal_config(degradation=degradation)
    result = _run_thermal_case(cfg)
    result["degradation"] = {
        "heater_efficiency_loss_pct": degradation.heater_degradation.efficiency_loss_pct,
        "radiator_efficiency_loss_pct": degradation.radiator_degradation.efficiency_loss_pct,
        "radiator_emissivity_loss_pct": degradation.radiator_degradation.emissivity_degradation_pct,
    }
    return result


def _legacy_run_fault_case_impl_4(fault_specs: list[FaultSpec]) -> dict[str, object]:
    """Run thermal simulation with faults applied.
    
    Args:
        fault_specs: List of fault specifications
    
    Returns:
        dict: Simulation results including fault details
    """
    cfg = apply_thermal_config_faults(build_nominal_thermal_config(), fault_specs)
    result = _run_thermal_case(cfg)
    result["faults"] = [
        {"type": str(f.fault_type), "magnitude": f.magnitude, "target_id": f.target_id}
        for f in fault_specs
    ]
    return result


def _legacy_run_all_modes_impl_21() -> dict[str, Any]:
    """Run all thermal simulation modes and return combined results.
    
    Executes four scenarios:
    1. nominal: Baseline thermal operation
    2. degradation: With heater and radiator efficiency loss
    3. fault_heater_failure: Heater failure fault
    4. fault_heater_overheating: Heater overheating fault
    
    Returns:
        dict: Combined results from all thermal simulation modes
    """
    return {
        "nominal": run_nominal_case(),
        "degradation": run_degradation_case(
            ThermalDegradation(
                heater_degradation=HeaterDegradation(efficiency_loss_pct=20.0),
                radiator_degradation=RadiatorDegradation(efficiency_loss_pct=15.0, emissivity_degradation_pct=10.0),
            )
        ),
        "fault_heater_failure": run_fault_case(
            [FaultSpec(fault_type=HeaterFaultType.Failure, onset_time_s=0.0, duration_s=-1.0, magnitude=1.0, target_id="heater_battery")]
        ),
        "fault_heater_overheating": run_fault_case(
            [FaultSpec(fault_type=HeaterFaultType.Overheating, onset_time_s=0.0, duration_s=-1.0, magnitude=0.5, target_id="heater_battery")]
        ),
    }


def run_and_save_nominal_case(save_dir: str | Path) -> dict[str, object]:
    """Run nominal case and save results to files.
    
    Args:
        save_dir: Output directory path
    
    Returns:
        dict: Simulation results with file paths
    """
    _, _, write_thermal_scheduled_dataset = _scheduled_backend()
    outputs = write_thermal_scheduled_dataset(Path(save_dir))
    summary = run_nominal_case()
    summary.update(outputs)
    return summary


_LEGACY_RUN_NOMINAL_CASE = _legacy_run_nominal_case_impl_4
_LEGACY_RUN_DEGRADATION_CASE = _legacy_run_degradation_case_impl_4
_LEGACY_RUN_FAULT_CASE = _legacy_run_fault_case_impl_4
_LEGACY_RUN_ALL_MODES = _legacy_run_all_modes_impl_21

_NORMAL_SCENARIOS = {
    "full_sun": "High external heat input in full sunlight.",
    "partial_eclipse": "Moderate heat input during partial eclipse.",
    "full_eclipse": "Low external heat input during full eclipse.",
    "payload_heating": "Payload operation increases thermal-node heat input.",
    "cooldown": "High initial temperature with low heat input and active cooling margin.",
}


def list_normal_scenarios() -> list[str]:
    """Return thermal non-fault, non-degradation Basilisk operating scenarios."""

    return list(_NORMAL_SCENARIOS.keys())


def _normal_config(scenario: str) -> ThermalBasiliskConfig:
    if scenario == "full_sun":
        return ThermalBasiliskConfig(duration_s=60.0, step_s=10.0, initial_temp_c=22.0, ambient_temp_c=18.0, heat_power_w=35.0)
    if scenario == "partial_eclipse":
        return ThermalBasiliskConfig(duration_s=60.0, step_s=10.0, initial_temp_c=20.0, ambient_temp_c=12.0, heat_power_w=18.0)
    if scenario == "full_eclipse":
        return ThermalBasiliskConfig(duration_s=60.0, step_s=10.0, initial_temp_c=10.0, ambient_temp_c=-5.0, heat_power_w=4.0)
    if scenario == "payload_heating":
        return ThermalBasiliskConfig(duration_s=60.0, step_s=10.0, initial_temp_c=24.0, ambient_temp_c=18.0, heat_power_w=55.0, max_safe_temp_c=50.0)
    if scenario == "cooldown":
        return ThermalBasiliskConfig(duration_s=60.0, step_s=10.0, initial_temp_c=42.0, ambient_temp_c=5.0, heat_power_w=2.0, cooling_on_above_c=35.0, cooling_off_below_c=32.0)
    raise ValueError(f"Unsupported thermal normal scenario: {scenario}")


def run_thermal_basilisk_scenario(
    config: ThermalBasiliskConfig | None = None,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
    return_context: bool = False,
):
    """Execute the thermal subsystem Basilisk builder context."""

    from Basilisk.utilities import macros

    ctx = build_thermal_basilisk_sim(config)
    cfg = ctx.config
    sim = ctx.simulation
    registered_runtime_events = attach_runtime_injection_events(
        ctx,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
    ) if (fault_event_specs or degradation_update_specs) else {"fault_events": (), "degradation_events": ()}
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    node = ctx.modules["thermal_node"]
    rows = tuple(
        {
            "time_s": row.time_s,
            "heat_power_w": row.heat_input_w,
            "heater_on": row.heater_on,
            "cooling_on": row.cooling_on,
            "heater_power_w": row.heater_power_w,
            "cooling_power_w": row.cooling_power_w,
            "passive_cooling_w": row.passive_cooling_w,
            "temp_c": row.temp_c,
            "min_margin_c": row.min_margin_c,
            "max_margin_c": row.max_margin_c,
            "thermal_safe": row.thermal_safe,
            "mode_recommendation": row.mode_recommendation,
        }
        for row in getattr(node, "trace", [])
    )
    temps = [float(row["temp_c"]) for row in rows]
    summary = {
        "backend": "basilisk_builder_context",
        "status": "PASS" if rows else "FAIL",
        "basilisk_simbase_used": True,
        "execute_simulation_used": True,
        "duration_s": float(cfg.duration_s),
        "step_s": float(cfg.step_s),
        "sample_count": len(rows),
        "final_temp_c": temps[-1] if temps else float(cfg.initial_temp_c),
        "min_temp_c": min(temps) if temps else float(cfg.initial_temp_c),
        "max_temp_c": max(temps) if temps else float(cfg.initial_temp_c),
        "min_low_temp_margin_c": min((float(row["min_margin_c"]) for row in rows), default=float("nan")),
        "min_high_temp_margin_c": min((float(row["max_margin_c"]) for row in rows), default=float("nan")),
        "heater_activation_count": sum(1 for row in rows if row["heater_on"]),
        "cooling_activation_count": sum(1 for row in rows if row["cooling_on"]),
        "unsafe_sample_count": sum(1 for row in rows if not row["thermal_safe"]),
        "modules": sorted(ctx.modules.keys()),
        "component_sources": dict(ctx.component_sources),
        "basilisk_builder": "subsystems.thermal.builder.build_thermal_basilisk_sim",
    }
    if isinstance(ctx.base_parameters, dict):
        ctx.base_parameters["registered_runtime_events"] = registered_runtime_events
    if return_context:
        return summary, rows, ctx
    return summary, rows


def _run_basilisk_config(
    config: ThermalBasiliskConfig,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    summary, rows, ctx = run_thermal_basilisk_scenario(
        config,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
        return_context=True,
    )
    summary["trace_sample_count"] = len(rows)
    summary["thermal_violation_duration_s"] = float(summary.get("unsafe_sample_count", 0) * config.step_s)
    summary["heater_duty_proxy"] = float(summary.get("heater_activation_count", 0) / max(len(rows), 1))
    summary["cooling_duty_proxy"] = float(summary.get("cooling_activation_count", 0) / max(len(rows), 1))
    summary["temperature_span_c"] = float(summary.get("max_temp_c", 0.0) - summary.get("min_temp_c", 0.0))
    summary["registered_runtime_events"] = ctx.base_parameters.get("registered_runtime_events", {"fault_events": (), "degradation_events": ()})
    summary["runtime_injection_summary"] = runtime_injection_summary(ctx)
    return summary


def run_normal_scenario(scenario: str = "full_sun") -> dict[str, Any]:
    """Run one thermal normal scenario through the subsystem Basilisk builder."""

    return scenario_result("thermal", scenario, _NORMAL_SCENARIOS[scenario], _run_basilisk_config(_normal_config(scenario)))


def run_normal_scenarios() -> dict[str, Any]:
    """Run every thermal non-fault, non-degradation Basilisk scenario."""

    return run_scenario_batch("thermal", _NORMAL_SCENARIOS, run_normal_scenario)


def run_nominal_case() -> dict[str, object]:
    """Backward-compatible nominal case now routed through a named normal scenario."""

    return run_normal_scenario("full_sun")


def _thermal_degradation_static_config(scenario_name: str) -> ThermalBasiliskConfig:
    cfg = _normal_config("partial_eclipse")
    heater_factor = 1.0
    cooling_factor = 1.0
    conductance_factor = 1.0
    capacity_factor = 1.0
    if scenario_name in {"heater_radiator_aging", "combined_thermal_aging"}:
        heater_factor = 0.78
        cooling_factor = 0.82
    if scenario_name in {"thermal_node_aging", "combined_thermal_aging"}:
        conductance_factor = 0.85
        capacity_factor = 1.10
    return ThermalBasiliskConfig(
        duration_s=140.0,
        step_s=10.0,
        initial_temp_c=cfg.initial_temp_c,
        ambient_temp_c=cfg.ambient_temp_c,
        min_safe_temp_c=cfg.min_safe_temp_c,
        max_safe_temp_c=cfg.max_safe_temp_c,
        thermal_capacity_j_per_c=cfg.thermal_capacity_j_per_c * capacity_factor,
        conductance_w_per_c=cfg.conductance_w_per_c * conductance_factor,
        heat_power_w=cfg.heat_power_w,
        heater_power_w=cfg.heater_power_w * heater_factor,
        cooling_power_w=cfg.cooling_power_w * cooling_factor,
        heater_on_below_c=cfg.heater_on_below_c,
        heater_off_above_c=cfg.heater_off_above_c,
        cooling_on_above_c=cfg.cooling_on_above_c,
        cooling_off_below_c=cfg.cooling_off_below_c,
    )


def run_degradation_case(degradation: ThermalDegradation | str | None = None) -> dict[str, object]:
    """Run a thermal component-aggregated degradation scenario."""

    scenarios = default_degradation_scenarios()
    if isinstance(degradation, ThermalDegradation):
        scenario = scenarios["combined_thermal_aging"]
        cfg = _normal_config("partial_eclipse")
        degraded_cfg = ThermalBasiliskConfig(
            duration_s=140.0,
            step_s=10.0,
            initial_temp_c=cfg.initial_temp_c,
            ambient_temp_c=cfg.ambient_temp_c,
            min_safe_temp_c=cfg.min_safe_temp_c,
            max_safe_temp_c=cfg.max_safe_temp_c,
            thermal_capacity_j_per_c=cfg.thermal_capacity_j_per_c,
            conductance_w_per_c=cfg.conductance_w_per_c * (1.0 - degradation.radiator_degradation.efficiency_loss_pct / 100.0),
            heat_power_w=cfg.heat_power_w,
            heater_power_w=cfg.heater_power_w * (1.0 - degradation.heater_degradation.efficiency_loss_pct / 100.0),
            cooling_power_w=cfg.cooling_power_w * (1.0 - degradation.radiator_degradation.efficiency_loss_pct / 100.0),
            heater_on_below_c=cfg.heater_on_below_c,
            heater_off_above_c=cfg.heater_off_above_c,
            cooling_on_above_c=cfg.cooling_on_above_c,
            cooling_off_below_c=cfg.cooling_off_below_c,
        )
    else:
        scenario = scenarios[degradation or "combined_thermal_aging"]
        degraded_cfg = _thermal_degradation_static_config(scenario.name)
    update_specs = build_degradation_update_specs(scenario, update_period_s=10.0)
    result = _run_basilisk_config(degraded_cfg, degradation_update_specs=update_specs)
    result["degradation_scenario"] = scenario.name
    result["degradation_update_specs"] = update_specs
    result["covered_components"] = sorted({binding.component for binding in scenario.component_degradations})
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("thermal", scenario.name, scenario.description, result, mode="degradation")


def _thermal_fault_static_config(scenario_name: str) -> ThermalBasiliskConfig:
    """Return an un-faulted baseline config for runtime fault injection.

    Earlier batches pre-shaped heater/radiator/thermal-node parameters before
    simulation start.  Under the direct-only policy, the default fault runner
    should start from an unfaulted Basilisk target and let createNewEvent perform
    the parameter change at runtime.
    """

    cfg = _normal_config("full_eclipse" if scenario_name in {"heater_stuck_off", "combined_thermal_fault"} else "full_sun")
    if scenario_name in {"heater_stuck_off", "combined_thermal_fault"}:
        # Keep the direct-only injection policy, but choose a cold profile where
        # the heater actually contributes before the stuck-off event.  The older
        # full-eclipse default started above the heater-on threshold, making the
        # heater-stuck-off event directly applied but directionally silent.
        cfg = replace(
            cfg,
            initial_temp_c=0.0,
            ambient_temp_c=-5.0,
            heat_power_w=0.0,
            heater_on_below_c=4.0,
            heater_off_above_c=8.0,
        )
    return ThermalBasiliskConfig(
        duration_s=430.0,
        step_s=10.0,
        initial_temp_c=cfg.initial_temp_c,
        ambient_temp_c=cfg.ambient_temp_c,
        min_safe_temp_c=cfg.min_safe_temp_c,
        max_safe_temp_c=cfg.max_safe_temp_c,
        thermal_capacity_j_per_c=cfg.thermal_capacity_j_per_c,
        conductance_w_per_c=cfg.conductance_w_per_c,
        heat_power_w=cfg.heat_power_w,
        heater_power_w=cfg.heater_power_w,
        cooling_power_w=cfg.cooling_power_w,
        heater_on_below_c=cfg.heater_on_below_c,
        heater_off_above_c=cfg.heater_off_above_c,
        cooling_on_above_c=cfg.cooling_on_above_c,
        cooling_off_below_c=cfg.cooling_off_below_c,
    )


def run_fault_case(fault_specs: list[FaultSpec] | str | None = None) -> dict[str, object]:
    """Run a thermal component-aggregated fault scenario."""

    scenarios = default_fault_scenarios()
    scenario = scenarios["heater_stuck_off" if isinstance(fault_specs, list) or fault_specs is None else fault_specs]
    event_specs = build_fault_event_specs(scenario)
    result = _run_basilisk_config(_thermal_fault_static_config(scenario.name), fault_event_specs=event_specs)
    result["fault_scenario"] = scenario.name
    result["fault_event_specs"] = event_specs
    result["covered_components"] = sorted({binding.component for binding in scenario.component_faults})
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("thermal", scenario.name, scenario.description, result, mode="fault")


def run_combined_case(fault_specs: list[FaultSpec] | str | None = None, degradation: ThermalDegradation | str | None = None) -> dict[str, object]:
    """Run static thermal fault + degradation case through the Basilisk builder."""

    fault_scenario = default_fault_scenarios()["heater_stuck_off" if isinstance(fault_specs, list) or fault_specs is None else fault_specs]
    degradation_scenario = default_degradation_scenarios()["heater_radiator_aging" if isinstance(degradation, ThermalDegradation) or degradation is None else degradation]
    combined_cfg = _thermal_fault_static_config("combined_thermal_fault")
    event_specs = build_fault_event_specs(fault_scenario)
    update_specs = build_degradation_update_specs(degradation_scenario, update_period_s=10.0)
    result = _run_basilisk_config(combined_cfg, fault_event_specs=event_specs, degradation_update_specs=update_specs)
    result["fault_scenario"] = fault_scenario.name
    result["degradation_scenario"] = degradation_scenario.name
    result["fault_event_specs"] = event_specs
    result["degradation_update_specs"] = update_specs
    result["covered_fault_components"] = sorted({binding.component for binding in fault_scenario.component_faults})
    result["covered_degradation_components"] = sorted({binding.component for binding in degradation_scenario.component_degradations})
    result["combined_with_degradation"] = True
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("thermal", "thermal_fault_degradation", "Thermal combined component fault/degradation scenario assembled through Basilisk builder.", result, mode="combined")


def run_all_modes() -> dict[str, Any]:
    return {
        "normal_scenarios": run_normal_scenarios(),
        "degradation": run_degradation_case(),
        "fault_heater_stuck_off": run_fault_case(),
        "combined_fault_degradation": run_combined_case(),
    }


def run_network_nominal_case(eclipse_factor: float = 1.0) -> dict[str, Any]:
    """Run nominal multi-node thermal network simulation.
    
    Args:
        eclipse_factor: Eclipse factor (0 = full eclipse, 1 = full sun)
    
    Returns:
        dict: Network simulation results including temperatures, activations, and safety status
    """
    config = build_nominal_thermal_network_config()
    run_thermal_network_simulation, _ = _network_backend()
    summary, _ = run_thermal_network_simulation(config, eclipse_factor=eclipse_factor)
    return {
        "backend": "thermal_network",
        "status": summary.status,
        "duration_s": summary.duration_s,
        "step_s": summary.step_s,
        "sample_count": summary.sample_count,
        "final_temps_c": {k: v - 273.15 for k, v in summary.final_temps_k.items()},
        "min_temps_c": {k: v - 273.15 for k, v in summary.min_temps_k.items()},
        "max_temps_c": {k: v - 273.15 for k, v in summary.max_temps_k.items()},
        "heater_activation_count": summary.heater_activation_count,
        "radiator_activation_count": summary.radiator_activation_count,
        "unsafe_sample_count": summary.unsafe_sample_count,
        "unsafe_nodes": summary.unsafe_nodes,
        "eclipse_factor": eclipse_factor,
    }


def run_network_degradation_case(
    heater_efficiency_loss_pct: float = 20.0,
    radiator_efficiency_loss_pct: float = 15.0,
    radiator_emissivity_loss_pct: float = 10.0,
    eclipse_factor: float = 1.0,
) -> dict[str, Any]:
    """Run thermal network simulation with degradation applied.
    
    Args:
        heater_efficiency_loss_pct: Heater efficiency loss percentage
        radiator_efficiency_loss_pct: Radiator efficiency loss percentage
        radiator_emissivity_loss_pct: Radiator emissivity degradation percentage
        eclipse_factor: Eclipse factor (0 = full eclipse, 1 = full sun)
    
    Returns:
        dict: Network simulation results with degradation metrics
    """
    config = build_nominal_thermal_network_config()
    degraded_config = apply_degradation_to_network_config(
        config,
        heater_efficiency_loss_pct=heater_efficiency_loss_pct,
        radiator_efficiency_loss_pct=radiator_efficiency_loss_pct,
        radiator_emissivity_loss_pct=radiator_emissivity_loss_pct,
    )
    run_thermal_network_simulation, _ = _network_backend()
    summary, _ = run_thermal_network_simulation(degraded_config, eclipse_factor=eclipse_factor)
    result = {
        "backend": "thermal_network",
        "status": summary.status,
        "duration_s": summary.duration_s,
        "step_s": summary.step_s,
        "sample_count": summary.sample_count,
        "final_temps_c": {k: v - 273.15 for k, v in summary.final_temps_k.items()},
        "min_temps_c": {k: v - 273.15 for k, v in summary.min_temps_k.items()},
        "max_temps_c": {k: v - 273.15 for k, v in summary.max_temps_k.items()},
        "heater_activation_count": summary.heater_activation_count,
        "radiator_activation_count": summary.radiator_activation_count,
        "unsafe_sample_count": summary.unsafe_sample_count,
        "unsafe_nodes": summary.unsafe_nodes,
        "eclipse_factor": eclipse_factor,
    }
    result["degradation"] = {
        "heater_efficiency_loss_pct": heater_efficiency_loss_pct,
        "radiator_efficiency_loss_pct": radiator_efficiency_loss_pct,
        "radiator_emissivity_loss_pct": radiator_emissivity_loss_pct,
    }
    return result


def run_network_fault_case(
    fault_type: str = "heater_failure",
    target_node: str = "battery",
    magnitude: float = 1.0,
    eclipse_factor: float = 1.0,
) -> dict[str, Any]:
    """Run thermal network simulation with fault applied.
    
    Supported fault types:
        - heater_failure: Disable heater for target node
        - heater_overheating: Increase heater setpoint temperature
        - radiator_failure: Zero radiator area for target node
        - radiator_deploy_stuck: Make radiator non-deployable
    
    Args:
        fault_type: Type of fault to apply
        target_node: Node to apply fault to
        magnitude: Fault magnitude (0-1)
        eclipse_factor: Eclipse factor (0 = full eclipse, 1 = full sun)
    
    Returns:
        dict: Network simulation results with fault details
    """
    from dataclasses import replace
    config = build_nominal_thermal_network_config()

    if fault_type == "heater_failure":
        if target_node in config.heaters:
            config = replace(
                config,
                heaters={
                    **config.heaters,
                    target_node: replace(config.heaters[target_node], power_w=0.0),
                },
            )
    elif fault_type == "heater_overheating":
        if target_node in config.heaters:
            config = replace(
                config,
                heaters={
                    **config.heaters,
                    target_node: replace(
                        config.heaters[target_node],
                        setpoint_k=config.heaters[target_node].setpoint_k * (1.0 + magnitude * 0.1),
                    ),
                },
            )
    elif fault_type == "radiator_failure":
        if target_node in config.radiators:
            config = replace(
                config,
                radiators={
                    **config.radiators,
                    target_node: replace(config.radiators[target_node], area_m2=0.0),
                },
            )
    elif fault_type == "radiator_deploy_stuck":
        if target_node in config.radiators:
            config = replace(
                config,
                radiators={
                    **config.radiators,
                    target_node: replace(config.radiators[target_node], deployable=False),
                },
            )

    run_thermal_network_simulation, _ = _network_backend()
    summary, _ = run_thermal_network_simulation(config, eclipse_factor=eclipse_factor)
    result = {
        "backend": "thermal_network",
        "status": summary.status,
        "duration_s": summary.duration_s,
        "step_s": summary.step_s,
        "sample_count": summary.sample_count,
        "final_temps_c": {k: v - 273.15 for k, v in summary.final_temps_k.items()},
        "min_temps_c": {k: v - 273.15 for k, v in summary.min_temps_k.items()},
        "max_temps_c": {k: v - 273.15 for k, v in summary.max_temps_k.items()},
        "heater_activation_count": summary.heater_activation_count,
        "radiator_activation_count": summary.radiator_activation_count,
        "unsafe_sample_count": summary.unsafe_sample_count,
        "unsafe_nodes": summary.unsafe_nodes,
        "eclipse_factor": eclipse_factor,
    }
    result["fault"] = {
        "type": fault_type,
        "target_node": target_node,
        "magnitude": magnitude,
    }
    return result


def run_all_network_modes() -> dict[str, Any]:
    """Run all thermal network simulation modes.
    
    Executes eight scenarios:
    1. network_nominal_full_sun: Full sun conditions
    2. network_nominal_partial_eclipse: 50% eclipse
    3. network_nominal_full_eclipse: Full eclipse
    4. network_degradation: With heater/radiator degradation
    5. network_fault_heater_failure: Battery heater failure
    6. network_fault_heater_overheating: Battery heater overheating
    7. network_fault_radiator_failure: Electronics radiator failure
    8. network_fault_radiator_stuck: Electronics radiator deploy stuck
    
    Returns:
        dict: Combined results from all network simulation modes
    """
    return {
        "network_nominal_full_sun": run_network_nominal_case(eclipse_factor=1.0),
        "network_nominal_partial_eclipse": run_network_nominal_case(eclipse_factor=0.5),
        "network_nominal_full_eclipse": run_network_nominal_case(eclipse_factor=0.0),
        "network_degradation": run_network_degradation_case(
            heater_efficiency_loss_pct=20.0,
            radiator_efficiency_loss_pct=15.0,
            radiator_emissivity_loss_pct=10.0,
        ),
        "network_fault_heater_failure": run_network_fault_case(
            fault_type="heater_failure",
            target_node="battery",
            magnitude=1.0,
        ),
        "network_fault_heater_overheating": run_network_fault_case(
            fault_type="heater_overheating",
            target_node="battery",
            magnitude=0.5,
        ),
        "network_fault_radiator_failure": run_network_fault_case(
            fault_type="radiator_failure",
            target_node="electronics",
            magnitude=1.0,
        ),
        "network_fault_radiator_stuck": run_network_fault_case(
            fault_type="radiator_deploy_stuck",
            target_node="electronics",
            magnitude=1.0,
        ),
    }


def run_and_save_network_case(save_dir: str | Path) -> dict[str, Any]:
    """Run network case and save results to files.
    
    Args:
        save_dir: Output directory path
    
    Returns:
        dict: Network simulation results with file paths
    """
    config = build_nominal_thermal_network_config()
    _, write_thermal_network_dataset = _network_backend()
    outputs = write_thermal_network_dataset(Path(save_dir), config)
    summary = run_network_nominal_case()
    summary.update(outputs)
    return summary


def print_summary(data: dict[str, Any]) -> None:
    """Print simulation summary as formatted JSON.
    
    Args:
        data: Simulation results dictionary
    """
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    results = run_all_network_modes()
    Path("thermal_network_all_modes.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(results)
