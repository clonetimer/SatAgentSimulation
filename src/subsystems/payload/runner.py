"""Payload subsystem runner backed by the subsystem Basilisk builder."""
from __future__ import annotations

import csv
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .builder import build_payload_basilisk_sim
from .degradation import default_degradation_scenarios, build_degradation_update_specs
from .faults import default_fault_scenarios, build_fault_event_specs
from .schemas import PayloadBasiliskConfig
from ..runner_common import scenario_result, run_scenario_batch
from ..runtime_injection import attach_runtime_injection_events, runtime_injection_summary


_NORMAL_SCENARIOS = {
    "standby": "Payload instrument scheduled at standby-like zero/low data rate.",
    "observation": "Nominal payload observation and science data generation.",
    "pointing_limited": "Payload observation inhibited by pointing margin, represented as reduced generated baud.",
    "data_generation": "High-rate payload data generation stress scenario.",
}


def list_normal_scenarios() -> list[str]:
    """Return payload non-fault, non-degradation Basilisk operating scenarios."""

    return list(_NORMAL_SCENARIOS.keys())


def _normal_config(scenario: str) -> PayloadBasiliskConfig:
    if scenario == "standby":
        return PayloadBasiliskConfig(duration_s=4.0, step_s=1.0, instrument_baud_bps=1_000.0, initial_mode="standby")
    if scenario == "observation":
        return PayloadBasiliskConfig(duration_s=4.0, step_s=1.0, instrument_baud_bps=250_000.0, initial_mode="observation")
    if scenario == "pointing_limited":
        return PayloadBasiliskConfig(duration_s=4.0, step_s=1.0, instrument_baud_bps=50_000.0, initial_mode="observation")
    if scenario == "data_generation":
        return PayloadBasiliskConfig(duration_s=4.0, step_s=1.0, instrument_baud_bps=500_000.0, initial_mode="observation")
    raise ValueError(f"Unsupported payload normal scenario: {scenario}")


def run_payload_basilisk_scenario(
    config: PayloadBasiliskConfig | None = None,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
    return_context: bool = False,
):
    """Execute the payload subsystem Basilisk builder context."""

    from Basilisk.utilities import macros

    ctx = build_payload_basilisk_sim(config)
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

    rec = ctx.recorders["instrument"]
    rows: list[dict[str, Any]] = []
    for i, t_ns in enumerate(list(rec.times())):
        baud = float(rec.baudRate[i]) if hasattr(rec, "baudRate") else float(cfg.instrument_baud_bps)
        data_name = str(rec.dataName[i]) if hasattr(rec, "dataName") else str(cfg.data_name)
        rows.append({"time_s": float(t_ns) * macros.NANO2SEC, "instrument_baud_bps": baud, "data_name": data_name})

    # Estimate generated science bits from the actual runtime recorder values,
    # not from the pre-run configuration.  This keeps fault/degradation QoI
    # tied to Basilisk runtime event effects rather than static config shaping.
    if rows:
        integrated_generated_bits = sum(max(0.0, float(row["instrument_baud_bps"])) * float(cfg.step_s) for row in rows)
        min_instrument_baud_bps = min(float(row["instrument_baud_bps"]) for row in rows)
        max_instrument_baud_bps = max(float(row["instrument_baud_bps"]) for row in rows)
        mean_instrument_baud_bps = integrated_generated_bits / max(1e-9, float(cfg.step_s) * len(rows))
    else:
        integrated_generated_bits = 0.0
        min_instrument_baud_bps = 0.0
        max_instrument_baud_bps = 0.0
        mean_instrument_baud_bps = 0.0
    summary = {
        "backend": "basilisk_builder_context",
        "status": "PASS" if rows else "FAIL",
        "basilisk_simbase_used": True,
        "execute_simulation_used": True,
        "duration_s": float(cfg.duration_s),
        "step_s": float(cfg.step_s),
        "sample_count": len(rows),
        "final_instrument_baud_bps": rows[-1]["instrument_baud_bps"] if rows else 0.0,
        "min_instrument_baud_bps": min_instrument_baud_bps,
        "max_instrument_baud_bps": max_instrument_baud_bps,
        "mean_instrument_baud_bps": mean_instrument_baud_bps,
        "estimated_generated_bits": integrated_generated_bits,
        "generated_bits": integrated_generated_bits,
        "instrument_baud_bps": mean_instrument_baud_bps,
        "storage_write_bits_proxy": integrated_generated_bits,
        "modules": sorted(ctx.modules.keys()),
        "component_sources": dict(ctx.component_sources),
        "basilisk_builder": "subsystems.payload.builder.build_payload_basilisk_sim",
    }
    if isinstance(ctx.base_parameters, dict):
        ctx.base_parameters["registered_runtime_events"] = registered_runtime_events
    if return_context:
        return summary, tuple(rows), ctx
    return summary, tuple(rows)


def _run_payload_config(
    config: PayloadBasiliskConfig,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    summary, rows, ctx = run_payload_basilisk_scenario(
        config,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
        return_context=True,
    )
    summary["trace_sample_count"] = len(rows)
    summary["registered_runtime_events"] = ctx.base_parameters.get("registered_runtime_events", {"fault_events": (), "degradation_events": ()})
    summary["runtime_injection_summary"] = runtime_injection_summary(ctx)
    return summary


def run_normal_scenario(scenario: str = "observation") -> dict[str, Any]:
    """Run one payload normal scenario through the subsystem Basilisk builder."""

    return scenario_result("payload", scenario, _NORMAL_SCENARIOS[scenario], _run_payload_config(_normal_config(scenario)))


def run_normal_scenarios() -> dict[str, Any]:
    """Run every payload non-fault, non-degradation Basilisk scenario."""

    return run_scenario_batch("payload", _NORMAL_SCENARIOS, run_normal_scenario)


def run_nominal_case() -> dict[str, object]:
    """Backward-compatible nominal case now routed through the Basilisk builder."""

    return run_normal_scenario("observation")


def run_degradation_case(scenario_name: str = "instrument_sensor_aging") -> dict[str, object]:
    """Run a static payload degradation case through the Basilisk builder."""

    scenarios = default_degradation_scenarios()
    scenario = scenarios[scenario_name]
    update_specs = build_degradation_update_specs(scenario, update_period_s=10.0)
    # Direct-only runtime validation starts from the nominal instrument rate;
    # degradation is applied by Basilisk createNewEvent during execution.
    cfg = replace(_normal_config("observation"), duration_s=140.0, step_s=10.0)
    result = _run_payload_config(cfg, degradation_update_specs=update_specs)
    result["degradation_scenario"] = scenario.name
    result["degradation_update_specs"] = update_specs
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("payload", scenario.name, scenario.description, result, mode="degradation")


def run_fault_case(scenario_name: str = "instrument_off") -> dict[str, object]:
    """Run a static payload fault case through the Basilisk builder."""

    scenarios = default_fault_scenarios()
    scenario = scenarios[scenario_name]
    event_specs = build_fault_event_specs(scenario)
    # Start from nominal observation and let the fault event inhibit the
    # instrument during its time window.
    cfg = replace(_normal_config("observation"), duration_s=430.0, step_s=10.0)
    result = _run_payload_config(cfg, fault_event_specs=event_specs)
    result["fault_scenario"] = scenario.name
    result["fault_event_specs"] = event_specs
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("payload", scenario.name, scenario.description, result, mode="fault")


def run_combined_case(
    fault_scenario_name: str = "instrument_off",
    degradation_scenario_name: str = "instrument_sensor_aging",
) -> dict[str, object]:
    """Run a static payload fault + degradation case through the Basilisk builder."""

    fault_scenario = default_fault_scenarios()[fault_scenario_name]
    degradation_scenario = default_degradation_scenarios()[degradation_scenario_name]
    # Start from nominal observation and let degradation/fault events shape the
    # direct Basilisk target during runtime.
    cfg = replace(_normal_config("observation"), duration_s=430.0, step_s=10.0)
    event_specs = build_fault_event_specs(fault_scenario)
    update_specs = build_degradation_update_specs(degradation_scenario, update_period_s=10.0)
    result = _run_payload_config(cfg, fault_event_specs=event_specs, degradation_update_specs=update_specs)
    result["fault_scenario"] = fault_scenario.name
    result["degradation_scenario"] = degradation_scenario.name
    result["fault_event_specs"] = event_specs
    result["degradation_update_specs"] = update_specs
    result["combined_with_degradation"] = True
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("payload", "payload_fault_degradation", "Payload combined fault/degradation scenario assembled through Basilisk builder.", result, mode="combined")


def run_all_modes() -> dict[str, object]:
    return {
        "normal_scenarios": run_normal_scenarios(),
        "degradation": run_degradation_case(),
        "fault_instrument_off": run_fault_case(),
        "combined_fault_degradation": run_combined_case(),
    }


def write_payload_basilisk_dataset(output_dir: str | Path, config: PayloadBasiliskConfig | None = None) -> dict[str, str]:
    """Write payload Basilisk summary and trace files."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows = run_payload_basilisk_scenario(config)
    summary_path = output_dir / "payload_basilisk_summary.json"
    trace_path = output_dir / "payload_basilisk_trace.csv"
    summary_path.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(rows[0].keys()) if rows else ["time_s", "instrument_baud_bps", "data_name"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)
    return {"summary": str(summary_path), "trace": str(trace_path)}


def run_and_save_nominal_case(save_dir: str | Path) -> dict[str, object]:
    outputs = write_payload_basilisk_dataset(Path(save_dir), _normal_config("observation"))
    summary = run_nominal_case()
    summary.update(outputs)
    return summary


def harness_available() -> bool:
    return True


def print_summary(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    results = run_all_modes()
    Path("payload_subsystem_all_modes.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(results)
