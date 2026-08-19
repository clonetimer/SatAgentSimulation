"""First-batch pairwise integration QoI profiles.

The profiles here are intentionally conservative.  A PASS is only assigned when
one runner actually exposes the relevant coupled variable path, such as EPS PDU
load channels or Comm/Data storage drain.  Missing cross-subsystem message
feedback is reported as PARTIAL instead of being inferred through unrelated
side metrics.
"""
from __future__ import annotations

import importlib
from dataclasses import asdict, is_dataclass, replace
from typing import Any

EXPECTED_POLICY = "direct_basilisk_targets_only"


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return _jsonable(asdict(value))
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v) for v in value]
    return value


def _f(value: Any, default: float | None = None) -> float:
    try:
        if value is None:
            raise TypeError("None")
        return float(value)
    except Exception:
        if default is None:
            raise
        return float(default)


def _metric(payload: dict[str, Any], key: str) -> float:
    if key not in payload:
        raise AssertionError(f"missing metric {key!r}")
    return _f(payload[key])


def _cmp(op: str, lhs: float, rhs: float, tol: float = 0.0) -> bool:
    if op == "gt":
        return lhs > rhs + tol
    if op == "lt":
        return lhs < rhs - tol
    if op == "ge":
        return lhs + tol >= rhs
    if op == "le":
        return lhs <= rhs + tol
    raise ValueError(op)


def _check(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    pair: str,
    metric: str,
    lhs_label: str,
    lhs: float,
    op: str,
    rhs_label: str,
    rhs: float,
    expected: str,
    support: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    ok = _cmp(op, lhs, rhs)
    checks.append(
        {
            "id": check_id,
            "pair": pair,
            "metric": metric,
            "expected": expected,
            "support": support,
            "status": "PASS" if ok else "FAIL",
            "lhs": {"label": lhs_label, "value": lhs},
            "rhs": {"label": rhs_label, "value": rhs},
            "comparator": op,
            "evidence": evidence or {},
        }
    )


def _partial(checks: list[dict[str, Any]], *, check_id: str, pair: str, metric: str, reason: str, next_step: str, evidence: dict[str, Any] | None = None) -> None:
    checks.append(
        {
            "id": check_id,
            "pair": pair,
            "metric": metric,
            "expected": "explicitly reported as partially supported; not counted as supported-check failure",
            "support": "partially_supported_integration",
            "status": "PARTIAL",
            "reason": reason,
            "recommended_next_step": next_step,
            "evidence": evidence or {},
        }
    )


def _run_eps_cfg(eps: Any, cfg: Any) -> dict[str, Any]:
    summary, rows = eps.run_eps_basilisk_scenario(cfg)
    data = _jsonable(summary)
    data["sample_count"] = len(rows)
    data["min_soc"] = min((_f(row.battery_soc) for row in rows), default=_f(cfg.initial_soc))
    data["max_soc"] = max((_f(row.battery_soc) for row in rows), default=_f(cfg.initial_soc))
    data["storage_delta_j"] = _f(data["final_storage_j"]) - _f(data["initial_storage_j"])
    data["battery_energy_delta_wh"] = data["storage_delta_j"] / 3600.0
    data["mean_net_power_w_proxy"] = data["storage_delta_j"] / max(_f(data["duration_s"]), 1e-9)
    data["load_shed_samples"] = sum(1 for row in rows if bool(row.load_shed_active))
    data["payload_enabled_samples"] = sum(1 for row in rows if bool(row.payload_enabled))
    data["comm_enabled_samples"] = sum(1 for row in rows if bool(row.comm_enabled))
    data["heater_enabled_samples"] = sum(1 for row in rows if bool(row.heater_enabled))
    return data


def _run_comm_cfg(comm: Any, cfg: Any) -> dict[str, Any]:
    summary, rows = comm.run_comm_data_basilisk_scenario(cfg)
    data = _jsonable(summary)
    data["sample_count"] = len(rows)
    data["initial_storage_bits_config"] = _f(cfg.initial_storage_bits)
    data["storage_backlog_growth_bits"] = _f(data["final_storage_bits"]) - _f(data["min_storage_bits"])
    data["downlink_rate_bps_proxy"] = _f(data["estimated_native_downlinked_bits"]) / max(_f(data["duration_s"]), 1e-9)
    data["drain_active_samples"] = sum(1 for row in rows if _f(row.transmitter_storage_node_baud_bps, 0.0) < 0.0)
    return data


def _run_payload_cfg(payload: Any, cfg: Any) -> dict[str, Any]:
    summary, rows = payload.run_payload_basilisk_scenario(cfg)
    data = _jsonable(summary)
    data["sample_count"] = len(rows)
    return data


def _run_thermal_cfg(thermal: Any, cfg: Any) -> dict[str, Any]:
    return thermal._run_basilisk_config(cfg)


def run_pairwise_profiles() -> dict[str, Any]:
    eps = importlib.import_module("subsystems.eps.runner")
    payload = importlib.import_module("subsystems.payload.runner")
    comm = importlib.import_module("subsystems.comm_data.runner")
    thermal = importlib.import_module("subsystems.thermal.runner")

    checks: list[dict[str, Any]] = []
    profiles: dict[str, Any] = {}

    # EPS + Payload: EPS has a real payload load channel and PDU state.  Payload
    # generation itself is still a separate runner, so PDU-to-payload feedback is
    # partial rather than fabricated.
    eps_payload_base_cfg = eps.EPSBasiliskConfig(
        duration_s=180.0,
        step_s=10.0,
        initial_soc=0.75,
        solar_power_w=80.0,
        bus_power_w=12.0,
        payload_power_w=0.0,
        adcs_power_w=5.0,
        comm_power_w=0.0,
        heater_power_w=0.0,
        payload_requested=False,
        payload_min_soc=0.20,
    )
    eps_payload_off = _run_eps_cfg(eps, eps_payload_base_cfg)
    eps_payload_on = _run_eps_cfg(eps, replace(eps_payload_base_cfg, payload_power_w=55.0, payload_requested=True))
    payload_data_generation = payload.run_normal_scenario("data_generation")
    eps_low_soc_payload = _run_eps_cfg(eps, replace(eps_payload_base_cfg, initial_soc=0.45, solar_power_w=20.0, payload_power_w=80.0, payload_requested=True, payload_min_soc=0.55))
    pair = "EPS+Payload"
    _check(
        checks,
        check_id="eps_payload.payload_load.final_soc_lt_no_payload",
        pair=pair,
        metric="final_soc",
        lhs_label="payload_load_on",
        lhs=_metric(eps_payload_on, "final_soc"),
        op="lt",
        rhs_label="payload_load_off",
        rhs=_metric(eps_payload_off, "final_soc"),
        expected="enabling the EPS payload load channel lowers final SOC against the same EPS profile without payload load",
        support="supported_pairwise_profile",
        evidence={"coupling_path": "EPS PDU payload simplePowerSink / payload_status"},
    )
    _check(
        checks,
        check_id="eps_payload.payload_load.net_power_lt_no_payload",
        pair=pair,
        metric="mean_net_power_w_proxy",
        lhs_label="payload_load_on",
        lhs=_metric(eps_payload_on, "mean_net_power_w_proxy"),
        op="lt",
        rhs_label="payload_load_off",
        rhs=_metric(eps_payload_off, "mean_net_power_w_proxy"),
        expected="payload electrical load reduces EPS net-power proxy",
        support="supported_pairwise_profile",
    )
    _check(
        checks,
        check_id="eps_payload.pdu_sheds_payload_when_low_soc",
        pair=pair,
        metric="payload_enabled_samples",
        lhs_label="low_soc_payload_enabled_samples",
        lhs=_metric(eps_low_soc_payload, "payload_enabled_samples"),
        op="lt",
        rhs_label="nominal_payload_enabled_samples",
        rhs=_metric(eps_payload_on, "payload_enabled_samples"),
        expected="low-SOC EPS/PDU profile disables payload load samples",
        support="supported_pairwise_profile",
        evidence={"low_soc_load_shed_samples": eps_low_soc_payload.get("load_shed_samples")},
    )
    _partial(
        checks,
        check_id="eps_payload.pdu_status_to_payload_generation_feedback",
        pair=pair,
        metric="payload_generated_bits_after_pdu_shed",
        reason="EPS runner exposes payload load shedding, and payload runner exposes generated bits, but the current pairwise layer does not feed EPS payload_status into the payload Basilisk instrument runner.",
        next_step="Add a message/API bridge from EPS payload_status to PayloadBasiliskConfig or payload instrument command gate; then compare generated_bits under PDU shed vs nominal.",
        evidence={"payload_data_generation_bits": payload_data_generation.get("generated_bits") or payload_data_generation.get("estimated_generated_bits")},
    )
    profiles["eps_payload"] = {"eps_payload_off": eps_payload_off, "eps_payload_on": eps_payload_on, "eps_low_soc_payload": eps_low_soc_payload, "payload_data_generation": payload_data_generation}

    # EPS + Comm/Data: EPS has a real comm load channel; downlink capacity is a
    # Comm/Data runner concern.  EPS-to-comm data-rate feedback remains partial.
    pair = "EPS+Comm/Data"
    eps_comm_base_cfg = eps.EPSBasiliskConfig(
        duration_s=180.0,
        step_s=10.0,
        initial_soc=0.75,
        solar_power_w=75.0,
        bus_power_w=12.0,
        payload_power_w=0.0,
        adcs_power_w=5.0,
        comm_power_w=0.0,
        heater_power_w=0.0,
        comm_requested=False,
        comm_min_soc=0.20,
    )
    eps_comm_off = _run_eps_cfg(eps, eps_comm_base_cfg)
    eps_comm_on = _run_eps_cfg(eps, replace(eps_comm_base_cfg, comm_power_w=45.0, comm_requested=True))
    eps_low_soc_comm = _run_eps_cfg(eps, replace(eps_comm_base_cfg, initial_soc=0.42, solar_power_w=15.0, comm_power_w=60.0, comm_requested=True, comm_min_soc=0.50))
    comm_downlink = comm.run_normal_scenario("ground_contact")
    _check(
        checks,
        check_id="eps_comm.comm_load.final_soc_lt_no_comm",
        pair=pair,
        metric="final_soc",
        lhs_label="comm_load_on",
        lhs=_metric(eps_comm_on, "final_soc"),
        op="lt",
        rhs_label="comm_load_off",
        rhs=_metric(eps_comm_off, "final_soc"),
        expected="enabling transmitter/downlink electrical load lowers EPS final SOC",
        support="supported_pairwise_profile",
        evidence={"coupling_path": "EPS PDU comm simplePowerSink / comm_status"},
    )
    _check(
        checks,
        check_id="eps_comm.comm_load.net_power_lt_no_comm",
        pair=pair,
        metric="mean_net_power_w_proxy",
        lhs_label="comm_load_on",
        lhs=_metric(eps_comm_on, "mean_net_power_w_proxy"),
        op="lt",
        rhs_label="comm_load_off",
        rhs=_metric(eps_comm_off, "mean_net_power_w_proxy"),
        expected="comm electrical load reduces EPS net-power proxy",
        support="supported_pairwise_profile",
    )
    _check(
        checks,
        check_id="eps_comm.pdu_sheds_comm_when_low_soc",
        pair=pair,
        metric="comm_enabled_samples",
        lhs_label="low_soc_comm_enabled_samples",
        lhs=_metric(eps_low_soc_comm, "comm_enabled_samples"),
        op="lt",
        rhs_label="nominal_comm_enabled_samples",
        rhs=_metric(eps_comm_on, "comm_enabled_samples"),
        expected="low-SOC EPS/PDU profile disables comm load samples",
        support="supported_pairwise_profile",
        evidence={"low_soc_load_shed_samples": eps_low_soc_comm.get("load_shed_samples")},
    )
    _partial(
        checks,
        check_id="eps_comm.pdu_status_to_downlink_rate_feedback",
        pair=pair,
        metric="downlink_capacity_after_eps_fault",
        reason="EPS can shed the comm load, and Comm/Data can drain storage during ground contact, but the current Comm/Data runner does not consume EPS comm_status as a transmitter enable gate.",
        next_step="Connect EPS comm_status/PDU channel state to Comm/Data transmitter command or nodeBaudRate before asserting downlink capacity drops under EPS fault.",
        evidence={"comm_ground_contact_downlinked_bits": comm_downlink.get("estimated_native_downlinked_bits")},
    )
    profiles["eps_comm_data"] = {"eps_comm_off": eps_comm_off, "eps_comm_on": eps_comm_on, "eps_low_soc_comm": eps_low_soc_comm, "comm_downlink": comm_downlink}

    # Payload + Comm/Data: Comm/Data has a simpleInstrument -> storage ->
    # transmitter path, which is the concrete payload-data-to-backlog profile.
    pair = "Payload+Comm/Data"
    payload_standby = payload.run_normal_scenario("standby")
    payload_high = payload.run_normal_scenario("data_generation")
    comm_no_contact = _run_comm_cfg(comm, comm.CommDataBasiliskConfig(duration_s=80.0, step_s=10.0, instrument_baud_bps=5.0e5, transmitter_baud_bps=0.0, storage_capacity_bits=5.0e9, native_storage_drain_enabled=False))
    comm_downlink_drain = _run_comm_cfg(comm, comm.CommDataBasiliskConfig(duration_s=80.0, step_s=10.0, instrument_baud_bps=0.0, transmitter_baud_bps=2.0e6, storage_capacity_bits=5.0e9, initial_storage_bits=60.0e6, native_storage_drain_enabled=True))
    _check(
        checks,
        check_id="payload_comm.payload_data_generation_gt_standby",
        pair=pair,
        metric="generated_bits",
        lhs_label="payload_data_generation",
        lhs=_metric(payload_high, "generated_bits"),
        op="gt",
        rhs_label="payload_standby",
        rhs=_metric(payload_standby, "generated_bits"),
        expected="payload data-generation scenario produces more bits than standby",
        support="supported_pairwise_profile",
    )
    _check(
        checks,
        check_id="payload_comm.no_contact_storage_backlog_positive",
        pair=pair,
        metric="final_storage_bits",
        lhs_label="comm_no_contact_payload_generation",
        lhs=_metric(comm_no_contact, "final_storage_bits"),
        op="gt",
        rhs_label="zero",
        rhs=0.0,
        expected="payload-like instrument generation with no contact creates positive onboard storage backlog",
        support="supported_pairwise_profile",
        evidence={"coupling_path": "Comm/Data simpleInstrument -> simpleStorageUnit"},
    )
    _check(
        checks,
        check_id="payload_comm.downlink_reduces_initial_backlog",
        pair=pair,
        metric="final_storage_bits",
        lhs_label="downlink_final_storage",
        lhs=_metric(comm_downlink_drain, "final_storage_bits"),
        op="lt",
        rhs_label="initial_storage_bits",
        rhs=_metric(comm_downlink_drain, "initial_storage_bits_config"),
        expected="active downlink pass lowers initial onboard storage backlog",
        support="supported_pairwise_profile",
        evidence={"coupling_path": "Comm/Data simpleStorageUnit -> simpleTransmitter"},
    )
    profiles["payload_comm_data"] = {"payload_standby": payload_standby, "payload_high": payload_high, "comm_no_contact": comm_no_contact, "comm_downlink_drain": comm_downlink_drain}

    # Thermal + EPS: EPS heater load has a real PDU channel, and Thermal has a
    # heater/thermal node.  Cross-feeding EPS heater state into Thermal is still partial.
    pair = "Thermal+EPS"
    eps_heater_base_cfg = eps.EPSBasiliskConfig(
        duration_s=180.0,
        step_s=10.0,
        initial_soc=0.75,
        solar_power_w=65.0,
        bus_power_w=12.0,
        payload_power_w=0.0,
        adcs_power_w=5.0,
        comm_power_w=0.0,
        heater_power_w=0.0,
        heater_requested=False,
        heater_min_soc=0.20,
    )
    eps_heater_off = _run_eps_cfg(eps, eps_heater_base_cfg)
    eps_heater_on = _run_eps_cfg(eps, replace(eps_heater_base_cfg, heater_power_w=35.0, heater_requested=True))
    thermal_fault = thermal.run_fault_case("heater_stuck_off")
    thermal_matched = _run_thermal_cfg(thermal, thermal._thermal_fault_static_config("heater_stuck_off"))
    _check(
        checks,
        check_id="thermal_eps.heater_load.final_soc_lt_no_heater",
        pair=pair,
        metric="final_soc",
        lhs_label="heater_load_on",
        lhs=_metric(eps_heater_on, "final_soc"),
        op="lt",
        rhs_label="heater_load_off",
        rhs=_metric(eps_heater_off, "final_soc"),
        expected="heater electrical load lowers EPS final SOC",
        support="supported_pairwise_profile",
        evidence={"coupling_path": "EPS PDU heater simplePowerSink / heater_status"},
    )
    _check(
        checks,
        check_id="thermal_eps.heater_load.net_power_lt_no_heater",
        pair=pair,
        metric="mean_net_power_w_proxy",
        lhs_label="heater_load_on",
        lhs=_metric(eps_heater_on, "mean_net_power_w_proxy"),
        op="lt",
        rhs_label="heater_load_off",
        rhs=_metric(eps_heater_off, "mean_net_power_w_proxy"),
        expected="heater electrical load reduces EPS net-power proxy",
        support="supported_pairwise_profile",
    )
    _check(
        checks,
        check_id="thermal_eps.heater_fault.temp_lt_matched_thermal",
        pair=pair,
        metric="final_temp_c",
        lhs_label="thermal_heater_stuck_off",
        lhs=_metric(thermal_fault, "final_temp_c"),
        op="lt",
        rhs_label="matched_thermal_no_fault",
        rhs=_metric(thermal_matched, "final_temp_c"),
        expected="thermal heater fault lowers node temperature against a duration/config matched thermal baseline",
        support="supported_pairwise_profile",
    )
    _partial(
        checks,
        check_id="thermal_eps.eps_heater_status_to_thermal_heat_feedback",
        pair=pair,
        metric="thermal_temperature_after_eps_heater_shed",
        reason="EPS exposes heater load shedding and Thermal exposes heater-driven temperature, but the Thermal runner does not consume EPS heater_status as its heater enable command.",
        next_step="Connect EPS heater_status to Thermal scheduled node heater enable/power before asserting closed-loop thermal temperature response to EPS load shedding.",
        evidence={"eps_heater_on_samples": eps_heater_on.get("heater_enabled_samples"), "thermal_heater_fault_final_temp_c": thermal_fault.get("final_temp_c")},
    )
    profiles["thermal_eps"] = {"eps_heater_off": eps_heater_off, "eps_heater_on": eps_heater_on, "thermal_fault": thermal_fault, "thermal_matched": thermal_matched}

    fail_count = sum(1 for row in checks if row["status"] == "FAIL")
    partial_count = sum(1 for row in checks if row["status"] == "PARTIAL")
    status = "FAIL" if fail_count else ("PASS_WITH_PARTIAL_INTEGRATION" if partial_count else "PASS")
    pairs = ("EPS+Payload", "EPS+Comm/Data", "Payload+Comm/Data", "Thermal+EPS")
    summary = {
        "batch": "INTEG-BSK-QOI-1",
        "status": status,
        "policy": EXPECTED_POLICY,
        "check_count": len(checks),
        "fail_count": fail_count,
        "partial_integration_count": partial_count,
        "checks_by_pair": {pair: sum(1 for row in checks if row["pair"] == pair) for pair in pairs},
        "failures_by_pair": {pair: sum(1 for row in checks if row["pair"] == pair and row["status"] == "FAIL") for pair in pairs},
        "partial_by_pair": {pair: sum(1 for row in checks if row["pair"] == pair and row["status"] == "PARTIAL") for pair in pairs},
    }
    return {"summary": summary, "checks": checks, "profiles": profiles}
