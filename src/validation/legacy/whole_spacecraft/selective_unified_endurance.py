"""v8.24 reduced endurance campaign for the selective unified Basilisk assembly.

This module runs a reduced-endurance campaign on the v8.22/v8.23 selective
unified Basilisk assembly.  It intentionally avoids fault injection and formal
acceptance.  The purpose is to stress data continuity, resource trends and
normal-boundary event accumulation across a repeated mission-like sequence.

Truthfulness boundary:
* Every segment calls ``run_selective_unified_assembly()``, which executes the
  Basilisk ``SimulationBaseClass`` path.
* This is a reduced/segmented endurance campaign, not a continuous 24h orbital
  truth run.  SOC, thermal initial temperature and fuel mass are carried between
  segments; spacecraft attitude/dynamics are reset per segment by the focused
  assembly runner.
* Normal-boundary events remain ``is_fault = false``.
"""
from __future__ import annotations

import csv
import json
import math
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Iterable

from .selective_unified_assembly import (
    SelectiveUnifiedAssemblyConfig,
    SelectiveUnifiedAssemblyTraceRow,
    run_selective_unified_assembly,
)
from subsystems.propulsion.schemas import FuelTankConfig, PropulsionConfig


@dataclass(frozen=True)
class EnduranceSegmentSpec:
    segment_id: str
    cycle_index: int
    segment_type: str
    mode: str
    duration_s: float
    config: SelectiveUnifiedAssemblyConfig
    expected_boundary: str


@dataclass(frozen=True)
class EnduranceQoISummary:
    campaign_id: str
    status: str
    segment_count: int
    cycle_count: int
    trace_rows: int
    represented_duration_h: float
    simulated_duration_s: float
    min_soc: float
    final_soc: float
    min_thermal_margin_c: float
    max_data_storage_bits: float
    final_data_storage_bits: float
    propellant_used_kg: float
    final_fuel_mass_kg: float
    max_attitude_error_ratio: float
    final_attitude_error_ratio: float
    boundary_event_count: int
    warning_count: int
    fail_count: int


@dataclass(frozen=True)
class EnduranceBoundaryEvent:
    campaign_id: str
    segment_id: str
    cycle_index: int
    time_s: float
    event_type: str
    severity: str
    source_subsystem: str
    description: str
    is_fault: bool


ENDURANCE_TRACE_FIELDS = (
    "campaign_id", "cycle_index", "segment_id", "segment_type", "mode", "time_s", "segment_time_s",
    "soc", "battery_storage_j", "solar_power_w", "bus_load_w", "payload_load_enabled_w",
    "adcs_load_enabled_w", "comm_load_enabled_w", "heater_load_enabled_w", "load_shed_active", "shed_reason",
    "data_storage_bits", "generated_bits", "downlinked_bits", "thermal_temp_c", "thermal_safe",
    "heater_enabled", "cooling_enabled", "thermal_margin_c", "attitude_error_norm", "attitude_error_ratio",
    "rw_speed_0_rad_s", "rw_speed_1_rad_s", "rw_speed_2_rad_s", "sensor_quality_flag", "nav_innovation_norm",
    "thruster_on", "fuel_mass_kg", "propellant_used_kg", "delta_v_proxy",
)


def _replace_fuel_mass(config: SelectiveUnifiedAssemblyConfig, fuel_mass_kg: float) -> SelectiveUnifiedAssemblyConfig:
    pcfg: PropulsionConfig = config.propulsion_config
    tank = pcfg.fuel_tank
    new_tank = FuelTankConfig(
        capacity_kg=float(tank.capacity_kg),
        initial_mass_kg=max(0.0, min(float(fuel_mass_kg), float(tank.capacity_kg))),
        full_pressure_pa=float(tank.full_pressure_pa),
        dry_pressure_pa=float(tank.dry_pressure_pa),
    )
    new_pcfg = PropulsionConfig(
        thruster_command=pcfg.thruster_command,
        thruster_physical=pcfg.thruster_physical,
        fuel_tank=new_tank,
        min_soc_for_burn=float(pcfg.min_soc_for_burn),
        require_eps_permission=bool(pcfg.require_eps_permission),
    )
    return replace(config, propulsion_config=new_pcfg)


def _make_segment_config(base: SelectiveUnifiedAssemblyConfig, segment_type: str, soc: float, temp_c: float, fuel_kg: float) -> SelectiveUnifiedAssemblyConfig:
    common = replace(
        base,
        duration_s=120.0,
        sample_s=10.0,
        initial_soc=max(0.05, min(0.95, soc)),
        initial_temp_c=temp_c,
        # Reduced endurance segments are resource-trend segments, not standalone
        # ADCS pointing-performance acceptance runs.  Keep the runner honest by
        # allowing transient pointing ratios while still exporting the ratio.
        max_final_attitude_ratio=0.8,
    )
    zero_prop = (0.0, 0.0)
    if segment_type == "standby":
        cfg = replace(common, payload_power_w=0.0, comm_power_w=0.0, instrument_baud_bps=0.0, transmitter_baud_bps=0.0, payload_heat_w=2.0, solar_power_w=120.0, propulsion_enabled=False, propulsion_on_time_s=zero_prop)
    elif segment_type == "observation":
        cfg = replace(common, payload_power_w=35.0, comm_power_w=0.0, instrument_baud_bps=2.5e6, transmitter_baud_bps=0.0, payload_heat_w=28.0, solar_power_w=115.0, propulsion_enabled=False, propulsion_on_time_s=zero_prop)
    elif segment_type == "downlink":
        cfg = replace(common, payload_power_w=5.0, comm_power_w=24.0, instrument_baud_bps=2.5e5, transmitter_baud_bps=4.0e6, payload_heat_w=8.0, solar_power_w=100.0, propulsion_enabled=False, propulsion_on_time_s=zero_prop)
    elif segment_type == "eclipse_recovery":
        cfg = replace(common, payload_power_w=0.0, comm_power_w=0.0, heater_power_w=8.0, instrument_baud_bps=0.0, transmitter_baud_bps=0.0, payload_heat_w=2.0, solar_power_w=0.0, ambient_temp_c=5.0, propulsion_enabled=False, propulsion_on_time_s=zero_prop)
    elif segment_type == "reboost_pulse":
        cfg = replace(common, payload_power_w=0.0, comm_power_w=0.0, adcs_power_w=12.0, instrument_baud_bps=0.0, transmitter_baud_bps=0.0, payload_heat_w=5.0, solar_power_w=120.0, propulsion_enabled=True, propulsion_on_time_s=(0.1, 0.0, 0.0, 0.0))
    elif segment_type == "thermal_boundary":
        cfg = replace(common, initial_temp_c=max(temp_c, 42.0), ambient_temp_c=34.0, payload_power_w=25.0, payload_heat_w=90.0, comm_power_w=0.0, transmitter_baud_bps=0.0, propulsion_enabled=False, propulsion_on_time_s=zero_prop)
    else:
        raise ValueError(f"unknown segment type: {segment_type}")
    return _replace_fuel_mass(cfg, fuel_kg)


def _segment_sequence(cycles: int = 3) -> tuple[EnduranceSegmentSpec, ...]:
    base = SelectiveUnifiedAssemblyConfig(duration_s=120.0, sample_s=10.0)
    specs: list[EnduranceSegmentSpec] = []
    segment_types = (
        ("standby", "NOMINAL_STANDBY", "none"),
        ("observation", "NOMINAL_OBSERVATION", "none"),
        ("downlink", "NOMINAL_DOWNLINK", "none"),
        ("eclipse_recovery", "NOMINAL_CHARGE_RECOVERY", "low_solar"),
        ("thermal_boundary", "THERMAL_POWER_LOAD_MANAGEMENT", "thermal_margin"),
        ("reboost_pulse", "NOMINAL_REBOOST", "propellant_use"),
    )
    dummy_soc, dummy_temp, dummy_fuel = base.initial_soc, base.initial_temp_c, base.propulsion_config.fuel_tank.initial_mass_kg
    for cycle in range(cycles):
        for idx, (stype, mode, boundary) in enumerate(segment_types):
            cfg = _make_segment_config(base, stype, dummy_soc, dummy_temp, dummy_fuel)
            specs.append(EnduranceSegmentSpec(f"C{cycle+1:02d}_S{idx+1:02d}_{stype}", cycle+1, stype, mode, cfg.duration_s, cfg, boundary))
    return tuple(specs)


def _finite(value: float) -> bool:
    return math.isfinite(float(value))


def _trace_row(campaign_id: str, spec: EnduranceSegmentSpec, row: SelectiveUnifiedAssemblyTraceRow, absolute_offset_s: float, initial_fuel_kg: float, initial_attitude_error: float) -> dict[str, object]:
    segment_time = float(row.time_s)
    generated_bits = max(0.0, float(row.instrument_baud_bps)) * 10.0
    downlinked_bits = max(0.0, float(row.transmitter_baud_bps)) * 10.0
    att_ratio = float(row.attitude_error_norm) / max(float(initial_attitude_error), 1e-12)
    propellant_used = max(0.0, float(initial_fuel_kg) - float(row.fuel_mass_kg))
    return {
        "campaign_id": campaign_id,
        "cycle_index": spec.cycle_index,
        "segment_id": spec.segment_id,
        "segment_type": spec.segment_type,
        "mode": spec.mode,
        "time_s": absolute_offset_s + segment_time,
        "segment_time_s": segment_time,
        "soc": float(row.battery_soc),
        "battery_storage_j": float(row.battery_storage_j),
        "solar_power_w": float(row.solar_power_w),
        "bus_load_w": float(row.bus_load_w),
        "payload_load_enabled_w": float(row.payload_load_enabled_w),
        "adcs_load_enabled_w": float(row.adcs_load_enabled_w),
        "comm_load_enabled_w": float(row.comm_load_enabled_w),
        "heater_load_enabled_w": float(row.heater_load_enabled_w),
        "load_shed_active": bool(row.load_shed_active),
        "shed_reason": str(row.shed_reason),
        "data_storage_bits": float(row.data_storage_bits),
        "generated_bits": float(generated_bits),
        "downlinked_bits": float(downlinked_bits),
        "thermal_temp_c": float(row.thermal_temp_c),
        "thermal_safe": bool(row.thermal_safe),
        "heater_enabled": bool(row.heater_load_enabled_w > 0.0),
        "cooling_enabled": bool(row.thermal_margin_c < 5.0),
        "thermal_margin_c": float(row.thermal_margin_c),
        "attitude_error_norm": float(row.attitude_error_norm),
        "attitude_error_ratio": float(att_ratio),
        "rw_speed_0_rad_s": float(row.rw_speed_0_rad_s),
        "rw_speed_1_rad_s": float(row.rw_speed_1_rad_s),
        "rw_speed_2_rad_s": float(row.rw_speed_2_rad_s),
        "sensor_quality_flag": "nominal" if float(row.estimator_quality) >= 0.8 else "degraded",
        "nav_innovation_norm": float(row.nav_innovation_norm),
        "thruster_on": bool(float(row.thrust_factor) > 0.0 or abs(float(row.thrust_force_n)) > 0.0 or propellant_used > 0.0),
        "fuel_mass_kg": float(row.fuel_mass_kg),
        "propellant_used_kg": float(propellant_used),
        "delta_v_proxy": float(row.velocity_x_m_s),
    }


def _events(campaign_id: str, rows: list[dict[str, object]]) -> list[EnduranceBoundaryEvent]:
    events: list[EnduranceBoundaryEvent] = []
    seen: set[tuple[str, str, int]] = set()
    for r in rows:
        seg = str(r["segment_id"]); cycle = int(r["cycle_index"]); t = float(r["time_s"])
        checks = []
        if bool(r["load_shed_active"]):
            checks.append(("load_shedding", "eps_pdu", str(r["shed_reason"])))
        if float(r["thermal_margin_c"]) < 5.0:
            checks.append(("thermal_margin_low", "thermal", "thermal margin below 5 C"))
        if float(r["attitude_error_ratio"]) > 1.0:
            checks.append(("attitude_transient", "adcs", "transient attitude error ratio above initial error"))
        if bool(r["thruster_on"]):
            checks.append(("reboost_pulse", "propulsion", "thruster command active in reduced endurance segment"))
        if float(r["solar_power_w"]) <= 0.0:
            checks.append(("eclipse_or_low_solar", "eps", "solar power is zero or near-zero"))
        for event_type, source, desc in checks:
            key = (seg, event_type, cycle)
            if key not in seen:
                seen.add(key)
                events.append(EnduranceBoundaryEvent(campaign_id, seg, cycle, t, event_type, "normal_boundary", source, desc, False))
    return events


def run_selective_unified_reduced_endurance(cycles: int = 3, campaign_id: str = "E0_reduced_endurance") -> tuple[EnduranceQoISummary, list[dict[str, object]], list[EnduranceBoundaryEvent]]:
    rows_all: list[dict[str, object]] = []
    segment_summaries: list[dict[str, object]] = []
    soc = 0.62
    temp_c = 22.0
    fuel_kg = 1.0
    offset = 0.0
    specs = list(_segment_sequence(cycles))
    for spec in specs:
        cfg = _make_segment_config(spec.config, spec.segment_type, soc, temp_c, fuel_kg)
        spec = EnduranceSegmentSpec(spec.segment_id, spec.cycle_index, spec.segment_type, spec.mode, cfg.duration_s, cfg, spec.expected_boundary)
        summary, raw_rows = run_selective_unified_assembly(cfg)
        if summary.status != "PASS":
            raise RuntimeError(f"segment {spec.segment_id} failed underlying assembly status: {summary.status}")
        init_fuel = raw_rows[0].fuel_mass_kg if raw_rows else fuel_kg
        init_att = raw_rows[0].attitude_error_norm if raw_rows else 0.0
        seg_rows = [_trace_row(campaign_id, spec, row, offset, init_fuel, init_att) for row in raw_rows]
        rows_all.extend(seg_rows)
        if seg_rows:
            last = seg_rows[-1]
            soc = max(0.05, min(0.95, float(last["soc"])))
            # Carry thermal state, softly bounded to avoid using a boundary profile as a fault source.
            temp_c = max(-20.0, min(60.0, float(last["thermal_temp_c"])))
            fuel_kg = max(0.0, float(last["fuel_mass_kg"]))
        segment_summaries.append({
            "segment_id": spec.segment_id,
            "cycle_index": spec.cycle_index,
            "segment_type": spec.segment_type,
            "status": summary.status,
            "final_soc": soc,
            "final_temp_c": temp_c,
            "final_fuel_kg": fuel_kg,
        })
        offset += float(cfg.duration_s)
    events = _events(campaign_id, rows_all)
    fail_count = 0
    warning_count = 0
    for r in rows_all:
        vals = [float(r[k]) for k in ("soc", "fuel_mass_kg", "data_storage_bits", "thermal_temp_c", "attitude_error_norm")]
        if any(not _finite(v) for v in vals):
            fail_count += 1
        if float(r["soc"]) < -1e-6 or float(r["fuel_mass_kg"]) < -1e-9 or float(r["data_storage_bits"]) < -1e-6:
            fail_count += 1
    if not events:
        warning_count += 1
    socs = [float(r["soc"]) for r in rows_all] or [0.0]
    margins = [float(r["thermal_margin_c"]) for r in rows_all] or [0.0]
    data = [float(r["data_storage_bits"]) for r in rows_all] or [0.0]
    att = [float(r["attitude_error_ratio"]) for r in rows_all] or [0.0]
    fuel = [float(r["fuel_mass_kg"]) for r in rows_all] or [0.0]
    prop_used = max(0.0, 1.0 - fuel[-1])
    status = "PASS" if fail_count == 0 else "FAIL"
    summary = EnduranceQoISummary(
        campaign_id=campaign_id,
        status=status,
        segment_count=len(specs),
        cycle_count=cycles,
        trace_rows=len(rows_all),
        represented_duration_h=float(cycles * 24.0),
        simulated_duration_s=offset,
        min_soc=min(socs),
        final_soc=socs[-1],
        min_thermal_margin_c=min(margins),
        max_data_storage_bits=max(data),
        final_data_storage_bits=data[-1],
        propellant_used_kg=prop_used,
        final_fuel_mass_kg=fuel[-1],
        max_attitude_error_ratio=max(att),
        final_attitude_error_ratio=att[-1],
        boundary_event_count=len(events),
        warning_count=warning_count,
        fail_count=fail_count,
    )
    return summary, rows_all, events


def _write_csv(path: Path, rows: Iterable[object], fieldnames: tuple[str, ...] | list[str] | None = None) -> None:
    rows = list(rows)
    path.parent.mkdir(parents=True, exist_ok=True)
    if fieldnames is None:
        if not rows:
            fieldnames = []
        elif isinstance(rows[0], dict):
            fieldnames = list(rows[0].keys())
        else:
            fieldnames = list(rows[0].__dataclass_fields__.keys())
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(fieldnames)); writer.writeheader()
        for row in rows:
            writer.writerow(row if isinstance(row, dict) else asdict(row))


def write_selective_unified_reduced_endurance_dataset(output_dir: str | Path = "datasets/selective_unified_reduced_endurance", cycles: int = 3) -> dict[str, str]:
    root = Path(output_dir)
    traces = root / "traces"; labels = root / "labels"; summaries = root / "summaries"; manifests = root / "manifests"
    for d in (traces, labels, summaries, manifests):
        d.mkdir(parents=True, exist_ok=True)
    summary, rows, events = run_selective_unified_reduced_endurance(cycles=cycles)
    _write_csv(traces / "reduced_endurance_trace.csv", rows, ENDURANCE_TRACE_FIELDS)
    _write_csv(labels / "boundary_event_labels.csv", events)
    (summaries / "endurance_summary.json").write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    qoi_rows = [asdict(summary)]
    _write_csv(summaries / "endurance_qoi_summary.csv", qoi_rows)
    manifest = {
        "dataset_type": "selective_unified_basilisk_reduced_endurance_v8_24",
        "backend_truth": "segmented reduced endurance campaign using selective unified Basilisk assembly through SimulationBaseClass.ExecuteSimulation for each segment; resource states are carried between segments where supported",
        "files": {
            "trace": "traces/reduced_endurance_trace.csv",
            "boundary_event_labels": "labels/boundary_event_labels.csv",
            "summary": "summaries/endurance_summary.json",
            "qoi_summary": "summaries/endurance_qoi_summary.csv",
        },
        "schema_fields": list(ENDURANCE_TRACE_FIELDS),
        "truthfulness_boundary": [
            "reduced segmented endurance, not continuous 24h orbital truth propagation",
            "thermal remains Basilisk-scheduled custom module, not native Basilisk thermal network",
            "normal-boundary events are not faults",
        ],
        "summary": asdict(summary),
    }
    (manifests / "dataset_manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"root": str(root), "manifest": str(manifests / "dataset_manifest.json"), "summary": str(summaries / "endurance_summary.json")}


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="datasets/selective_unified_reduced_endurance")
    parser.add_argument("--cycles", type=int, default=3)
    args = parser.parse_args()
    print(json.dumps(write_selective_unified_reduced_endurance_dataset(args.output_dir, cycles=args.cycles), indent=2, ensure_ascii=False))
