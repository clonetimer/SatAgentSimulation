"""Propulsion subsystem runners."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from components.fuel_tank.degradation import FuelTankDegradation
from components.thruster.degradation import ThrusterDegradation
from components.fault_spec import FaultSpec
from components.thruster.faults import ThrusterFaultType

from .faults import default_fault_scenarios, build_fault_event_specs
from .builder import (
    apply_propulsion_config_faults,
    build_nominal_propulsion_config,
    build_propulsion_basilisk_sim,
    nominal_propulsion_steps,
)
from .degradation import PropulsionDegradation, default_degradation_scenarios, build_degradation_update_specs
from ..runner_common import scenario_result, run_scenario_batch
from ..runtime_injection import attach_runtime_injection_events, runtime_injection_summary
from .model import initialize_propulsion_state, simulate_propulsion_profile
from .schemas import (
    PropulsionBasiliskConfig,
    PropulsionBasiliskSummary,
    PropulsionBasiliskTraceRow,
    PropulsionNativeConfig,
    PropulsionNativeSummary,
    PropulsionNativeTraceRow,
)


def _run_reference_case(cfg) -> dict[str, object]:
    initial = initialize_propulsion_state(cfg)
    final_state, profile, rows = simulate_propulsion_profile(initial, cfg, nominal_propulsion_steps())
    inhibition_reasons = tuple(sorted({row.inhibition_reason for row in rows if row.inhibition_reason is not None}))
    return {
        "backend": "python_reference",
        "steps": len(rows),
        "burns_allowed": sum(1 for row in rows if row.burn_allowed),
        "cumulative_impulse_ns": profile.cumulative_impulse_ns[-1] if profile.cumulative_impulse_ns else 0.0,
        "propellant_remaining_kg": final_state.fuel_tank.propellant_mass_kg,
        "inhibition_reasons": inhibition_reasons,
        "rows": [asdict(row) for row in rows],
    }


def run_reference_nominal_case() -> dict[str, object]:
    return _run_reference_case(build_nominal_propulsion_config())


def run_and_save_nominal_reference_case(save_dir: str | Path) -> dict[str, object]:
    output_dir = Path(save_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = run_reference_nominal_case()
    rows = summary.pop("rows")
    summary_path = output_dir / "propulsion_nominal_summary.json"
    csv_path = output_dir / "propulsion_nominal_rows.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(rows[0].keys()) if rows else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            encoded = dict(row)
            encoded["burn_requested"] = int(bool(encoded["burn_requested"]))
            encoded["burn_allowed"] = int(bool(encoded["burn_allowed"]))
            encoded["eps_allows_burn"] = int(bool(encoded["eps_allows_burn"]))
            encoded["on_time_s"] = json.dumps(encoded["on_time_s"])
            encoded["impulse_ns"] = json.dumps(encoded["impulse_ns"])
            encoded["total_force_impulse_b_ns"] = json.dumps(encoded["total_force_impulse_b_ns"])
            encoded["total_torque_impulse_b_nms"] = json.dumps(encoded["total_torque_impulse_b_nms"])
            writer.writerow(encoded)
    summary["summary"] = str(summary_path)
    summary["csv"] = str(csv_path)
    return summary


def run_propulsion_basilisk_scenario(
    propulsion_config=None,
    native_config: PropulsionBasiliskConfig | None = None,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
    return_context: bool = False,
):
    """Execute a focused Basilisk propulsion scenario."""

    ctx = build_propulsion_basilisk_sim(propulsion_config=propulsion_config, native_config=native_config)
    ncfg = ctx.config
    from Basilisk.utilities import macros

    sim = ctx.simulation
    registered_runtime_events = attach_runtime_injection_events(
        ctx,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
    ) if (fault_event_specs or degradation_update_specs) else {"fault_events": (), "degradation_events": ()}
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(ncfg.duration_s)))
    sim.ExecuteSimulation()

    sc_rec = ctx.recorders["spacecraft"]
    tank_rec = ctx.recorders["fuel_tank"]
    thr_rec = ctx.recorders.get("thruster_0")
    initial_mass = float(ctx.base_parameters["initial_fuel_mass_kg"])

    rows: list[PropulsionBasiliskTraceRow] = []
    for i, t_ns in enumerate(list(sc_rec.times())):
        t_s = float(t_ns) * macros.NANO2SEC
        fuel_mass = float(tank_rec.fuelMass[i]) if hasattr(tank_rec, "fuelMass") else float("nan")
        fuel_mdot = float(tank_rec.fuelMassDot[i]) if hasattr(tank_rec, "fuelMassDot") else 0.0
        thrust_force = 0.0
        thrust_force_b_x = 0.0
        thrust_factor = 0.0
        if thr_rec is not None and i < len(thr_rec.times()):
            if hasattr(thr_rec, "thrustForce"):
                thrust_force = float(thr_rec.thrustForce[i])
            if hasattr(thr_rec, "thrustForce_B"):
                thrust_force_b_x = float(thr_rec.thrustForce_B[i][0])
            if hasattr(thr_rec, "thrustFactor"):
                thrust_factor = float(thr_rec.thrustFactor[i])
        rows.append(
            PropulsionBasiliskTraceRow(
                time_s=t_s,
                position_x_m=float(sc_rec.r_BN_N[i][0]),
                velocity_x_m_s=float(sc_rec.v_BN_N[i][0]),
                fuel_mass_kg=fuel_mass,
                fuel_mass_dot_kg_s=fuel_mdot,
                thrust_force_n=thrust_force,
                thrust_force_b_x_n=thrust_force_b_x,
                thrust_factor=thrust_factor,
            )
        )

    initial_fuel = rows[0].fuel_mass_kg if rows else initial_mass
    final_fuel = rows[-1].fuel_mass_kg if rows else initial_fuel
    final_vx = rows[-1].velocity_x_m_s if rows else 0.0
    prop_used = max(0.0, initial_fuel - final_fuel)
    status = "PASS" if rows and prop_used > 0.0 and abs(final_vx) > 0.0 else "FAIL"
    summary = PropulsionBasiliskSummary(
        backend="basilisk_modules",
        subsystem="propulsion",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        native_modules=(
            "spacecraft.Spacecraft",
            "thrusterDynamicEffector.ThrusterDynamicEffector",
            "fuelTank.FuelTank",
        ),
        messages=("THRArrayOnTimeCmdMsg", "SCStatesMsg", "FuelTankMsg", "THROutputMsg"),
        duration_s=float(ncfg.duration_s),
        step_s=float(ncfg.step_s),
        sample_count=len(rows),
        initial_fuel_mass_kg=initial_fuel,
        final_fuel_mass_kg=final_fuel,
        propellant_used_kg=prop_used,
        final_velocity_x_m_s=final_vx,
        status=status,
    )
    if isinstance(ctx.base_parameters, dict):
        ctx.base_parameters["registered_runtime_events"] = registered_runtime_events
    if return_context:
        return summary, tuple(rows), ctx
    return summary, tuple(rows)


# Compatibility API retained after removing the legacy Basilisk compatibility module.
run_propulsion_native_scenario = run_propulsion_basilisk_scenario


def write_propulsion_basilisk_dataset(output_dir: str | Path, native_config: PropulsionBasiliskConfig | None = None) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows = run_propulsion_basilisk_scenario(native_config=native_config)
    summary_path = output_dir / "propulsion_basilisk_summary.json"
    trace_path = output_dir / "propulsion_basilisk_trace.csv"
    manifest_path = output_dir / "propulsion_basilisk_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(asdict(rows[0]).keys()) if rows else list(PropulsionBasiliskTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "basilisk_propulsion",
        "backend_truth": "Basilisk SimBaseClass + spacecraft + thrusterDynamicEffector + fuelTank",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
        "scope_limit": "focused propulsion subsystem runner; not full mission/orbit reboost propagation",
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


write_propulsion_native_dataset = write_propulsion_basilisk_dataset


def _run_propulsion_case(cfg) -> dict[str, object]:
    summary, rows = run_propulsion_basilisk_scenario(propulsion_config=cfg)
    return {
        "backend": summary.backend,
        "status": summary.status,
        "steps": summary.sample_count,
        "propellant_used_kg": summary.propellant_used_kg,
        "propellant_remaining_kg": summary.final_fuel_mass_kg,
        "final_velocity_x_m_s": summary.final_velocity_x_m_s,
        "thrust_n": cfg.thruster_physical.thrust_n,
        "isp_s": cfg.thruster_physical.isp_s,
        "initial_propellant_kg": summary.initial_fuel_mass_kg,
        "burn_active_samples": sum(1 for row in rows if row.thrust_force_n > 0.0),
    }


def _legacy_run_nominal_case_impl_3() -> dict[str, object]:
    return _run_propulsion_case(build_nominal_propulsion_config())


def _legacy_run_degradation_case_impl_3(degradation: PropulsionDegradation) -> dict[str, object]:
    cfg = build_nominal_propulsion_config(degradation=degradation)
    result = _run_propulsion_case(cfg)
    result["degradation"] = {
        "thrust_loss_pct": degradation.thruster_degradation.thrust_loss_pct,
        "isp_loss_pct": degradation.thruster_degradation.isp_loss_pct,
        "fuel_leak_pct": degradation.fuel_tank_degradation.fuel_leak_pct,
        "pressure_loss_pct": degradation.fuel_tank_degradation.pressure_loss_pct,
    }
    return result


def _legacy_run_fault_case_impl_3(fault_specs: list[FaultSpec]) -> dict[str, object]:
    cfg = apply_propulsion_config_faults(build_nominal_propulsion_config(), fault_specs)
    result = _run_propulsion_case(cfg)
    result["faults"] = [
        {"type": str(f.fault_type), "magnitude": f.magnitude, "target_id": f.target_id}
        for f in fault_specs
    ]
    return result


def _legacy_run_combined_case_impl_3(fault_specs: list[FaultSpec] | None = None, degradation: PropulsionDegradation | None = None) -> dict[str, object]:
    cfg = build_nominal_propulsion_config(degradation=degradation or PropulsionDegradation(
        thruster_degradation=ThrusterDegradation(thrust_loss_pct=10.0, isp_loss_pct=5.0),
        fuel_tank_degradation=FuelTankDegradation(fuel_leak_pct=5.0, pressure_loss_pct=2.0),
    ))
    cfg = apply_propulsion_config_faults(cfg, fault_specs or [])
    result = _run_propulsion_case(cfg)
    result["faults"] = [
        {"type": str(f.fault_type), "magnitude": f.magnitude, "target_id": f.target_id}
        for f in (fault_specs or [])
    ]
    result["combined_with_degradation"] = True
    return result


def run_and_save_nominal_case(save_dir: str | Path) -> dict[str, object]:
    outputs = write_propulsion_basilisk_dataset(Path(save_dir))
    summary = run_nominal_case()
    summary.update(outputs)
    return summary


_LEGACY_RUN_NOMINAL_CASE = _legacy_run_nominal_case_impl_3
_LEGACY_RUN_DEGRADATION_CASE = _legacy_run_degradation_case_impl_3
_LEGACY_RUN_FAULT_CASE = _legacy_run_fault_case_impl_3
_LEGACY_RUN_COMBINED_CASE = _legacy_run_combined_case_impl_3

_NORMAL_SCENARIOS = {
    "station_keeping": "Short station-keeping burn with both nominal thrusters active.",
    "attitude_trim": "Small impulse trim burn with reduced on-time.",
    "short_impulse": "Minimum-pulse burn to exercise command quantization.",
    "long_burn": "Longer burn window for fuel-tank and thrust recorder coverage.",
    "inhibited_burn": "Burn request inhibited by zero on-time command while the Basilisk graph still executes.",
}


def list_normal_scenarios() -> list[str]:
    """Return propulsion non-fault, non-degradation Basilisk operating scenarios."""

    return list(_NORMAL_SCENARIOS.keys())


def _normal_native_config(scenario: str) -> PropulsionBasiliskConfig:
    if scenario == "station_keeping":
        return PropulsionBasiliskConfig(duration_s=0.5, step_s=0.1, on_time_s=(0.08, 0.08), burn_start_s=0.0)
    if scenario == "attitude_trim":
        return PropulsionBasiliskConfig(duration_s=0.5, step_s=0.1, on_time_s=(0.05, 0.02), burn_start_s=0.0)
    if scenario == "short_impulse":
        return PropulsionBasiliskConfig(duration_s=0.3, step_s=0.1, on_time_s=(0.05, 0.05), burn_start_s=0.0)
    if scenario == "long_burn":
        return PropulsionBasiliskConfig(duration_s=1.0, step_s=0.1, on_time_s=(0.20, 0.20), burn_start_s=0.0)
    if scenario == "inhibited_burn":
        return PropulsionBasiliskConfig(duration_s=0.3, step_s=0.1, on_time_s=(0.0, 0.0), burn_start_s=0.0)
    raise ValueError(f"Unsupported propulsion normal scenario: {scenario}")


def _run_basilisk_config(
    native_config: PropulsionBasiliskConfig,
    propulsion_config=None,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    summary, rows, ctx = run_propulsion_basilisk_scenario(
        propulsion_config=propulsion_config,
        native_config=native_config,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
        return_context=True,
    )
    return {
        **asdict(summary),
        "trace_sample_count": len(rows),
        "burn_active_samples": sum(1 for row in rows if row.thrust_force_n > 0.0 or row.thrust_force_b_x_n > 0.0),
        "spacecraft_mass_kg": float(native_config.spacecraft_mass_kg),
        "delta_v_proxy_m_s": float(abs(summary.final_velocity_x_m_s)),
        "cumulative_impulse_proxy_n_s": float(abs(summary.final_velocity_x_m_s) * float(native_config.spacecraft_mass_kg)),
        "component_sources": dict(ctx.component_sources),
        "basilisk_builder": "subsystems.propulsion.builder.build_propulsion_basilisk_sim",
        "registered_runtime_events": ctx.base_parameters.get("registered_runtime_events", {"fault_events": (), "degradation_events": ()}),
        "runtime_injection_summary": runtime_injection_summary(ctx),
    }


def run_normal_scenario(scenario: str = "station_keeping") -> dict[str, Any]:
    """Run one propulsion normal scenario through the subsystem Basilisk builder."""

    result = _run_basilisk_config(_normal_native_config(scenario))
    if scenario == "inhibited_burn":
        result["status"] = "PASS" if result["sample_count"] > 0 and result["propellant_used_kg"] <= 1e-12 else result["status"]
    return scenario_result("propulsion", scenario, _NORMAL_SCENARIOS[scenario], result)


def run_normal_scenarios() -> dict[str, Any]:
    """Run every propulsion non-fault, non-degradation Basilisk scenario."""

    return run_scenario_batch("propulsion", _NORMAL_SCENARIOS, run_normal_scenario)


def run_nominal_case() -> dict[str, object]:
    """Backward-compatible nominal case now routed through a named normal scenario."""

    return run_normal_scenario("station_keeping")


def run_degradation_case(degradation: PropulsionDegradation | str | None = None) -> dict[str, object]:
    """Run a propulsion component-aggregated degradation scenario."""

    scenarios = default_degradation_scenarios()
    if isinstance(degradation, PropulsionDegradation):
        scenario = scenarios["combined_propulsion_aging"]
        cfg = build_nominal_propulsion_config(degradation=degradation)
    else:
        scenario = scenarios[degradation or "combined_propulsion_aging"]
        if scenario.name == "thruster_aging":
            cfg = build_nominal_propulsion_config(
                degradation=PropulsionDegradation(
                    thruster_degradation=ThrusterDegradation(thrust_loss_pct=20.0, isp_loss_pct=10.0),
                    fuel_tank_degradation=FuelTankDegradation(fuel_leak_pct=0.0, pressure_loss_pct=0.0),
                )
            )
        elif scenario.name == "fuel_tank_aging":
            cfg = build_nominal_propulsion_config(
                degradation=PropulsionDegradation(
                    thruster_degradation=ThrusterDegradation(thrust_loss_pct=0.0, isp_loss_pct=0.0),
                    fuel_tank_degradation=FuelTankDegradation(fuel_leak_pct=10.0, pressure_loss_pct=8.0),
                )
            )
        else:
            cfg = build_nominal_propulsion_config(
                degradation=PropulsionDegradation(
                    thruster_degradation=ThrusterDegradation(thrust_loss_pct=18.0, isp_loss_pct=8.0),
                    fuel_tank_degradation=FuelTankDegradation(fuel_leak_pct=8.0, pressure_loss_pct=5.0),
                )
            )
    update_specs = build_degradation_update_specs(scenario, update_period_s=10.0)
    result = _run_basilisk_config(PropulsionBasiliskConfig(duration_s=220.0, step_s=10.0, on_time_s=(0.05, 0.05), burn_start_s=0.0), propulsion_config=cfg, degradation_update_specs=update_specs)
    result["degradation_scenario"] = scenario.name
    result["degradation_update_specs"] = update_specs
    result["covered_components"] = sorted({binding.component for binding in scenario.component_degradations})
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("propulsion", scenario.name, scenario.description, result, mode="degradation")


def run_fault_case(fault_specs: list[FaultSpec] | str | None = None) -> dict[str, object]:
    """Run a propulsion component-aggregated fault scenario."""

    scenarios = default_fault_scenarios()
    scenario = scenarios["thruster_valve_closed" if isinstance(fault_specs, list) or fault_specs is None else fault_specs]
    if scenario.name == "fuel_tank_leak":
        cfg = build_nominal_propulsion_config(
            degradation=PropulsionDegradation(
                thruster_degradation=ThrusterDegradation(thrust_loss_pct=0.0, isp_loss_pct=0.0),
                fuel_tank_degradation=FuelTankDegradation(fuel_leak_pct=15.0, pressure_loss_pct=10.0),
            )
        )
    elif scenario.name == "feed_system_restriction":
        cfg = build_nominal_propulsion_config(
            degradation=PropulsionDegradation(
                thruster_degradation=ThrusterDegradation(thrust_loss_pct=40.0, isp_loss_pct=5.0),
                fuel_tank_degradation=FuelTankDegradation(fuel_leak_pct=0.0, pressure_loss_pct=15.0),
            )
        )
    else:
        cfg = apply_propulsion_config_faults(
            build_nominal_propulsion_config(),
            [FaultSpec(fault_type=ThrusterFaultType.NozzleBlockage, onset_time_s=0.0, duration_s=-1.0, magnitude=0.5, target_id="thruster_0")],
        )
    event_specs = build_fault_event_specs(scenario)
    result = _run_basilisk_config(PropulsionBasiliskConfig(duration_s=430.0, step_s=10.0, on_time_s=(0.05, 0.05), burn_start_s=0.0), propulsion_config=cfg, fault_event_specs=event_specs)
    result["fault_scenario"] = scenario.name
    result["fault_event_specs"] = event_specs
    result["covered_components"] = sorted({binding.component for binding in scenario.component_faults})
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("propulsion", scenario.name, scenario.description, result, mode="fault")


def run_combined_case(fault_specs: list[FaultSpec] | str | None = None, degradation: PropulsionDegradation | str | None = None) -> dict[str, object]:
    """Run static propulsion fault + degradation case through the Basilisk builder."""

    fault_scenario = default_fault_scenarios()["fuel_tank_leak" if isinstance(fault_specs, list) or fault_specs is None else fault_specs]
    degradation_scenario = default_degradation_scenarios()["thruster_aging" if isinstance(degradation, PropulsionDegradation) or degradation is None else degradation]
    cfg = build_nominal_propulsion_config(
        degradation=PropulsionDegradation(
            thruster_degradation=ThrusterDegradation(thrust_loss_pct=25.0, isp_loss_pct=10.0),
            fuel_tank_degradation=FuelTankDegradation(fuel_leak_pct=10.0, pressure_loss_pct=8.0),
        )
    )
    event_specs = build_fault_event_specs(fault_scenario)
    update_specs = build_degradation_update_specs(degradation_scenario, update_period_s=10.0)
    result = _run_basilisk_config(PropulsionBasiliskConfig(duration_s=430.0, step_s=10.0, on_time_s=(0.05, 0.05), burn_start_s=0.0), propulsion_config=cfg, fault_event_specs=event_specs, degradation_update_specs=update_specs)
    result["fault_scenario"] = fault_scenario.name
    result["degradation_scenario"] = degradation_scenario.name
    result["fault_event_specs"] = event_specs
    result["degradation_update_specs"] = update_specs
    result["covered_fault_components"] = sorted({binding.component for binding in fault_scenario.component_faults})
    result["covered_degradation_components"] = sorted({binding.component for binding in degradation_scenario.component_degradations})
    result["combined_with_degradation"] = True
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("propulsion", "propulsion_fault_degradation", "Propulsion combined component fault/degradation scenario assembled through Basilisk builder.", result, mode="combined")


def run_all_modes() -> dict[str, Any]:
    return {
        "normal_scenarios": run_normal_scenarios(),
        "degradation": run_degradation_case(),
        "fault_thruster_valve_closed": run_fault_case(),
        "combined_fault_degradation": run_combined_case(),
    }


def harness_available() -> bool:
    return True


def print_summary(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    results = run_all_modes()
    Path("propulsion_subsystem_all_modes.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(results)
