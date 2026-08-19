"""EPS subsystem runner backed by the Basilisk EPS/PDU path."""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from components.battery.degradation import BatteryDegradation
from components.solar_panel.degradation import SolarPanelDegradation
from components.battery.faults import BatteryFaultType
from components.fault_spec import FaultSpec
from components.solar_panel.faults import SolarPanelFaultType
from components.power_sink.builder import demand_w

from .builder import (
    EpsBasiliskConfig,
    EpsBasiliskModuleGraph,
    apply_eps_config_faults,
    build_eps_basilisk_sim,
    build_nominal_eps_config,
)
from .faults import default_fault_scenarios, build_fault_event_specs
from .degradation import EPSDegradation, default_degradation_scenarios, build_degradation_update_specs
from ..runner_common import scenario_result, run_scenario_batch
from ..runtime_injection import attach_runtime_injection_events, runtime_injection_summary
from .schemas import (
    EPSBasiliskConfig,
    EPSBasiliskSummary,
    EPSBasiliskTraceRow,
    EPSNativeConfig,
    EPSNativeSummary,
    EPSNativeTraceRow,
    EpsConfig,
)


def _safe_float_array(rec, attr: str, default: float = 0.0) -> list[float]:
    if not hasattr(rec, attr):
        return []
    return [float(x) for x in getattr(rec, attr)]


def _eps_public_row(row: EPSBasiliskTraceRow) -> dict[str, Any]:
    return {
        "time_s": row.time_s,
        "eps.battery_storage_j": row.battery_storage_j,
        "eps.battery_capacity_j": row.battery_capacity_j,
        "eps.battery_soc": row.battery_soc,
        "eps.solar_power_w": row.solar_power_w,
        "eps.bus_load_w": row.bus_load_w,
        "eps.payload_load_enabled_w": row.payload_load_enabled_w,
        "eps.adcs_load_enabled_w": row.adcs_load_enabled_w,
        "eps.comm_load_enabled_w": row.comm_load_enabled_w,
        "eps.heater_load_enabled_w": row.heater_load_enabled_w,
        "eps.net_power_w": row.net_power_w,
        "eps.load_shed_active": row.load_shed_active,
        "eps.shed_reason": row.shed_reason,
    }


def _eps_rows_from_recorders(rec: Mapping[str, Any], cfg: EPSBasiliskConfig, pdu: Any) -> list[EPSBasiliskTraceRow]:
    """Build EPS rows from one internally sampled Basilisk recorder group."""
    from Basilisk.utilities import macros

    p_net = _safe_float_array(rec["payload"], "netPower")
    a_net = _safe_float_array(rec["adcs"], "netPower")
    c_net = _safe_float_array(rec["comm"], "netPower")
    h_net = _safe_float_array(rec["heater"], "netPower")
    rw_net = _safe_float_array(rec.get("rw_power"), "netPower") if "rw_power" in rec else []
    antenna_net = _safe_float_array(rec.get("antenna_power"), "netPower") if "antenna_power" in rec else []
    s_net = _safe_float_array(rec["solar"], "netPower")
    b_net = _safe_float_array(rec["bus"], "netPower")
    decision_by_time = {round(row.time_s, 9): row for row in pdu.trace}
    rows: list[EPSBasiliskTraceRow] = []
    for i, t_ns in enumerate(list(rec["battery"].times())):
        t_s = float(t_ns) * macros.NANO2SEC
        storage_j = float(rec["battery"].storageLevel[i])
        capacity_j = float(rec["battery"].storageCapacity[i])
        soc = storage_j / capacity_j if capacity_j else 0.0
        decision = decision_by_time.get(round(t_s, 9))
        payload_enabled = bool(rec["payload_status"].deviceStatus[i]) if len(rec["payload_status"].deviceStatus) > i else bool(decision.payload_enabled if decision else False)
        adcs_enabled = bool(rec["adcs_status"].deviceStatus[i]) if len(rec["adcs_status"].deviceStatus) > i else bool(decision.adcs_enabled if decision else False)
        comm_enabled = bool(rec["comm_status"].deviceStatus[i]) if len(rec["comm_status"].deviceStatus) > i else bool(decision.comm_enabled if decision else False)
        heater_enabled = bool(rec["heater_status"].deviceStatus[i]) if len(rec["heater_status"].deviceStatus) > i else bool(decision.heater_enabled if decision else False)
        load_shed = bool(decision.load_shed_active) if decision else False
        reason = decision.shed_reason if decision else "none"
        solar_w = float(s_net[i]) if len(s_net) > i else abs(float(cfg.solar_power_w))
        bus_load_w = -float(b_net[i]) if len(b_net) > i else abs(float(cfg.bus_power_w))
        payload_enabled_w = -float(p_net[i]) if len(p_net) > i else 0.0
        adcs_enabled_w = -float(a_net[i]) if len(a_net) > i else 0.0
        comm_enabled_w = -float(c_net[i]) if len(c_net) > i else 0.0
        heater_enabled_w = -float(h_net[i]) if len(h_net) > i else 0.0
        rw_power_w = -float(rw_net[i]) if len(rw_net) > i else 0.0
        antenna_power_w = -float(antenna_net[i]) if len(antenna_net) > i else 0.0
        net = solar_w - bus_load_w - payload_enabled_w - adcs_enabled_w - comm_enabled_w - heater_enabled_w - rw_power_w - antenna_power_w
        rows.append(EPSBasiliskTraceRow(
            time_s=t_s, battery_storage_j=storage_j, battery_capacity_j=capacity_j, battery_soc=soc,
            solar_power_w=solar_w, bus_load_w=bus_load_w,
            payload_load_requested_w=abs(float(cfg.payload_power_w)) if cfg.payload_requested else 0.0,
            payload_load_enabled_w=payload_enabled_w,
            adcs_load_requested_w=abs(float(cfg.adcs_power_w)) if cfg.adcs_requested else 0.0,
            adcs_load_enabled_w=adcs_enabled_w,
            comm_load_requested_w=abs(float(cfg.comm_power_w)) if cfg.comm_requested else 0.0,
            comm_load_enabled_w=comm_enabled_w,
            heater_load_requested_w=abs(float(cfg.heater_power_w)) if cfg.heater_requested else 0.0,
            heater_load_enabled_w=heater_enabled_w, rw_power_w=rw_power_w, antenna_power_w=antenna_power_w,
            battery_fault_capacity_ratio=cfg.battery_fault_capacity_ratio, solar_panel_enabled=bool(cfg.solar_panel_enabled),
            payload_enabled=payload_enabled, adcs_enabled=adcs_enabled, comm_enabled=comm_enabled, heater_enabled=heater_enabled,
            load_shed_active=load_shed, shed_reason=reason, net_power_w=net,
        ))
    return rows


def run_eps_basilisk_scenario(
    config: EPSBasiliskConfig | None = None,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
    telemetry_streams: Sequence[Mapping[str, Any]] = (),
    return_context: bool = False,
):
    """Execute a focused EPS Basilisk scenario."""

    ctx = build_eps_basilisk_sim(config)
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
        "battery": ctx.modules["battery"].batPowerOutMsg,
        "payload": ctx.modules["payload"].nodePowerOutMsg,
        "adcs": ctx.modules["adcs"].nodePowerOutMsg,
        "comm": ctx.modules["comm"].nodePowerOutMsg,
        "heater": ctx.modules["heater"].nodePowerOutMsg,
        "rw_power": ctx.modules["rw_power"].nodePowerOutMsg,
        "antenna_power": ctx.modules["antenna_power"].nodePowerOutMsg,
        "solar": ctx.message_handles["solar_power"],
        "bus": ctx.modules["bus"].nodePowerOutMsg,
        "payload_status": ctx.modules["pdu"].payloadStatusOutMsg,
        "adcs_status": ctx.modules["pdu"].adcsStatusOutMsg,
        "comm_status": ctx.modules["pdu"].commStatusOutMsg,
        "heater_status": ctx.modules["pdu"].heaterStatusOutMsg,
    }
    recorder_groups = attach_grouped_message_recorders(
        sim=sim, task_name=ctx.task_name, message_handles=recorder_messages, streams=telemetry_streams
    )
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    rec = ctx.recorders
    pdu = ctx.modules["pdu"]
    rows = _eps_rows_from_recorders(rec, cfg, pdu)
    native_streams: list[dict[str, Any]] = []
    for group in recorder_groups.values():
        group_rows = _eps_rows_from_recorders(group.recorders, cfg, pdu)
        native_streams.append({
            "stream_id": group.spec.stream_id,
            "sample_s": group.spec.sample_s,
            "format": group.spec.format,
            "fields": list(group.spec.fields),
            "rows": [_eps_public_row(row) for row in group_rows],
            "recorder_sources": sorted(group.recorders),
            "task_name": group.task_name,
            "native_recorder": True,
        })
    if isinstance(ctx.base_parameters, dict):
        ctx.base_parameters["native_multi_rate_telemetry"] = {
            **group_manifest(recorder_groups),
            "streams": native_streams,
        }

    initial_j = float(cfg.battery_capacity_wh) * 3600.0 * max(0.0, min(1.0, float(cfg.initial_soc)))
    final_j = rows[-1].battery_storage_j if rows else initial_j
    reconstructed = initial_j
    for row in rows[1:]:
        reconstructed = max(0.0, min(float(cfg.battery_capacity_wh) * 3600.0, reconstructed + row.net_power_w * float(cfg.step_s)))
    load_shed_count = sum(1 for row in rows if row.load_shed_active)
    status = "PASS" if rows and all(row.battery_storage_j == row.battery_storage_j for row in rows) else "FAIL"
    native_modules_list = [
        "simpleBattery.SimpleBattery",
        "simplePowerSink.SimplePowerSink",
        "ReactionWheelPower.ReactionWheelPower",
        "antennaPower.AntennaPower",
        "components.battery.builder.build_simple_battery",
        "components.battery.builder.write_battery_capacity_fault_message",
        "components.power_sink.builder.build_simple_power_sink",
        "components.reaction_wheel.builder.build_reaction_wheel_power_node",
        "components.antenna.builder.build_antenna_power_node",
    ]
    if bool(ctx.base_parameters.get("use_native_solar_panel")):
        native_modules_list.append("simpleSolarPanel.SimpleSolarPanel")
        native_modules_list.append("components.solar_panel.builder.build_simple_solar_panel")
    summary = EPSBasiliskSummary(
        backend="basilisk_modules_plus_scheduled_pdu" + ("_with_simpleSolarPanel" if bool(ctx.base_parameters.get("use_native_solar_panel")) else ""),
        subsystem="eps",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        native_modules=tuple(native_modules_list),
        custom_modules=("components.pdu.builder.PduLoadSheddingSysModel", "components.pdu.builder.ConstantDeviceRequest"),
        message_contracts=("PowerNodeUsageMsg", "PowerStorageStatusMsg", "PowerStorageFaultMsg", "DeviceCmdMsg", "DeviceStatusMsg"),
        duration_s=float(cfg.duration_s),
        step_s=float(cfg.step_s),
        sample_count=len(rows),
        initial_storage_j=initial_j,
        final_storage_j=final_j,
        expected_final_storage_j=reconstructed,
        final_soc=rows[-1].battery_soc if rows else max(0.0, min(1.0, float(cfg.initial_soc))),
        load_shed_event_count=load_shed_count,
        status=status,
        not_claimed=("native_basilisk_cxx_pdu", "flight_grade_eps_controller", "mppt_bcr_bus_regulator", "electrochemical_charge_discharge_efficiency"),
    )
    if isinstance(ctx.base_parameters, dict):
        ctx.base_parameters["registered_runtime_events"] = registered_runtime_events
    if return_context:
        return summary, tuple(rows), ctx
    return summary, tuple(rows)


# Compatibility API retained after removing the legacy Basilisk compatibility module.
run_eps_native_scenario = run_eps_basilisk_scenario


def write_eps_basilisk_dataset(output_dir: str | Path, config: EPSBasiliskConfig | None = None) -> dict[str, str]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    cfg = config or EPSBasiliskConfig(solar_power_w=120.0, payload_power_w=35.0, adcs_power_w=8.0, comm_power_w=12.0, heater_power_w=6.0, bus_power_w=12.0, initial_soc=0.62)
    summary, rows = run_eps_basilisk_scenario(cfg)
    summary_path = output_dir / "eps_basilisk_summary.json"
    trace_path = output_dir / "eps_basilisk_trace.csv"
    manifest_path = output_dir / "eps_basilisk_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        fieldnames = list(asdict(rows[0]).keys()) if rows else list(EPSBasiliskTraceRow.__annotations__.keys())
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "basilisk_eps_with_scheduled_pdu",
        "backend_truth": "Basilisk SimBaseClass + simpleBattery/simplePowerSink load nodes + Basilisk Python SysModel PDU/load shedding",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


write_eps_native_dataset = write_eps_basilisk_dataset


def _native_config_from_eps_config(*, battery_capacity_wh: float, initial_soc: float, solar_power_w: float, loads_w: dict[str, float]) -> EPSBasiliskConfig:
    return EPSBasiliskConfig(
        battery_capacity_wh=float(battery_capacity_wh),
        initial_soc=float(initial_soc),
        solar_power_w=float(solar_power_w),
        bus_power_w=float(loads_w.get("obc", 0.0) + loads_w.get("thermal", 0.0)),
        payload_power_w=float(loads_w.get("payload", 0.0)),
        adcs_power_w=float(loads_w.get("adcs", 0.0)),
        comm_power_w=float(loads_w.get("comm", 0.0)),
        heater_power_w=float(loads_w.get("heater", 0.0)),
    )


def _run_eps_case(cfg: EpsConfig) -> dict[str, Any]:
    loads_w = {name: float(demand_w(load, "payload")) for name, load in cfg.loads.items()}
    native_cfg = _native_config_from_eps_config(
        battery_capacity_wh=cfg.battery.capacity_wh,
        initial_soc=cfg.battery.initial_soc,
        solar_power_w=cfg.solar_panel.max_power_w * cfg.solar_panel.efficiency,
        loads_w=loads_w,
    )
    summary, rows = run_eps_basilisk_scenario(native_cfg)
    return {
        "backend": summary.backend,
        "status": summary.status,
        "steps": summary.sample_count,
        "initial_soc": native_cfg.initial_soc,
        "final_soc": summary.final_soc,
        "min_soc": min((row.battery_soc for row in rows), default=native_cfg.initial_soc),
        "max_soc": max((row.battery_soc for row in rows), default=native_cfg.initial_soc),
        "shed_events": summary.load_shed_event_count,
        "battery_capacity": cfg.battery.capacity_wh,
        "solar_max_power": cfg.solar_panel.max_power_w,
    }


def _legacy_run_nominal_case_impl_2() -> dict[str, Any]:
    return _run_eps_case(build_nominal_eps_config())


def _legacy_run_degradation_case_impl_2(degradation: EPSDegradation) -> dict[str, Any]:
    cfg = build_nominal_eps_config(degradation=degradation)
    result = _run_eps_case(cfg)
    result["degradation"] = {
        "battery_capacity_loss_pct": degradation.battery_degradation.capacity_loss_pct,
        "solar_efficiency_loss_pct": degradation.solar_panel_degradation.efficiency_loss_pct,
        "pdu_efficiency_loss_pct": degradation.pdu_efficiency_loss_pct,
    }
    return result


def _legacy_run_fault_case_impl_2(fault_specs: list[FaultSpec]) -> dict[str, Any]:
    cfg = apply_eps_config_faults(build_nominal_eps_config(), fault_specs)
    result = _run_eps_case(cfg)
    result["faults"] = [
        {"type": str(f.fault_type), "magnitude": f.magnitude, "target_id": f.target_id}
        for f in fault_specs
    ]
    return result


def _legacy_run_combined_case_impl_2(fault_specs: list[FaultSpec] | None = None, degradation: EPSDegradation | None = None) -> dict[str, Any]:
    cfg = build_nominal_eps_config(degradation=degradation or EPSDegradation(
        battery_degradation=BatteryDegradation(capacity_loss_pct=15.0),
        solar_panel_degradation=SolarPanelDegradation(efficiency_loss_pct=10.0),
        pdu_efficiency_loss_pct=5.0,
    ))
    cfg = apply_eps_config_faults(cfg, fault_specs or [])
    result = _run_eps_case(cfg)
    result["faults"] = [
        {"type": str(f.fault_type), "magnitude": f.magnitude, "target_id": f.target_id}
        for f in (fault_specs or [])
    ]
    result["combined_with_degradation"] = True
    return result


def run_and_save_nominal_case(save_dir: str | Path) -> dict[str, object]:
    outputs = write_eps_basilisk_dataset(Path(save_dir))
    summary = run_nominal_case()
    summary.update(outputs)
    return summary


_LEGACY_RUN_NOMINAL_CASE = _legacy_run_nominal_case_impl_2
_LEGACY_RUN_DEGRADATION_CASE = _legacy_run_degradation_case_impl_2
_LEGACY_RUN_FAULT_CASE = _legacy_run_fault_case_impl_2
_LEGACY_RUN_COMBINED_CASE = _legacy_run_combined_case_impl_2

_NORMAL_SCENARIOS = {
    "sunlit_charge": "Solar generation exceeds subsystem loads and charges the battery.",
    "eclipse_discharge": "Solar generation is absent and the battery supports essential loads.",
    "payload_peak": "Payload peak load stresses PDU load-shedding thresholds.",
    "safe_mode": "Non-essential loads are disabled for low-power safe mode.",
}


def list_normal_scenarios() -> list[str]:
    """Return EPS non-fault, non-degradation Basilisk operating scenarios."""

    return list(_NORMAL_SCENARIOS.keys())


def _normal_config(scenario: str) -> EPSBasiliskConfig:
    if scenario == "sunlit_charge":
        return EPSBasiliskConfig(duration_s=60.0, step_s=10.0, use_simple_solar_panel=False, initial_soc=0.40, solar_power_w=160.0, bus_power_w=10.0, payload_power_w=20.0, adcs_power_w=8.0, comm_power_w=6.0, heater_power_w=0.0)
    if scenario == "eclipse_discharge":
        return EPSBasiliskConfig(duration_s=60.0, step_s=10.0, use_simple_solar_panel=False, initial_soc=0.80, solar_power_w=0.0, bus_power_w=12.0, payload_power_w=25.0, adcs_power_w=8.0, comm_power_w=5.0, heater_power_w=4.0)
    if scenario == "payload_peak":
        return EPSBasiliskConfig(duration_s=60.0, step_s=10.0, use_simple_solar_panel=False, initial_soc=0.70, solar_power_w=90.0, bus_power_w=12.0, payload_power_w=75.0, adcs_power_w=10.0, comm_power_w=10.0, heater_power_w=4.0, payload_min_soc=0.55)
    if scenario == "safe_mode":
        return EPSBasiliskConfig(duration_s=60.0, step_s=10.0, use_simple_solar_panel=False, initial_soc=0.35, solar_power_w=40.0, bus_power_w=8.0, payload_power_w=0.0, adcs_power_w=5.0, comm_power_w=0.0, heater_power_w=2.0, payload_requested=False, comm_requested=False)
    raise ValueError(f"Unsupported EPS normal scenario: {scenario}")


def _run_basilisk_config(
    config: EPSBasiliskConfig,
    *,
    fault_event_specs: tuple[dict[str, Any], ...] = (),
    degradation_update_specs: tuple[dict[str, Any], ...] = (),
) -> dict[str, Any]:
    summary, rows, ctx = run_eps_basilisk_scenario(
        config,
        fault_event_specs=fault_event_specs,
        degradation_update_specs=degradation_update_specs,
        return_context=True,
    )
    return {
        **asdict(summary),
        "trace_sample_count": len(rows),
        "min_soc": min((row.battery_soc for row in rows), default=float(config.initial_soc)),
        "max_soc": max((row.battery_soc for row in rows), default=float(config.initial_soc)),
        "storage_delta_j": float(summary.final_storage_j - summary.initial_storage_j),
        "battery_energy_delta_wh": float((summary.final_storage_j - summary.initial_storage_j) / 3600.0),
        "mean_net_power_w_proxy": float((summary.final_storage_j - summary.initial_storage_j) / max(float(summary.duration_s), 1e-9)),
        "component_sources": dict(ctx.component_sources),
        "basilisk_builder": "subsystems.eps.builder.build_eps_basilisk_sim",
        "registered_runtime_events": ctx.base_parameters.get("registered_runtime_events", {"fault_events": (), "degradation_events": ()}),
        "runtime_injection_summary": runtime_injection_summary(ctx),
    }


def run_normal_scenario(scenario: str = "sunlit_charge") -> dict[str, Any]:
    """Run one EPS normal scenario through the subsystem Basilisk builder."""

    return scenario_result("eps", scenario, _NORMAL_SCENARIOS[scenario], _run_basilisk_config(_normal_config(scenario)))


def run_normal_scenarios() -> dict[str, Any]:
    """Run every EPS non-fault, non-degradation Basilisk scenario."""

    return run_scenario_batch("eps", _NORMAL_SCENARIOS, run_normal_scenario)


def run_nominal_case() -> dict[str, Any]:
    """Backward-compatible nominal case now routed through a named normal scenario."""

    return run_normal_scenario("sunlit_charge")


def _eps_degradation_static_config(scenario_name: str) -> EPSBasiliskConfig:
    cfg = _normal_config("payload_peak")
    if scenario_name == "source_storage_aging":
        battery_factor, solar_factor, load_factor = 0.72, 0.78, 1.0
    elif scenario_name == "distribution_load_aging":
        battery_factor, solar_factor, load_factor = 1.0, 1.0, 1.15
    else:
        battery_factor, solar_factor, load_factor = 0.75, 0.80, 1.12
    return EPSBasiliskConfig(
        duration_s=140.0,
        step_s=10.0,
        use_simple_solar_panel=False,
        battery_capacity_wh=cfg.battery_capacity_wh * battery_factor,
        initial_soc=cfg.initial_soc,
        solar_power_w=cfg.solar_power_w * solar_factor,
        bus_power_w=cfg.bus_power_w * load_factor,
        payload_power_w=cfg.payload_power_w * load_factor,
        adcs_power_w=cfg.adcs_power_w,
        comm_power_w=cfg.comm_power_w,
        heater_power_w=cfg.heater_power_w,
        payload_min_soc=cfg.payload_min_soc,
        comm_min_soc=cfg.comm_min_soc,
        heater_min_soc=cfg.heater_min_soc,
        adcs_min_soc=cfg.adcs_min_soc,
        recovery_soc=cfg.recovery_soc,
    )


def run_degradation_case(degradation: EPSDegradation | str | None = None) -> dict[str, Any]:
    """Run an EPS component-aggregated degradation scenario."""

    scenarios = default_degradation_scenarios()
    if isinstance(degradation, EPSDegradation):
        scenario = scenarios["combined_eps_aging"]
        cfg = _normal_config("payload_peak")
        degraded_cfg = EPSBasiliskConfig(
            duration_s=140.0,
            step_s=10.0,
            use_simple_solar_panel=False,
            battery_capacity_wh=cfg.battery_capacity_wh * (1.0 - degradation.battery_degradation.capacity_loss_pct / 100.0),
            initial_soc=cfg.initial_soc,
            solar_power_w=cfg.solar_power_w * (1.0 - degradation.solar_panel_degradation.efficiency_loss_pct / 100.0),
            bus_power_w=cfg.bus_power_w,
            payload_power_w=cfg.payload_power_w,
            adcs_power_w=cfg.adcs_power_w,
            comm_power_w=cfg.comm_power_w,
            heater_power_w=cfg.heater_power_w,
            payload_min_soc=cfg.payload_min_soc,
            comm_min_soc=cfg.comm_min_soc,
            heater_min_soc=cfg.heater_min_soc,
            adcs_min_soc=cfg.adcs_min_soc,
            recovery_soc=cfg.recovery_soc,
        )
    else:
        scenario = scenarios[degradation or "combined_eps_aging"]
        degraded_cfg = _eps_degradation_static_config(scenario.name)
    update_specs = build_degradation_update_specs(scenario, update_period_s=10.0)
    result = _run_basilisk_config(degraded_cfg, degradation_update_specs=update_specs)
    result["degradation_scenario"] = scenario.name
    result["degradation_update_specs"] = update_specs
    result["covered_components"] = sorted({binding.component for binding in scenario.component_degradations})
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("eps", scenario.name, scenario.description, result, mode="degradation")


def _eps_fault_static_config(scenario_name: str) -> EPSBasiliskConfig:
    cfg = _normal_config("payload_peak")
    battery_factor = 1.0
    solar_factor = 1.0
    load_factor = 1.0
    if scenario_name in {"battery_capacity_loss", "combined_eps_fault"}:
        battery_factor = 0.5
    if scenario_name in {"solar_string_loss", "combined_eps_fault"}:
        solar_factor = 0.45
    if scenario_name in {"power_sink_overload", "combined_eps_fault"}:
        load_factor = 1.35
    if scenario_name == "pdu_channel_trip":
        load_factor = 0.7
    return EPSBasiliskConfig(
        duration_s=430.0,
        step_s=10.0,
        use_simple_solar_panel=False,
        battery_capacity_wh=cfg.battery_capacity_wh * battery_factor,
        initial_soc=cfg.initial_soc,
        solar_power_w=cfg.solar_power_w * solar_factor,
        bus_power_w=cfg.bus_power_w * load_factor,
        payload_power_w=cfg.payload_power_w * load_factor,
        adcs_power_w=cfg.adcs_power_w,
        comm_power_w=cfg.comm_power_w,
        heater_power_w=cfg.heater_power_w,
        payload_min_soc=cfg.payload_min_soc,
        comm_min_soc=cfg.comm_min_soc,
        heater_min_soc=cfg.heater_min_soc,
        adcs_min_soc=cfg.adcs_min_soc,
        recovery_soc=cfg.recovery_soc,
    )


def run_fault_case(fault_specs: list[FaultSpec] | str | None = None) -> dict[str, Any]:
    """Run an EPS component-aggregated fault scenario."""

    scenarios = default_fault_scenarios()
    scenario = scenarios["battery_capacity_loss" if isinstance(fault_specs, list) or fault_specs is None else fault_specs]
    event_specs = build_fault_event_specs(scenario)
    result = _run_basilisk_config(_eps_fault_static_config(scenario.name), fault_event_specs=event_specs)
    result["fault_scenario"] = scenario.name
    result["fault_event_specs"] = event_specs
    result["covered_components"] = sorted({binding.component for binding in scenario.component_faults})
    result["runtime_injection"] = "basilisk_createNewEvent_direct"
    return scenario_result("eps", scenario.name, scenario.description, result, mode="fault")


def run_combined_case(fault_specs: list[FaultSpec] | str | None = None, degradation: EPSDegradation | str | None = None) -> dict[str, Any]:
    """Run static EPS fault + degradation case through the Basilisk builder."""

    fault_scenario = default_fault_scenarios()["solar_string_loss" if isinstance(fault_specs, list) or fault_specs is None else fault_specs]
    degradation_scenario = default_degradation_scenarios()["distribution_load_aging" if isinstance(degradation, EPSDegradation) or degradation is None else degradation]
    cfg = _normal_config("payload_peak")
    combined_cfg = EPSBasiliskConfig(
        duration_s=430.0,
        step_s=10.0,
        use_simple_solar_panel=False,
        battery_capacity_wh=cfg.battery_capacity_wh * 0.85,
        initial_soc=cfg.initial_soc,
        solar_power_w=cfg.solar_power_w * 0.65,
        bus_power_w=cfg.bus_power_w * 1.08,
        payload_power_w=cfg.payload_power_w * 1.08,
        adcs_power_w=cfg.adcs_power_w,
        comm_power_w=cfg.comm_power_w,
        heater_power_w=cfg.heater_power_w,
        payload_min_soc=cfg.payload_min_soc,
        comm_min_soc=cfg.comm_min_soc,
        heater_min_soc=cfg.heater_min_soc,
        adcs_min_soc=cfg.adcs_min_soc,
        recovery_soc=cfg.recovery_soc,
    )
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
    return scenario_result("eps", "eps_fault_degradation", "EPS combined component fault/degradation scenario assembled through Basilisk builder.", result, mode="combined")


def run_all_modes() -> dict[str, Any]:
    return {
        "normal_scenarios": run_normal_scenarios(),
        "degradation": run_degradation_case(),
        "fault_battery_capacity_loss": run_fault_case(),
        "combined_fault_degradation": run_combined_case(),
    }


def harness_available() -> bool:
    return True


def print_summary(data: dict[str, Any]) -> None:
    print(json.dumps(data, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    results = run_all_modes()
    Path("eps_subsystem_all_modes.json").write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print_summary(results)


# --- Basilisk EPS smoke runner migrated from legacy basilisk_impl.py ---

@dataclass(frozen=True)
class EpsBasiliskSmokeResult:
    """Result summary for optional Basilisk bridge smoke runs."""

    basilisk_available: bool
    ran_simulation: bool
    initial_storage_j: float
    final_storage_j: float
    expected_net_power_w: float
    note: str = ""


def basilisk_available() -> bool:
    """Return True if Basilisk imports in the current environment."""

    try:
        import Basilisk  # noqa: F401
        return True
    except Exception:
        return False


def require_basilisk() -> None:
    """Raise a clear error if Basilisk is unavailable."""

    if not basilisk_available():
        raise RuntimeError("Basilisk is not available. Install bsk/Basilisk to run EPS Basilisk bridge tests.")


def eps_config_to_basilisk_config(cfg: EpsConfig, mode: str = "nominal", shadow_factor: float = 1.0) -> EpsBasiliskConfig:
    """Build a minimal Basilisk bridge config from the Python EPS config.

    This conversion intentionally preserves only Basilisk simplePower-compatible
    quantities: battery capacity/initial storage, deterministic solar power, and
    simple load sink powers.  PDU and low-SOC strategy remain in the Python EPS
    layer.
    """

    solar_power_w = max(0.0, float(cfg.solar_panel.max_power_w) * float(cfg.solar_panel.efficiency) * max(0.0, min(1.0, float(shadow_factor))))
    loads_w = {name: max(0.0, float(demand_w(load_cfg, mode=mode, enabled=True))) for name, load_cfg in cfg.loads.items()}
    return EpsBasiliskConfig(
        battery_capacity_wh=float(cfg.battery.capacity_wh),
        initial_soc=float(cfg.battery.initial_soc),
        solar_power_w=solar_power_w,
        loads_w=loads_w,
    )


def build_basilisk_eps_graph(cfg: EpsBasiliskConfig) -> EpsBasiliskModuleGraph:
    """Create a Basilisk simplePower module graph without running it.

    The graph uses component-level factories from ``components/*/builder.py``:

    * ``build_simple_battery`` for storage integration;
    * ``build_simple_power_sink`` with positive ``nodePowerOut`` as a
      deterministic solar power source for weak-supportData tests;
    * one ``build_simple_power_sink`` per load with negative power;
    * ``build_simple_solar_panel`` when available (not wired in smoke tests).
    """

    require_basilisk()
    from components.battery.builder import build_simple_battery
    from components.power_sink.builder import build_simple_power_sink
    from components.solar_panel.builder import build_simple_solar_panel

    battery = build_simple_battery(
        "EPS_Battery",
        float(cfg.battery_capacity_wh),
        float(cfg.initial_soc),
    )

    solar_source = build_simple_power_sink(
        "EPS_DeterministicSolarPowerSource",
        max(0.0, float(cfg.solar_power_w)),
    )
    battery.addPowerNodeToModel(solar_source.nodePowerOutMsg)

    load_sinks: dict[str, Any] = {}
    for name, power_w in cfg.loads_w.items():
        sink = build_simple_power_sink(
            f"EPS_Load_{name}",
            -max(0.0, float(power_w)),
        )
        battery.addPowerNodeToModel(sink.nodePowerOutMsg)
        load_sinks[str(name)] = sink

    simple_solar_panel_obj: Any | None = None
    try:
        simple_solar_panel_obj = build_simple_solar_panel(
            model_tag="EPS_SimpleSolarPanel_Unwired",
            panel_normal_b=(1.0, 0.0, 0.0),
            max_power_w=1.0,
            efficiency=1.0,
        )
    except Exception:
        simple_solar_panel_obj = None

    return EpsBasiliskModuleGraph(
        battery=battery,
        solar_source=solar_source,
        load_sinks=load_sinks,
        simple_solar_panel=simple_solar_panel_obj,
        cfg=cfg,
    )


def run_basilisk_eps_smoke(cfg: EpsBasiliskConfig | None = None) -> EpsBasiliskSmokeResult:
    """Run a minimal Basilisk EPS simplePower smoke simulation.

    This run does not use spacecraft dynamics, SPICE, eclipse, or WMM.  It only
    tests that simplePower nodes can be wired into simpleBattery and integrated
    for a short deterministic profile.
    """

    if not basilisk_available():
        return EpsBasiliskSmokeResult(False, False, 0.0, 0.0, 0.0, "Basilisk unavailable; smoke skipped")

    cfg = cfg or EpsBasiliskConfig()
    graph = build_basilisk_eps_graph(cfg)
    expected_net_power = max(0.0, float(cfg.solar_power_w)) - sum(max(0.0, float(v)) for v in cfg.loads_w.values())
    initial_storage = max(0.0, float(cfg.battery_capacity_wh)) * 3600.0 * max(0.0, min(1.0, float(cfg.initial_soc)))

    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # type: ignore
    except Exception as exc:  # pragma: no cover - only exercised in partial Basilisk installs
        return EpsBasiliskSmokeResult(True, False, initial_storage, initial_storage, expected_net_power, f"SimulationBaseClass import failed: {exc}")

    sim = SimulationBaseClass.SimBaseClass()
    process_name = "epsProcess"
    task_name = "epsTask"
    process = sim.CreateNewProcess(process_name)
    process.addTask(sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.dt_s))))
    sim.AddModelToTask(task_name, graph.solar_source)
    for sink in graph.load_sinks.values():
        sim.AddModelToTask(task_name, sink)
    sim.AddModelToTask(task_name, graph.battery)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.dt_s) * int(cfg.steps)))
    sim.ExecuteSimulation()

    try:
        final_storage = float(graph.battery.batPowerOutMsg.read().storageLevel)
    except Exception:
        final_storage = float("nan")
    return EpsBasiliskSmokeResult(True, True, initial_storage, final_storage, expected_net_power, "Basilisk EPS smoke ran")

