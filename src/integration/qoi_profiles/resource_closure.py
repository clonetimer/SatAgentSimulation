"""v0.5.6.3 resource-closure QoI profiles.

The checks in this file supersede seven historical PARTIAL findings by running
actual message-connected whole-spacecraft graphs.  The old pairwise/dynamic
reports are retained for historical traceability; the coupling gate resolves a
check ID to PASS only when this newer direct evidence succeeds.
"""
from __future__ import annotations

from dataclasses import asdict
from typing import Any

from Basilisk.utilities import macros

from components.fault_spec import FaultSpec
from components.thruster.faults import ThrusterFaultType
from whole_spacecraft.builder import build_whole_spacecraft_graph
from whole_spacecraft.runner import run_whole_spacecraft_native_case
from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig


def _check(checks: list[dict[str, Any]], *, check_id: str, pair: str, metric: str, passed: bool, evidence: dict[str, Any]) -> None:
    checks.append({
        "id": check_id,
        "pair": pair,
        "metric": metric,
        "expected": "direct whole-spacecraft message/resource feedback produces the requested physical response",
        "support": "supported_whole_spacecraft_message_closed_loop",
        "status": "PASS" if passed else "FAIL",
        "evidence": evidence,
    })


def _run(cfg: WholeSpacecraftConfig, duration_s: float = 20.0, sample_s: float = 1.0):
    return run_whole_spacecraft_native_case(
        WholeSpacecraftRunConfig(duration_s=duration_s, sample_s=sample_s, structure=cfg)
    )


def _execute_graph(cfg: WholeSpacecraftConfig, duration_s: float) -> Any:
    graph = build_whole_spacecraft_graph(cfg)
    graph.sim.InitializeSimulation()
    graph.sim.ConfigureStopTime(macros.sec2nano(float(duration_s)))
    graph.sim.ExecuteSimulation()
    return graph


def _max_power(bridge: Any) -> float:
    return max((float(row.electrical_power_w) for row in getattr(bridge, "trace", ())), default=0.0)


def _thermal_payload_temp(graph: Any) -> float:
    network = getattr(graph.thermal_graph, "network", None)
    nodes = getattr(network, "nodes", {}) if network is not None else {}
    node = nodes.get("payload")
    return float(getattr(node, "temp_k", 0.0))


def run_resource_closure_profiles() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    profiles: dict[str, Any] = {}

    nominal_summary, nominal_rows = _run(WholeSpacecraftConfig(initial_soc=0.62), duration_s=20.0, sample_s=1.0)
    low_summary, low_rows = _run(WholeSpacecraftConfig(initial_soc=0.40), duration_s=20.0, sample_s=1.0)
    _check(
        checks,
        check_id="eps_payload.pdu_status_to_payload_generation_feedback",
        pair="EPS+Payload",
        metric="mission_generated_bits",
        passed=low_summary.mission_generated_bits == 0.0 and nominal_summary.mission_generated_bits > 0.0,
        evidence={
            "nominal_generated_bits": nominal_summary.mission_generated_bits,
            "low_soc_generated_bits": low_summary.mission_generated_bits,
            "low_soc_payload_enabled_count": low_summary.mission_payload_enabled_count,
        },
    )
    _check(
        checks,
        check_id="eps_comm.pdu_status_to_downlink_rate_feedback",
        pair="EPS+Comm/Data",
        metric="mission_delivered_bits",
        passed=low_summary.mission_delivered_bits == 0.0 and nominal_summary.mission_delivered_bits > 0.0,
        evidence={
            "nominal_delivered_bits": nominal_summary.mission_delivered_bits,
            "low_soc_delivered_bits": low_summary.mission_delivered_bits,
            "low_soc_access_count": low_summary.mission_access_count,
        },
    )
    profiles["pdu_payload_comm"] = {
        "nominal": asdict(nominal_summary),
        "low_soc": asdict(low_summary),
    }

    heater_graph = build_whole_spacecraft_graph(WholeSpacecraftConfig(initial_soc=0.20))
    for heater in heater_graph.thermal_graph.network.heaters.values():
        heater.forced_state = True
    heater_graph.sim.InitializeSimulation()
    heater_graph.sim.ConfigureStopTime(macros.sec2nano(2.0))
    heater_graph.sim.ExecuteSimulation()
    pdu_trace = list(getattr(heater_graph.eps_graph.pdu, "trace", ()))
    heater_states = {name: bool(getattr(heater, "_heater_on", False)) for name, heater in heater_graph.thermal_graph.network.heaters.items()}
    heater_permit = any(bool(row.heater_enabled) for row in pdu_trace)
    _check(
        checks,
        check_id="thermal_eps.eps_heater_status_to_thermal_heat_feedback",
        pair="Thermal+EPS",
        metric="heater_enable_after_pdu_shed",
        passed=not heater_permit and not any(heater_states.values()),
        evidence={"pdu_heater_enabled_any": heater_permit, "heater_states": heater_states},
    )
    profiles["heater_pdu"] = {"pdu_trace_count": len(pdu_trace), "heater_states": heater_states}

    base_cfg = WholeSpacecraftConfig(
        mission_initial_sigma_bn=(0.0, 0.0, 0.0),
        mission_initial_omega_bn_b_rad_s=(0.0, 0.0, 0.0),
    )
    nominal_prop, nominal_prop_rows = _run(base_cfg, duration_s=5.0, sample_s=0.2)
    thruster_fault = FaultSpec(
        fault_type=ThrusterFaultType.IgnitionFailure,
        onset_time_s=0.0,
        duration_s=-1.0,
        magnitude=1.0,
        target_id="thruster_0",
    )
    fault_prop, fault_prop_rows = _run(
        WholeSpacecraftConfig(
            mission_initial_sigma_bn=(0.0, 0.0, 0.0),
            mission_initial_omega_bn_b_rad_s=(0.0, 0.0, 0.0),
            fault_specs=[thruster_fault],
        ),
        duration_s=5.0,
        sample_s=0.2,
    )
    nominal_max_att = max((row.attitude_error_norm for row in nominal_prop_rows), default=0.0)
    fault_max_att = max((row.attitude_error_norm for row in fault_prop_rows), default=0.0)
    _check(
        checks,
        check_id="prop_adcs.thruster_fault_to_attitude_response",
        pair="Propulsion+ADCS",
        metric="max_attitude_error_norm",
        passed=fault_prop.runtime_fault_triggered_count >= 1 and fault_max_att > nominal_max_att + 1e-6,
        evidence={
            "nominal_max_attitude_error_norm": nominal_max_att,
            "fault_max_attitude_error_norm": fault_max_att,
            "runtime_fault_triggered_count": fault_prop.runtime_fault_triggered_count,
            "fault_target": "thruster_0",
        },
    )
    profiles["propulsion_adcs"] = {"nominal": asdict(nominal_prop), "fault": asdict(fault_prop)}

    pointing_nominal, _ = _run(WholeSpacecraftConfig(mission_initial_sigma_bn=(0.0, 0.0, 0.0), propulsion_enabled=False), duration_s=5.0, sample_s=0.5)
    pointing_bad, _ = _run(WholeSpacecraftConfig(mission_initial_sigma_bn=(0.2, 0.0, 0.0), propulsion_enabled=False), duration_s=5.0, sample_s=0.5)
    _check(
        checks,
        check_id="adcs_payload.adcs_pointing_to_payload_command_feedback",
        pair="ADCS+Payload",
        metric="mission_generated_bits",
        passed=pointing_bad.mission_generated_bits < pointing_nominal.mission_generated_bits,
        evidence={
            "nominal_generated_bits": pointing_nominal.mission_generated_bits,
            "bad_pointing_generated_bits": pointing_bad.mission_generated_bits,
            "bad_pointing_final_error_deg": pointing_bad.final_attitude_error_deg,
        },
    )

    adcs_idle_graph = _execute_graph(WholeSpacecraftConfig(mission_initial_sigma_bn=(0.0, 0.0, 0.0), propulsion_enabled=False), 8.0)
    adcs_slew_graph = _execute_graph(WholeSpacecraftConfig(mission_initial_sigma_bn=(0.1, 0.0, 0.0), propulsion_enabled=False), 8.0)
    idle_adcs_power = _max_power(adcs_idle_graph.component_registry["adcs_power_bridge"])
    slew_adcs_power = _max_power(adcs_slew_graph.component_registry["adcs_power_bridge"])
    _check(
        checks,
        check_id="adcs_eps.control_effort_to_power_load_feedback",
        pair="ADCS+EPS",
        metric="max_adcs_electrical_power_w",
        passed=slew_adcs_power > idle_adcs_power + 1e-6,
        evidence={"idle_max_power_w": idle_adcs_power, "slew_max_power_w": slew_adcs_power},
    )

    payload_on_graph = _execute_graph(WholeSpacecraftConfig(initial_soc=0.62, propulsion_enabled=False), 120.0)
    payload_off_graph = _execute_graph(WholeSpacecraftConfig(initial_soc=0.40, propulsion_enabled=False), 120.0)
    payload_on_power = _max_power(payload_on_graph.component_registry["payload_power_bridge"])
    payload_off_power = _max_power(payload_off_graph.component_registry["payload_power_bridge"])
    payload_on_temp = _thermal_payload_temp(payload_on_graph)
    payload_off_temp = _thermal_payload_temp(payload_off_graph)
    _check(
        checks,
        check_id="thermal_payload.generated_payload_power_to_thermal_heat_feedback",
        pair="Thermal+Payload",
        metric="payload_temperature_and_power_from_runtime_generation",
        passed=(payload_on_power > payload_off_power + 1e-6 and payload_on_temp > payload_off_temp + 1e-6),
        evidence={
            "payload_on_max_power_w": payload_on_power,
            "payload_off_max_power_w": payload_off_power,
            "payload_on_final_temp_k": payload_on_temp,
            "payload_off_final_temp_k": payload_off_temp,
        },
    )

    profiles["adcs_payload_eps_thermal"] = {
        "pointing_nominal": asdict(pointing_nominal),
        "pointing_bad": asdict(pointing_bad),
        "adcs_idle_max_power_w": idle_adcs_power,
        "adcs_slew_max_power_w": slew_adcs_power,
        "payload_on_power_w": payload_on_power,
        "payload_off_power_w": payload_off_power,
        "payload_on_temp_k": payload_on_temp,
        "payload_off_temp_k": payload_off_temp,
    }

    fail_count = sum(1 for row in checks if row["status"] == "FAIL")
    return {
        "schema_version": "sat-sim.resource-closure-qoi.v1",
        "batch": "INTEG-BSK-QOI-3",
        "status": "FAIL" if fail_count else "PASS",
        "check_count": len(checks),
        "pass_count": len(checks) - fail_count,
        "fail_count": fail_count,
        "checks": checks,
        "profiles": profiles,
    }


__all__ = ["run_resource_closure_profiles"]
