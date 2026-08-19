"""Basilisk-scheduled thermal subsystem backend.

This backend is a Basilisk-scheduled custom thermal subsystem.  It is not a
native Basilisk whole-spacecraft thermal network.  It uses component-level
ThermalNodeScheduledSysModel blocks and Basilisk architecture messages to make
heat input, thermal state, heater/cooling response, and thermal-safe contracts
explicit and testable.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from Basilisk.utilities import SimulationBaseClass, macros

from components.thermal_node.basilisk_scheduled import (
    ConstantThermalPowerInput,
    DynamicThermalPowerInput,
    EclipseShadowFactorConverter,
    ThermalNodeScheduledConfig,
    ThermalNodeScheduledSysModel,
    ThermalNodeScheduledTraceRow,
)


@dataclass(frozen=True)
class ThermalScheduledConfig:
    duration_s: float = 300.0
    step_s: float = 10.0
    initial_temp_c: float = 22.0
    ambient_temp_c: float = 18.0
    min_safe_temp_c: float = -5.0
    max_safe_temp_c: float = 45.0
    thermal_capacity_j_per_c: float = 900.0
    conductance_w_per_c: float = 0.35
    heat_power_w: float = 25.0
    heater_power_w: float = 18.0
    cooling_power_w: float = 12.0
    profile_name: str = "nominal_heat_response"


@dataclass(frozen=True)
class ThermalScheduledTraceRow:
    time_s: float
    profile_name: str
    heat_power_w: float
    heater_on: bool
    cooling_on: bool
    heater_power_w: float
    cooling_power_w: float
    passive_cooling_w: float
    temp_c: float
    min_margin_c: float
    max_margin_c: float
    thermal_safe: bool
    mode_recommendation: str


@dataclass(frozen=True)
class ThermalScheduledSummary:
    backend: str
    subsystem: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    native_modules: tuple[str, ...]
    custom_modules: tuple[str, ...]
    duration_s: float
    step_s: float
    sample_count: int
    final_temp_c: float
    min_low_temp_margin_c: float
    min_high_temp_margin_c: float
    heater_activation_count: int
    cooling_activation_count: int
    unsafe_sample_count: int
    status: str
    not_claimed: tuple[str, ...]


def _to_node_config(cfg: ThermalScheduledConfig) -> ThermalNodeScheduledConfig:
    return ThermalNodeScheduledConfig(
        node_name="thermal_subsystem_node",
        initial_temp_c=cfg.initial_temp_c,
        ambient_temp_c=cfg.ambient_temp_c,
        min_safe_temp_c=cfg.min_safe_temp_c,
        max_safe_temp_c=cfg.max_safe_temp_c,
        thermal_capacity_j_per_c=cfg.thermal_capacity_j_per_c,
        conductance_w_per_c=cfg.conductance_w_per_c,
        heater_power_w=cfg.heater_power_w,
        cooling_power_w=cfg.cooling_power_w,
    )


def run_thermal_scheduled_scenario(config: ThermalScheduledConfig | None = None) -> tuple[ThermalScheduledSummary, tuple[ThermalScheduledTraceRow, ...]]:
    cfg = config or ThermalScheduledConfig()
    if cfg.step_s <= 0 or cfg.duration_s <= 0:
        raise ValueError("duration_s and step_s must be positive")

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("thermalScheduledProcess")
    task_name = "thermalScheduledTask"
    process.addTask(sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s))))

    heat = ConstantThermalPowerInput(cfg.heat_power_w)
    node = ThermalNodeScheduledSysModel(_to_node_config(cfg))
    node.heatInMsg.subscribeTo(heat.heatOutMsg)

    sim.AddModelToTask(task_name, heat)
    sim.AddModelToTask(task_name, node)

    thermal_rec = node.thermalStatusOutMsg.recorder(macros.sec2nano(float(cfg.step_s)))
    heater_rec = node.heaterStatusOutMsg.recorder(macros.sec2nano(float(cfg.step_s)))
    cooling_rec = node.coolingStatusOutMsg.recorder(macros.sec2nano(float(cfg.step_s)))
    sim.AddModelToTask(task_name, thermal_rec)
    sim.AddModelToTask(task_name, heater_rec)
    sim.AddModelToTask(task_name, cooling_rec)

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    rows = tuple(ThermalScheduledTraceRow(
        time_s=r.time_s,
        profile_name=cfg.profile_name,
        heat_power_w=r.heat_input_w,
        heater_on=r.heater_on,
        cooling_on=r.cooling_on,
        heater_power_w=r.heater_power_w,
        cooling_power_w=r.cooling_power_w,
        passive_cooling_w=r.passive_cooling_w,
        temp_c=r.temp_c,
        min_margin_c=r.min_margin_c,
        max_margin_c=r.max_margin_c,
        thermal_safe=r.thermal_safe,
        mode_recommendation=r.mode_recommendation,
    ) for r in node.trace)

    min_low_margin = min((r.min_margin_c for r in rows), default=float("nan"))
    min_high_margin = min((r.max_margin_c for r in rows), default=float("nan"))
    unsafe_count = sum(1 for r in rows if not r.thermal_safe)
    heater_count = sum(1 for r in rows if r.heater_on)
    cooling_count = sum(1 for r in rows if r.cooling_on)
    finite = all(r.temp_c == r.temp_c for r in rows)
    status = "PASS" if rows and finite else "FAIL"
    return ThermalScheduledSummary(
        backend="basilisk_scheduled_custom_module",
        subsystem="thermal",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        native_modules=(
            "Basilisk.architecture.messaging.PowerNodeUsageMsg",
            "Basilisk.architecture.messaging.DeviceStatusMsg",
        ),
        custom_modules=(
            "ConstantThermalPowerInput(SysModel)",
            "ThermalNodeScheduledSysModel(SysModel)",
        ),
        duration_s=float(cfg.duration_s),
        step_s=float(cfg.step_s),
        sample_count=len(rows),
        final_temp_c=rows[-1].temp_c if rows else float(cfg.initial_temp_c),
        min_low_temp_margin_c=min_low_margin,
        min_high_temp_margin_c=min_high_margin,
        heater_activation_count=heater_count,
        cooling_activation_count=cooling_count,
        unsafe_sample_count=unsafe_count,
        status=status,
        not_claimed=(
            "native_basilisk_thermal_network",
            "externally_validated_thermal_model",
            "full_spacecraft_thermal_fem",
            "flight_thermal_control_system",
        ),
    ), rows


def write_thermal_scheduled_dataset(output_dir: str | Path, config: ThermalScheduledConfig | None = None) -> dict[str, str]:
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    profiles = [
        config or ThermalScheduledConfig(profile_name="nominal_heat_response", heat_power_w=25.0, initial_temp_c=22.0),
        ThermalScheduledConfig(profile_name="hot_boundary_cooling", heat_power_w=70.0, initial_temp_c=41.0, duration_s=300.0),
        ThermalScheduledConfig(profile_name="cold_boundary_heater", heat_power_w=0.0, initial_temp_c=-7.0, ambient_temp_c=-12.0, duration_s=300.0),
    ]
    summaries = []
    all_rows: list[ThermalScheduledTraceRow] = []
    for prof in profiles:
        summary, rows = run_thermal_scheduled_scenario(prof)
        summaries.append(summary)
        all_rows.extend(rows)

    summary_path = output_dir / "thermal_scheduled_summary.json"
    trace_path = output_dir / "thermal_scheduled_trace.csv"
    manifest_path = output_dir / "thermal_scheduled_manifest.json"
    summary_payload = {
        "dataset_type": "basilisk_scheduled_thermal",
        "backend": "basilisk_scheduled_custom_module",
        "backend_detail": "basilisk_scheduled_custom_thermal_subsystem",
        "profile_count": len(summaries),
        "profile_names": [p.profile_name for p in profiles],
        "profiles": [asdict(s) for s in summaries],
        "overall_status": "PASS" if all(s.status == "PASS" for s in summaries) else "FAIL",
        "status": "PASS" if all(s.status == "PASS" for s in summaries) else "FAIL",
        "execute_simulation_used": all(s.execute_simulation_used for s in summaries),
        "not_claimed": list(summaries[0].not_claimed) if summaries else [],
    }
    summary_path.write_text(json.dumps(summary_payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fields = list(ThermalScheduledTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader()
        for row in all_rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "basilisk_scheduled_thermal",
        "backend_truth": "Basilisk SimBaseClass + Python SysModel + Basilisk architecture messages; not a native Basilisk thermal network",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": summary_payload,
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}

# Backward-compatible names used by the selective whole-spacecraft assembly.
ConstantHeatInput = ConstantThermalPowerInput

class ThermalStatusEvaluator(ThermalNodeScheduledSysModel):
    """Compatibility wrapper around the component-level thermal node SysModel."""

    def __init__(self, cfg: ThermalScheduledConfig):
        super().__init__(_to_node_config(cfg))
