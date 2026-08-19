"""Comm/Data subsystem runner backed by the Basilisk data path."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any, Mapping, Sequence

from components.fault_spec import FaultSpec

from .builder import build_comm_data_basilisk_sim, build_comm_data_config, build_nominal_comm_data_config
from .faults import default_fault_scenarios as _default_fault_scenarios, build_fault_event_specs as _build_fault_event_specs
from .degradation import default_degradation_scenarios as _default_degradation_scenarios, build_degradation_update_specs as _build_degradation_update_specs
from ..runner_common import scenario_result, run_scenario_batch
from ..runtime_injection import attach_runtime_injection_events, runtime_injection_summary
from .schemas import (
    CommDataBasiliskConfig,
    CommDataBasiliskSummary,
    CommDataBasiliskTraceRow,
    CommDataNativeConfig,
    CommDataNativeSummary,
    CommDataNativeTraceRow,
)


def _comm_public_row(row: CommDataBasiliskTraceRow) -> dict[str, Any]:
    return {
        "time_s": row.time_s,
        "comm_data.instrument_baud_bps": row.instrument_baud_bps,
        "comm_data.storage_level_bits": row.storage_level_bits,
        "comm_data.storage_capacity_bits": row.storage_capacity_bits,
        "comm_data.transmitter_baud_bps": row.transmitter_baud_bps,
        "comm_data.native_storage_drain_enabled": row.native_storage_drain_enabled,
        "comm_data.transmitter_storage_node_baud_bps": row.transmitter_storage_node_baud_bps,
    }


def _comm_rows_from_recorders(rec: Mapping[str, Any], cfg: CommDataBasiliskConfig, transmitter_storage_node_baud: float) -> list[CommDataBasiliskTraceRow]:
    from Basilisk.utilities import macros

    inst_rec = rec["instrument"]
    storage_rec = rec["storage"]
    tx_rec = rec["transmitter"]
    rows: list[CommDataBasiliskTraceRow] = []
    for i, t_ns in enumerate(list(storage_rec.times())):
        t_s = float(t_ns) * macros.NANO2SEC
        level_bits = float(storage_rec.storageLevel[i]) if hasattr(storage_rec, "storageLevel") else 0.0
        cap_bits = float(storage_rec.storageCapacity[i]) if hasattr(storage_rec, "storageCapacity") else float(cfg.storage_capacity_bits)
        inst_baud = float(inst_rec.baudRate[i]) if hasattr(inst_rec, "baudRate") else float(cfg.instrument_baud_bps)
        tx_node_baud = float(tx_rec.baudRate[i]) if hasattr(tx_rec, "baudRate") else float(transmitter_storage_node_baud)
        rows.append(CommDataBasiliskTraceRow(
            t_s, inst_baud, level_bits, cap_bits, abs(float(cfg.transmitter_baud_bps)),
            bool(cfg.native_storage_drain_enabled), tx_node_baud,
        ))
    return rows


def run_comm_data_basilisk_scenario(
    config: CommDataBasiliskConfig | None = None,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
    telemetry_streams: Sequence[Mapping[str, Any]] = (),
    return_context: bool = False,
):
    """Execute a focused Comm/Data Basilisk scenario."""

    ctx = build_comm_data_basilisk_sim(config)
    cfg = ctx.config
    from Basilisk.utilities import macros

    sim = ctx.simulation
    registered_runtime_events = attach_runtime_injection_events(
        ctx,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
    ) if (fault_event_specs or degradation_update_specs) else {"fault_events": (), "degradation_events": ()}
    from sat_sim.basilisk_recorder_groups import attach_grouped_message_recorders, group_manifest
    recorder_messages = {
        "instrument": ctx.message_handles["instrument_node_data_out"],
        "storage": ctx.message_handles["storage_unit_data_out"],
        "transmitter": ctx.message_handles["transmitter_node_data_out"],
    }
    recorder_groups = attach_grouped_message_recorders(
        sim=sim, task_name=ctx.task_name, message_handles=recorder_messages, streams=telemetry_streams
    )
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    transmitter_storage_node_baud = float(ctx.base_parameters["transmitter_storage_node_baud_bps"])
    rows = _comm_rows_from_recorders(ctx.recorders, cfg, transmitter_storage_node_baud)
    native_streams: list[dict[str, Any]] = []
    for group in recorder_groups.values():
        group_rows = _comm_rows_from_recorders(group.recorders, cfg, transmitter_storage_node_baud)
        native_streams.append({
            "stream_id": group.spec.stream_id,
            "sample_s": group.spec.sample_s,
            "format": group.spec.format,
            "fields": list(group.spec.fields),
            "rows": [_comm_public_row(row) for row in group_rows],
            "recorder_sources": sorted(group.recorders),
            "task_name": group.task_name,
            "native_recorder": True,
        })
    if isinstance(ctx.base_parameters, dict):
        ctx.base_parameters["native_multi_rate_telemetry"] = {
            **group_manifest(recorder_groups),
            "streams": native_streams,
        }

    if cfg.native_storage_drain_enabled:
        status = "PASS" if rows and rows[-1].storage_level_bits >= -1e-6 else "FAIL"
    else:
        status = "PASS" if rows and rows[-1].storage_level_bits >= rows[0].storage_level_bits else "FAIL"
    generated_bits = max(0.0, float(cfg.instrument_baud_bps)) * float(cfg.duration_s)
    estimated_downlinked_bits = max(0.0, generated_bits + float(cfg.initial_storage_bits) - (rows[-1].storage_level_bits if rows else 0.0))
    summary = CommDataBasiliskSummary(
        backend="basilisk_modules",
        subsystem="comm_data",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        native_modules=(
            "simpleInstrument.SimpleInstrument",
            "simpleStorageUnit.SimpleStorageUnit",
            "simpleTransmitter.SimpleTransmitter",
            "components.payload.builder.build_simple_instrument",
            "components.data_queue.builder.build_simple_storage_unit",
            "components.link_budget.builder.build_simple_transmitter",
        ),
        duration_s=float(cfg.duration_s),
        step_s=float(cfg.step_s),
        sample_count=len(rows),
        final_storage_bits=rows[-1].storage_level_bits if rows else 0.0,
        max_storage_bits=max((r.storage_level_bits for r in rows), default=0.0),
        min_storage_bits=min((r.storage_level_bits for r in rows), default=0.0),
        nominal_generated_bits=generated_bits,
        estimated_native_downlinked_bits=estimated_downlinked_bits,
        native_storage_drain_enabled=bool(cfg.native_storage_drain_enabled),
        status=status,
    )
    if isinstance(ctx.base_parameters, dict):
        ctx.base_parameters["registered_runtime_events"] = registered_runtime_events
    if return_context:
        return summary, tuple(rows), ctx
    return summary, tuple(rows)


# Compatibility API retained after removing the legacy Basilisk compatibility module.
run_comm_data_native_scenario = run_comm_data_basilisk_scenario


def write_comm_data_basilisk_dataset(output_dir: str | Path, config: CommDataBasiliskConfig | None = None) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows = run_comm_data_basilisk_scenario(config)
    summary_path = output_dir / "comm_data_basilisk_summary.json"
    trace_path = output_dir / "comm_data_basilisk_trace.csv"
    manifest_path = output_dir / "comm_data_basilisk_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(asdict(rows[0]).keys()) if rows else list(CommDataBasiliskTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "basilisk_comm_data",
        "backend_truth": "Basilisk SimBaseClass + simpleInstrument/simpleStorageUnit/simpleTransmitter modules",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
        "not_claimed": [] if summary.native_storage_drain_enabled else ["fully_validated_closed_loop_storage_drain_across_all_basilisk_versions"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


write_comm_data_native_dataset = write_comm_data_basilisk_dataset


def _run_comm_data_case(cfg) -> dict[str, object]:
    native_cfg = CommDataBasiliskConfig(
        instrument_baud_bps=float(max(cfg.generated_bps_by_mode.values(), default=cfg.default_generated_bps)),
        storage_capacity_bits=float(cfg.queue.capacity_bits),
        transmitter_baud_bps=float(cfg.max_downlink_bps or cfg.link.raw_rate_bps),
        native_storage_drain_enabled=True,
    )
    summary, rows = run_comm_data_basilisk_scenario(native_cfg)
    return {
        "backend": summary.backend,
        "status": summary.status,
        "steps": summary.sample_count,
        "final_storage_bits": summary.final_storage_bits,
        "max_storage_bits": summary.max_storage_bits,
        "min_storage_bits": summary.min_storage_bits,
        "estimated_native_downlinked_bits": summary.estimated_native_downlinked_bits,
        "instrument_baud_bps": native_cfg.instrument_baud_bps,
        "transmitter_baud_bps": native_cfg.transmitter_baud_bps,
        "drain_active_samples": sum(1 for row in rows if row.transmitter_storage_node_baud_bps < 0.0),
    }


def _legacy_run_nominal_case_impl() -> dict[str, object]:
    return _run_comm_data_case(build_nominal_comm_data_config())


def _legacy_run_degradation_case_impl() -> dict[str, object]:
    cfg = build_comm_data_config(amp_type="twt", tx_power_w=3.0, efficiency=0.30, gain_db=28.0)
    result = _run_comm_data_case(cfg)
    result["degradation"] = {
        "transmitter_efficiency_drop": True,
        "transmitter_baud_reduction": True,
    }
    return result


def _legacy_run_fault_case_impl(fault_specs: list[FaultSpec] | None = None) -> dict[str, object]:
    cfg = build_comm_data_config(amp_type="sspa", tx_power_w=0.5, efficiency=0.10, gain_db=10.0)
    fault_specs = fault_specs or []
    result = _run_comm_data_case(cfg)
    result["faults"] = [
        {"type": str(spec.fault_type), "magnitude": spec.magnitude, "target_id": spec.target_id}
        for spec in fault_specs
    ]
    result["fault_static_effect"] = "reduced transmitter capability"
    return result


def _legacy_run_combined_case_impl(fault_specs: list[FaultSpec] | None = None) -> dict[str, object]:
    result = run_fault_case(fault_specs)
    result["combined_with_degradation"] = True
    result["degradation"] = {"transmitter_efficiency_drop": True, "link_margin_reduction": True}
    return result


def run_and_save_nominal_case(save_dir: str | Path) -> dict[str, object]:
    outputs = write_comm_data_basilisk_dataset(Path(save_dir), CommDataBasiliskConfig(native_storage_drain_enabled=True))
    summary = run_nominal_case()
    summary.update(outputs)
    return summary


_LEGACY_RUN_NOMINAL_CASE = _legacy_run_nominal_case_impl
_LEGACY_RUN_DEGRADATION_CASE = _legacy_run_degradation_case_impl
_LEGACY_RUN_FAULT_CASE = _legacy_run_fault_case_impl
_LEGACY_RUN_COMBINED_CASE = _legacy_run_combined_case_impl

_NORMAL_SCENARIOS = {
    "imaging_record": "Payload imaging data are generated and stored without a ground contact.",
    "downlink_pass": "Stored data are drained during an active downlink pass.",
    "no_contact_storage_growth": "No contact; storage should monotonically grow while payload data are generated.",
    "ground_contact": "Ground contact drains an initial onboard-storage backlog.",
}


def list_normal_scenarios() -> list[str]:
    """Return Comm/Data non-fault, non-degradation Basilisk operating scenarios."""

    return list(_NORMAL_SCENARIOS.keys())


def _normal_config(scenario: str) -> CommDataBasiliskConfig:
    if scenario == "imaging_record":
        return CommDataBasiliskConfig(duration_s=40.0, step_s=10.0, instrument_baud_bps=1.0e6, storage_capacity_bits=5.0e9, transmitter_baud_bps=0.0, native_storage_drain_enabled=False)
    if scenario == "downlink_pass":
        return CommDataBasiliskConfig(duration_s=40.0, step_s=10.0, instrument_baud_bps=2.0e5, storage_capacity_bits=5.0e9, transmitter_baud_bps=1.0e6, initial_storage_bits=5.0e6, native_storage_drain_enabled=True)
    if scenario == "no_contact_storage_growth":
        return CommDataBasiliskConfig(duration_s=40.0, step_s=10.0, instrument_baud_bps=1.5e6, storage_capacity_bits=5.0e9, transmitter_baud_bps=0.0, native_storage_drain_enabled=False)
    if scenario == "ground_contact":
        return CommDataBasiliskConfig(duration_s=40.0, step_s=10.0, instrument_baud_bps=0.0, storage_capacity_bits=5.0e9, transmitter_baud_bps=2.0e6, initial_storage_bits=8.0e6, native_storage_drain_enabled=True)
    raise ValueError(f"Unsupported Comm/Data normal scenario: {scenario}")


def _run_basilisk_config(
    config: CommDataBasiliskConfig,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
) -> dict[str, object]:
    summary, rows, ctx = run_comm_data_basilisk_scenario(
        config,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
        return_context=True,
    )
    return {
        **asdict(summary),
        "trace_sample_count": len(rows),
        "storage_backlog_growth_bits": float(summary.final_storage_bits - summary.min_storage_bits),
        "storage_growth_rate_bps_proxy": float((summary.final_storage_bits - summary.min_storage_bits) / max(float(summary.duration_s), 1e-9)),
        "downlink_rate_bps_proxy": float(summary.estimated_native_downlinked_bits / max(float(summary.duration_s), 1e-9)),
        "component_sources": dict(ctx.component_sources),
        "basilisk_builder": "subsystems.comm_data.builder.build_comm_data_basilisk_sim",
        "registered_runtime_events": ctx.base_parameters.get("registered_runtime_events", {"fault_events": (), "degradation_events": ()}),
        "runtime_injection_summary": runtime_injection_summary(ctx),
    }


def run_normal_scenario(scenario: str = "imaging_record") -> dict[str, Any]:
    """Run one Comm/Data normal scenario through the subsystem Basilisk builder."""

    return scenario_result("comm_data", scenario, _NORMAL_SCENARIOS[scenario], _run_basilisk_config(_normal_config(scenario)))


def run_normal_scenarios() -> dict[str, Any]:
    """Run every Comm/Data non-fault, non-degradation Basilisk scenario."""

    return run_scenario_batch("comm_data", _NORMAL_SCENARIOS, run_normal_scenario)


def run_nominal_case() -> dict[str, object]:
    """Backward-compatible nominal case now routed through a named normal scenario."""

    return run_normal_scenario("imaging_record")


def run_degradation_case(scenario_name: str = "transmitter_link_aging") -> dict[str, object]:
    """Run a static Comm/Data degradation case through the Basilisk builder."""

    scenario = _default_degradation_scenarios().get(scenario_name) or next(iter(_default_degradation_scenarios().values()))
    cfg = CommDataBasiliskConfig(duration_s=140.0, step_s=10.0, instrument_baud_bps=1.0e6, transmitter_baud_bps=5.0e5, storage_capacity_bits=5.0e9, native_storage_drain_enabled=True)
    update_specs = _build_degradation_update_specs(scenario, update_period_s=10.0)
    result = _run_basilisk_config(cfg, degradation_update_specs=update_specs)
    result["degradation_scenario"] = scenario.name
    result["degradation_update_specs"] = update_specs
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("comm_data", scenario.name, scenario.description, result, mode="degradation")


def run_fault_case(scenario_name: str = "transmitter_power_loss") -> dict[str, object]:
    """Run a static Comm/Data fault case through the Basilisk builder."""

    scenario = _default_fault_scenarios().get(scenario_name) or next(iter(_default_fault_scenarios().values()))
    cfg = CommDataBasiliskConfig(duration_s=430.0, step_s=10.0, instrument_baud_bps=1.0e6, transmitter_baud_bps=1.0e5, storage_capacity_bits=5.0e9, native_storage_drain_enabled=True)
    event_specs = _build_fault_event_specs(scenario)
    result = _run_basilisk_config(cfg, fault_event_specs=event_specs)
    result["fault_scenario"] = scenario.name
    result["fault_event_specs"] = event_specs
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("comm_data", scenario.name, scenario.description, result, mode="fault")


def run_combined_case(fault_scenario_name: str = "antenna_link_outage", degradation_scenario_name: str = "combined_comm_data_aging") -> dict[str, object]:
    """Run a static Comm/Data fault + degradation case through the Basilisk builder."""

    fault_scenario = _default_fault_scenarios().get(fault_scenario_name) or next(iter(_default_fault_scenarios().values()))
    degradation_scenario = _default_degradation_scenarios().get(degradation_scenario_name) or next(iter(_default_degradation_scenarios().values()))
    cfg = CommDataBasiliskConfig(duration_s=430.0, step_s=10.0, instrument_baud_bps=1.0e6, transmitter_baud_bps=1.0e5, storage_capacity_bits=5.0e9, native_storage_drain_enabled=True)
    event_specs = _build_fault_event_specs(fault_scenario)
    update_specs = _build_degradation_update_specs(degradation_scenario, update_period_s=10.0)
    result = _run_basilisk_config(cfg, fault_event_specs=event_specs, degradation_update_specs=update_specs)
    result["fault_scenario"] = fault_scenario.name
    result["degradation_scenario"] = degradation_scenario.name
    result["fault_event_specs"] = event_specs
    result["degradation_update_specs"] = update_specs
    result["combined_with_degradation"] = True
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("comm_data", "comm_data_fault_degradation", "Comm/Data combined fault/degradation scenario assembled through Basilisk builder.", result, mode="combined")


def run_all_modes() -> dict[str, object]:
    return {
        "normal_scenarios": run_normal_scenarios(),
        "degradation": run_degradation_case(),
        "fault_transmitter_power_loss": run_fault_case(),
        "combined_fault_degradation": run_combined_case(),
    }


def harness_available() -> bool:
    return True


def print_summary(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    results = run_all_modes()
    Path("comm_data_subsystem_all_modes.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(results)

# ---------------------------------------------------------------------------
# COMMDATA-RF-NATIVE-1: native SimpleAntenna + LinkBudget harness
# ---------------------------------------------------------------------------


def run_comm_data_rf_native_scenario(config=None):
    """Execute a focused native RF-link scenario using SimpleAntenna + LinkBudget."""
    from Basilisk.architecture import messaging
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.antenna.builder import build_simple_antenna_native, seed_simple_antenna_environment
    from components.antenna.schemas import SimpleAntennaNativeConfig
    from components.link_budget.builder import build_link_budget_native, wire_link_budget_antennas
    from components.link_budget.schemas import LinkBudgetNativeConfig
    from .schemas import CommDataRfNativeConfig, CommDataRfNativeSummary, CommDataRfNativeTraceRow

    cfg = config or CommDataRfNativeConfig()
    if cfg.step_s <= 0 or cfg.duration_s <= 0:
        raise ValueError("duration_s and step_s must be positive")
    if cfg.spacecraft_radius_m <= cfg.ground_radius_m:
        raise ValueError("spacecraft_radius_m must exceed ground_radius_m for this simple focused RF test")

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("commDataRfNativeProcess")
    task_name = "commDataRfNativeTask"
    process.addTask(sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s))))

    sc_ant = build_simple_antenna_native(SimpleAntennaNativeConfig(
        model_tag="commDataSpacecraftSimpleAntenna",
        antenna_name="spacecraft",
        frequency_hz=cfg.frequency_hz,
        bandwidth_hz=cfg.bandwidth_hz,
        directivity_db=cfg.spacecraft_directivity_db,
        hpbw_ratio=cfg.hpbw_ratio,
        tx_power_w=cfg.spacecraft_tx_power_w,
        rx_power_w=cfg.spacecraft_rx_power_w,
        radiation_efficiency=cfg.radiation_efficiency,
        equivalent_noise_temp_k=cfg.equivalent_noise_temp_k,
        environment_temp_k=cfg.environment_temp_k,
        available=cfg.spacecraft_available,
        operating_mode="tx",
        orientation_b=(0.0, -0.41421356237309503, 0.0),
    ))
    ground_ant = build_simple_antenna_native(SimpleAntennaNativeConfig(
        model_tag="commDataGroundSimpleAntenna",
        antenna_name="ground",
        frequency_hz=cfg.frequency_hz,
        bandwidth_hz=cfg.bandwidth_hz,
        directivity_db=cfg.ground_directivity_db,
        hpbw_ratio=cfg.hpbw_ratio,
        tx_power_w=cfg.ground_tx_power_w,
        rx_power_w=cfg.ground_rx_power_w,
        radiation_efficiency=cfg.radiation_efficiency,
        equivalent_noise_temp_k=cfg.equivalent_noise_temp_k,
        environment_temp_k=cfg.environment_temp_k,
        available=cfg.ground_available,
        operating_mode="rx",
    ))
    lb = build_link_budget_native(LinkBudgetNativeConfig(
        model_tag="commDataNativeLinkBudget",
        pointing_loss_enabled=cfg.pointing_loss_enabled,
        frequency_loss_enabled=cfg.frequency_loss_enabled,
        atmospheric_attenuation_enabled=cfg.atmospheric_attenuation_enabled,
    ))
    wire_link_budget_antennas(lb, sc_ant, ground_ant)

    sc_state = messaging.SCStatesMsgPayload()
    sc_state.r_BN_N = [float(cfg.spacecraft_radius_m), 0.0, 0.0]
    sc_state.v_BN_N = [0.0, 0.0, 0.0]
    sc_state.sigma_BN = [0.0, 0.0, 0.0]
    sc_state.omega_BN_B = [0.0, 0.0, 0.0]
    sc_msg = messaging.SCStatesMsg().write(sc_state)
    ground_state = messaging.GroundStateMsgPayload()
    ground_state.r_LN_N = [float(cfg.ground_radius_m), 0.0, 0.0]
    ground_state.r_LP_N = [0.0, 0.0, 0.0]
    ground_state.rHat_LP_N = [1.0, 0.0, 0.0]
    ground_msg = messaging.GroundStateMsg().write(ground_state)
    sc_ant.scStateInMsg.subscribeTo(sc_msg)
    ground_ant.groundStateInMsg.subscribeTo(ground_msg)
    seed_simple_antenna_environment(sc_ant, "space", operating_mode="tx", available=cfg.spacecraft_available)
    seed_simple_antenna_environment(ground_ant, "ground", operating_mode="rx", available=cfg.ground_available)

    period = macros.sec2nano(float(cfg.step_s))
    link_rec = lb.linkBudgetOutPayload.recorder(period)
    sc_rec = sc_ant.antennaOutMsg.recorder(period)
    ground_rec = ground_ant.antennaOutMsg.recorder(period)
    for model in (sc_ant, ground_ant, lb, link_rec, sc_rec, ground_rec):
        sim.AddModelToTask(task_name, model)

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    rows = []
    times = list(link_rec.times())
    for i, t_ns in enumerate(times):
        rows.append(CommDataRfNativeTraceRow(
            time_s=float(t_ns) * macros.NANO2SEC,
            distance_m=float(link_rec.distance[i]),
            frequency_hz=float(link_rec.frequency[i]),
            bandwidth_hz=float(link_rec.bandwidth[i]),
            cnr1=float(link_rec.CNR1[i]),
            cnr2=float(link_rec.CNR2[i]),
            spacecraft_eirp_db=float(sc_rec.P_eirp_dB[i]),
            spacecraft_tx_power_w=float(sc_rec.P_Tx[i]),
            ground_rx_power_w=float(ground_rec.P_Rx[i]),
            spacecraft_state=int(link_rec.antennaState1[i]),
            ground_state=int(link_rec.antennaState2[i]),
        ))
    final = rows[-1] if rows else None
    expected_distance = float(cfg.spacecraft_radius_m - cfg.ground_radius_m)
    checks = {
        "samples": bool(rows),
        "distance": bool(final and abs(final.distance_m - expected_distance) <= max(1.0, expected_distance * 1e-9)),
        "frequency": bool(final and abs(final.frequency_hz - float(cfg.frequency_hz)) <= max(1.0, float(cfg.frequency_hz) * 1e-12)),
        "bandwidth": bool(final and abs(final.bandwidth_hz - float(cfg.bandwidth_hz)) <= max(1.0, float(cfg.bandwidth_hz) * 1e-12)),
        "state": bool(final and final.spacecraft_state == 2 and final.ground_state == 1),
        "eirp": bool(final and final.spacecraft_eirp_db > 0.0),
        "cnr": bool(final and final.cnr2 > 0.0),
    }
    summary = CommDataRfNativeSummary(
        backend="basilisk_native_rf_chain",
        subsystem="comm_data",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        native_modules=(
            "simpleAntenna.SimpleAntenna(spacecraft)",
            "simpleAntenna.SimpleAntenna(ground)",
            "linkBudget.LinkBudget",
            "AntennaLogMsg",
            "LinkBudgetMsg",
        ),
        duration_s=float(cfg.duration_s),
        step_s=float(cfg.step_s),
        sample_count=len(rows),
        distance_m=float(final.distance_m if final else 0.0),
        frequency_hz=float(final.frequency_hz if final else 0.0),
        bandwidth_hz=float(final.bandwidth_hz if final else 0.0),
        cnr1_final=float(final.cnr1 if final else 0.0),
        cnr2_final=float(final.cnr2 if final else 0.0),
        spacecraft_eirp_db_final=float(final.spacecraft_eirp_db if final else 0.0),
        native_fspl_db=float(lb.getL_FSPL()),
        spacecraft_state_final=int(final.spacecraft_state if final else -1),
        ground_state_final=int(final.ground_state if final else -1),
        status="PASS" if all(checks.values()) else "FAIL",
    )
    return summary, tuple(rows), checks


def write_comm_data_rf_native_dataset(output_dir: str | Path, config=None) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows, checks = run_comm_data_rf_native_scenario(config)
    summary_path = output_dir / "comm_data_rf_native_summary.json"
    trace_path = output_dir / "comm_data_rf_native_trace.csv"
    manifest_path = output_dir / "comm_data_rf_native_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(asdict(rows[0]).keys()) if rows else []
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "basilisk_comm_data_rf_native",
        "backend_truth": "Basilisk SimBaseClass + simpleAntenna.SimpleAntenna x2 + linkBudget.LinkBudget",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
        "checks": checks,
        "not_claimed": ["BER/PER/storage-removal closure; this belongs to COMMDATA-ODH-NATIVE-1"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


# ---------------------------------------------------------------------------
# COMMDATA-ODH-NATIVE-1: native SimpleInstrument -> SimpleStorageUnit ->
# DownlinkHandling -> DataNodeUsageMsg storage-removal harness
# ---------------------------------------------------------------------------


def run_comm_data_odh_native_scenario(config=None):
    """Execute a focused native ODH/downlink scenario.

    This path deliberately keeps ``SimpleStorageUnit`` passive: it integrates
    signed ``DataNodeUsageMsg`` publishers.  Payload generation comes from
    ``SimpleInstrument`` and downlink/removal comes from Basilisk
    ``DownlinkHandling``.  A Basilisk ``LinkBudgetMsg`` drives the ODH module so
    BER/PER, packet success/drop and storage-removal rates are computed by the
    native downlink handler, not by the project analytical helper.
    """
    from Basilisk.architecture import messaging
    from Basilisk.simulation import downlinkHandling
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.payload.builder import build_simple_instrument
    from components.data_queue.builder import build_simple_storage_unit
    from .schemas import (
        CommDataOdhNativeConfig,
        CommDataOdhNativeSummary,
        CommDataOdhNativeTraceRow,
    )

    cfg = config or CommDataOdhNativeConfig()
    if cfg.duration_s <= 0.0 or cfg.step_s <= 0.0:
        raise ValueError("duration_s and step_s must be positive")
    if cfg.packet_size_bits <= 0.0:
        raise ValueError("packet_size_bits must be positive")
    if cfg.max_retransmissions < 1:
        raise ValueError("DownlinkHandling requires max_retransmissions >= 1")

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("commDataOdhNativeProcess")
    task_name = "commDataOdhNativeTask"
    process.addTask(sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s))))

    instrument = build_simple_instrument(
        "commDataOdhSimpleInstrument",
        float(cfg.instrument_baud_bps),
        str(cfg.data_name),
    )
    storage = build_simple_storage_unit(
        "commDataOdhSimpleStorageUnit",
        float(cfg.storage_capacity_bits),
        float(cfg.initial_storage_bits),
    )
    storage.addDataNodeToModel(instrument.nodeDataOutMsg)

    link_payload = messaging.LinkBudgetMsgPayload()
    link_payload.antennaName1 = "spacecraft"
    link_payload.antennaName2 = "ground"
    # LinkBudgetMsg uses AntennaStateEnum: spacecraft transmits on path 1,
    # ground antenna receives on path 2.
    link_payload.antennaState1 = 2 if bool(cfg.link_active) else 0
    link_payload.antennaState2 = 1 if bool(cfg.link_active) else 0
    link_payload.CNR1 = max(0.0, float(cfg.cnr_linear))
    link_payload.CNR2 = max(0.0, float(cfg.cnr_linear))
    link_payload.distance = max(0.0, float(cfg.link_distance_m))
    link_payload.bandwidth = max(0.0, float(cfg.link_bandwidth_hz))
    link_payload.frequency = max(0.0, float(cfg.link_frequency_hz))
    link_msg = messaging.LinkBudgetMsg().write(link_payload)

    downlink = downlinkHandling.DownlinkHandling()
    downlink.ModelTag = "commDataNativeDownlinkHandling"
    downlink.nodeDataName = str(cfg.data_name)
    downlink.setBitRateRequest(max(0.0, float(cfg.bit_rate_request_bps)))
    downlink.setPacketSizeBits(float(cfg.packet_size_bits))
    downlink.setMaxRetransmissions(int(cfg.max_retransmissions))
    downlink.setReceiverAntenna(int(cfg.receiver_index))
    if bool(cfg.remove_delivered_only):
        downlink.setRemovalPolicy(downlinkHandling.DownlinkHandling.RemovalPolicy_REMOVE_DELIVERED_ONLY)
    else:
        downlink.setRemovalPolicy(downlinkHandling.DownlinkHandling.RemovalPolicy_REMOVE_ATTEMPTED)
    downlink.setRequireFullPacket(bool(cfg.require_full_packet))
    downlink.linkBudgetInMsg.subscribeTo(link_msg)
    downlink.addStorageUnitToDownlink(storage.storageUnitDataOutMsg)
    storage.addDataNodeToModel(downlink.nodeDataOutMsg)

    for model in (instrument, storage, downlink):
        sim.AddModelToTask(task_name, model)

    period = macros.sec2nano(float(cfg.step_s))
    instrument_rec = instrument.nodeDataOutMsg.recorder(period)
    storage_rec = storage.storageUnitDataOutMsg.recorder(period)
    downlink_node_rec = downlink.nodeDataOutMsg.recorder(period)
    downlink_rec = downlink.downlinkOutMsg.recorder(period)
    for rec in (instrument_rec, storage_rec, downlink_node_rec, downlink_rec):
        sim.AddModelToTask(task_name, rec)

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    rows: list[CommDataOdhNativeTraceRow] = []
    times = list(storage_rec.times())
    for i, t_ns in enumerate(times):
        t_s = float(t_ns) * macros.NANO2SEC
        rows.append(CommDataOdhNativeTraceRow(
            time_s=t_s,
            storage_level_bits=float(storage_rec.storageLevel[i]),
            storage_capacity_bits=float(storage_rec.storageCapacity[i]),
            storage_net_baud_bps=float(storage_rec.currentNetBaud[i]),
            instrument_baud_bps=float(instrument_rec.baudRate[i]),
            downlink_node_baud_bps=float(downlink_node_rec.baudRate[i]),
            link_active=bool(downlink_rec.linkActive[i]),
            bit_rate_request_bps=float(downlink_rec.bitRateRequest[i]),
            attempted_data_rate_bps=float(downlink_rec.attemptedDataRate[i]),
            delivered_data_rate_bps=float(downlink_rec.deliveredDataRate[i]),
            dropped_data_rate_bps=float(downlink_rec.droppedDataRate[i]),
            storage_removal_rate_bps=float(downlink_rec.storageRemovalRate[i]),
            cumulative_delivered_bits=float(downlink_rec.cumulativeDeliveredBits[i]),
            cumulative_dropped_bits=float(downlink_rec.cumulativeDroppedBits[i]),
            cumulative_removed_bits=float(downlink_rec.cumulativeRemovedBits[i]),
            ber=float(downlink_rec.ber[i]),
            per=float(downlink_rec.per[i]),
            packet_success_prob=float(downlink_rec.packetSuccessProb[i]),
            packet_drop_prob=float(downlink_rec.packetDropProb[i]),
            ebn0_db=float(downlink_rec.ebN0_dB[i]),
        ))

    final = rows[-1] if rows else None
    status = "PASS" if rows else "FAIL"
    if final is not None and cfg.link_active:
        status = "PASS" if final.cumulative_delivered_bits > 0.0 and final.cumulative_removed_bits > 0.0 else "FAIL"
    if final is not None and not cfg.link_active:
        status = "PASS" if final.cumulative_delivered_bits == 0.0 and final.storage_level_bits > rows[0].storage_level_bits else "FAIL"

    summary = CommDataOdhNativeSummary(
        backend="basilisk_native_odh_chain",
        subsystem="comm_data",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        native_modules=(
            "simpleInstrument.SimpleInstrument",
            "simpleStorageUnit.SimpleStorageUnit",
            "downlinkHandling.DownlinkHandling",
            "DataNodeUsageMsg",
            "DataStorageStatusMsg",
            "LinkBudgetMsg",
            "DownlinkHandlingMsg",
        ),
        message_contracts=(
            "SimpleInstrument.nodeDataOutMsg -> SimpleStorageUnit.addDataNodeToModel",
            "DataStorageStatusMsg + LinkBudgetMsg -> DownlinkHandling",
            "DownlinkHandling.nodeDataOutMsg -> SimpleStorageUnit.addDataNodeToModel",
        ),
        duration_s=float(cfg.duration_s),
        step_s=float(cfg.step_s),
        sample_count=len(rows),
        final_storage_bits=float(final.storage_level_bits if final else 0.0),
        max_storage_bits=max((r.storage_level_bits for r in rows), default=0.0),
        min_storage_bits=min((r.storage_level_bits for r in rows), default=0.0),
        delivered_bits=float(final.cumulative_delivered_bits if final else 0.0),
        dropped_bits=float(final.cumulative_dropped_bits if final else 0.0),
        removed_bits=float(final.cumulative_removed_bits if final else 0.0),
        final_ber=float(final.ber if final else 0.0),
        final_per=float(final.per if final else 0.0),
        final_packet_success_prob=float(final.packet_success_prob if final else 0.0),
        link_active=bool(cfg.link_active),
        status=status,
    )
    checks = {
        "samples": bool(rows),
        "uses_signed_data_nodes": bool(rows and any(r.downlink_node_baud_bps < 0.0 for r in rows[1:])),
        "downlink_msg_outputs": bool(rows and all(hasattr(row, "ber") and hasattr(row, "per") for row in rows)),
        "storage_not_rate_configured": True,
    }
    return summary, tuple(rows), checks


def write_comm_data_odh_native_dataset(output_dir: str | Path, config=None) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows, checks = run_comm_data_odh_native_scenario(config)
    summary_path = output_dir / "comm_data_odh_native_summary.json"
    trace_path = output_dir / "comm_data_odh_native_trace.csv"
    manifest_path = output_dir / "comm_data_odh_native_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(asdict(rows[0]).keys()) if rows else list(summary.__dataclass_fields__)
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "basilisk_comm_data_odh_native",
        "backend_truth": "Basilisk SimBaseClass + SimpleInstrument + SimpleStorageUnit + DownlinkHandling",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
        "checks": checks,
        "not_claimed": ["V7 LinkBudget CNR directionality; V8 uses a controlled LinkBudgetMsg to exercise DownlinkHandling"],
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}
