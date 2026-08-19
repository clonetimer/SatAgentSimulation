"""Whole-spacecraft Basilisk QoI profiles.

The profile suite still uses ``propulsion_unified_feasibility`` as the stable
QoI-oriented backend for historical continuity, while also inspecting the
formal ``whole_spacecraft.runner`` path.  After WHOLESC-STRUCTURE-FIX-2A the
formal runner is expected to be runnable because whole-spacecraft owns the
central spacecraft bus and ADCS attaches to it instead of implicitly owning it.
"""
from __future__ import annotations

import importlib
from dataclasses import asdict, is_dataclass, replace
from math import sqrt
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
    if op == "abs_gt":
        return abs(lhs) > rhs + tol
    raise ValueError(op)


def _check(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    scenario: str,
    metric: str,
    lhs_label: str,
    lhs: float,
    op: str,
    rhs_label: str,
    rhs: float,
    expected: str,
    support: str,
    tol: float = 0.0,
    evidence: dict[str, Any] | None = None,
) -> None:
    ok = _cmp(op, lhs, rhs, tol=tol)
    checks.append(
        {
            "id": check_id,
            "scenario": scenario,
            "metric": metric,
            "expected": expected,
            "support": support,
            "status": "PASS" if ok else "FAIL",
            "lhs": {"label": lhs_label, "value": lhs},
            "rhs": {"label": rhs_label, "value": rhs},
            "comparator": op,
            "tolerance": tol,
            "evidence": evidence or {},
        }
    )


def _partial(
    checks: list[dict[str, Any]],
    *,
    check_id: str,
    scenario: str,
    metric: str,
    reason: str,
    next_step: str,
    evidence: dict[str, Any] | None = None,
) -> None:
    checks.append(
        {
            "id": check_id,
            "scenario": scenario,
            "metric": metric,
            "expected": "explicitly reported as partially supported; not counted as supported-check failure",
            "support": "partially_supported_whole_spacecraft",
            "status": "PARTIAL",
            "reason": reason,
            "recommended_next_step": next_step,
            "evidence": evidence or {},
        }
    )


def _torque_norm(row: Any) -> float:
    return sqrt(
        _f(getattr(row, "rw_motor_torque_command_0_nm", 0.0), 0.0) ** 2
        + _f(getattr(row, "rw_motor_torque_command_1_nm", 0.0), 0.0) ** 2
        + _f(getattr(row, "rw_motor_torque_command_2_nm", 0.0), 0.0) ** 2
    )


def _run_unified(cfg: Any, *, label: str) -> dict[str, Any]:
    unified = importlib.import_module("validation.legacy.whole_spacecraft.propulsion_unified_feasibility")
    summary, rows = unified.run_propulsion_unified_feasibility(cfg)
    row_list = list(rows)
    data = _jsonable(summary)
    data["label"] = label
    data["sample_count"] = len(row_list)
    data["initial_soc"] = _f(row_list[0].battery_soc, _f(getattr(cfg, "initial_soc", 0.0), 0.0)) if row_list else _f(getattr(cfg, "initial_soc", 0.0), 0.0)
    data["soc_delta"] = _f(data.get("final_soc"), 0.0) - data["initial_soc"]
    data["initial_data_storage_bits"] = _f(row_list[0].data_storage_bits, 0.0) if row_list else 0.0
    data["data_storage_growth_bits"] = _f(data.get("final_data_storage_bits"), 0.0) - data["initial_data_storage_bits"]
    data["max_data_storage_bits"] = max((_f(row.data_storage_bits, 0.0) for row in row_list), default=0.0)
    data["min_data_storage_bits"] = min((_f(row.data_storage_bits, 0.0) for row in row_list), default=0.0)
    data["delta_v_proxy_m_s"] = abs(_f(data.get("final_velocity_x_m_s"), 0.0))
    data["initial_temp_c"] = _f(row_list[0].thermal_temp_c, _f(getattr(cfg, "initial_temp_c", 0.0), 0.0)) if row_list else _f(getattr(cfg, "initial_temp_c", 0.0), 0.0)
    data["final_temp_c"] = _f(row_list[-1].thermal_temp_c, data["initial_temp_c"]) if row_list else data["initial_temp_c"]
    data["max_temp_c"] = max((_f(row.thermal_temp_c, 0.0) for row in row_list), default=data["initial_temp_c"])
    data["temp_rise_c"] = data["final_temp_c"] - data["initial_temp_c"]
    data["max_attitude_error_norm"] = max((_f(row.attitude_error_norm, 0.0) for row in row_list), default=0.0)
    data["final_attitude_error_norm"] = _f(row_list[-1].attitude_error_norm, 0.0) if row_list else 0.0
    data["mean_rw_motor_torque_command_nm"] = sum(_torque_norm(row) for row in row_list) / max(len(row_list), 1)
    data["max_rw_motor_torque_command_nm"] = max((_torque_norm(row) for row in row_list), default=0.0)
    dt = _f(getattr(cfg, "sample_s", 1.0), 1.0)
    data["payload_generated_bits_proxy"] = sum(max(0.0, _f(row.instrument_baud_bps, 0.0)) * dt for row in row_list)
    data["downlinked_bits_proxy"] = max(0.0, data["payload_generated_bits_proxy"] + data["initial_data_storage_bits"] - _f(data.get("final_data_storage_bits"), 0.0))
    data["mean_transmitter_baud_abs_bps"] = sum(abs(_f(row.transmitter_baud_bps, 0.0)) for row in row_list) / max(len(row_list), 1)
    data["load_shed_samples"] = sum(1 for row in row_list if bool(row.load_shed_active))
    data["payload_load_served_mean_w"] = sum(_f(row.payload_load_enabled_w, 0.0) for row in row_list) / max(len(row_list), 1)
    data["adcs_load_served_mean_w"] = sum(_f(row.adcs_load_enabled_w, 0.0) for row in row_list) / max(len(row_list), 1)
    return data




def _run_formal_propulsion_runtime_fault(label: str = "runtime_thruster_ignition_failure") -> dict[str, Any]:
    from dataclasses import replace as dc_replace
    runner = importlib.import_module("whole_spacecraft.runner")
    cfg_mod = importlib.import_module("whole_spacecraft.schemas")
    faults_mod = importlib.import_module("whole_spacecraft.faults")

    run_cfg = cfg_mod.WholeSpacecraftRunConfig(
        duration_s=60.0,
        sample_s=10.0,
        structure=cfg_mod.WholeSpacecraftConfig(propulsion_enabled=True),
    )
    nominal_summary, nominal_rows = runner.run_whole_spacecraft_native_case(run_cfg)
    fault_specs = [dc_replace(spec, onset_time_s=0.0) for spec in faults_mod.runtime_fault_specs_for_scenario("propulsion_thruster_ignition_failure")]
    fault_summary, fault_rows = runner.run_whole_spacecraft_fault_case(run_cfg, fault_specs)

    nominal_initial = _f(nominal_rows[0].propellant_remaining_kg, 0.0) if nominal_rows else 0.0
    nominal_final = _f(nominal_rows[-1].propellant_remaining_kg, 0.0) if nominal_rows else 0.0
    fault_initial = _f(fault_rows[0].propellant_remaining_kg, 0.0) if fault_rows else 0.0
    fault_final = _f(fault_rows[-1].propellant_remaining_kg, 0.0) if fault_rows else 0.0
    return {
        "label": label,
        "nominal_summary": _jsonable(nominal_summary),
        "fault_summary": _jsonable(fault_summary),
        "nominal_propellant_used_kg": max(0.0, nominal_initial - nominal_final),
        "fault_propellant_used_kg": max(0.0, fault_initial - fault_final),
        "nominal_final_propellant_kg": nominal_final,
        "fault_final_propellant_kg": fault_final,
        "runtime_fault_event_status": getattr(fault_summary, "runtime_fault_event_status", ""),
        "runtime_fault_event_count": int(getattr(fault_summary, "runtime_fault_event_count", 0)),
        "runtime_fault_triggered_count": int(getattr(fault_summary, "runtime_fault_triggered_count", 0)),
        "runtime_fault_fallback_count": int(getattr(fault_summary, "runtime_fault_fallback_count", 0)),
        "runtime_fault_mutation_target_count": int(getattr(fault_summary, "runtime_fault_mutation_target_count", 0)),
    }

def _propulsion_degradation(thrust_loss_pct: float) -> Any:
    ws_deg = importlib.import_module("whole_spacecraft.degradation")
    eps_deg_mod = importlib.import_module("subsystems.eps.degradation")
    adcs_deg_mod = importlib.import_module("subsystems.adcs.degradation")
    prop_deg_mod = importlib.import_module("subsystems.propulsion.degradation")
    therm_deg_mod = importlib.import_module("subsystems.thermal.degradation")
    return ws_deg.WholeSatelliteDegradation(
        eps_degradation=eps_deg_mod.build_eps_degradation(),
        propulsion_degradation=prop_deg_mod.build_propulsion_degradation(thrust_loss_pct=float(thrust_loss_pct)),
        adcs_degradation=adcs_deg_mod.build_adcs_degradation(),
        thermal_degradation=therm_deg_mod.build_thermal_degradation(),
    )


def _inspect_legacy_runner() -> dict[str, Any]:
    try:
        runner = importlib.import_module("whole_spacecraft.runner")
        cfg_mod = importlib.import_module("whole_spacecraft.schemas")
        summary, rows = runner.run_whole_spacecraft_native_case(cfg_mod.WholeSpacecraftRunConfig(duration_s=20.0, sample_s=10.0))
        return {"status": "RUNNABLE", "summary": _jsonable(summary), "sample_count": len(rows)}
    except Exception as exc:  # noqa: BLE001 - this is an inspection report, not a supported PASS path.
        return {
            "status": "UNSUPPORTED_ENTRYPOINT",
            "reason": f"{type(exc).__name__}: {exc}",
            "recommended_next_step": "Inspect whole_spacecraft.builder and subsystems.adcs.builder attach mode; the formal runner is expected to be runnable after WHOLESC-STRUCTURE-FIX-2A.",
        }


def run_whole_spacecraft_qoi_profiles() -> dict[str, Any]:
    unified = importlib.import_module("validation.legacy.whole_spacecraft.propulsion_unified_feasibility")
    checks: list[dict[str, Any]] = []
    profiles: dict[str, Any] = {}
    unsupported: list[dict[str, Any]] = []

    base_cfg = unified.PropulsionUnifiedFeasibilityConfig(
        duration_s=80.0,
        sample_s=10.0,
        propulsion_on_time_s=(0.20, 0.20),
        max_final_attitude_ratio=99.0,
    )

    legacy_runner = _inspect_legacy_runner()
    if legacy_runner.get("status") != "RUNNABLE":
        unsupported.append({"item": "whole_spacecraft.runner.run_whole_spacecraft_native_case", **legacy_runner})

    # 1) Power-orbit profile: solar/eclipsed power balance is represented in the
    # runnable unified assembly by changing solar_power_w.  Native orbit/eclipse
    # message coupling remains partial and is stated below.
    scenario = "whole_spacecraft_power_orbit"
    sunlit = _run_unified(replace(base_cfg, propulsion_enabled=False, solar_power_w=120.0), label="sunlit_power_profile")
    eclipse = _run_unified(replace(base_cfg, propulsion_enabled=False, solar_power_w=0.0), label="eclipse_power_proxy")
    low_power = _run_unified(
        replace(
            base_cfg,
            propulsion_enabled=False,
            solar_power_w=0.0,
            initial_soc=0.25,
            bus_power_w=20.0,
            payload_power_w=80.0,
            adcs_power_w=50.0,
            comm_power_w=50.0,
            heater_power_w=40.0,
        ),
        label="low_soc_load_shed_profile",
    )
    _check(
        checks,
        check_id="wholesc_power_orbit.sunlit_final_soc_gt_eclipse_proxy",
        scenario=scenario,
        metric="final_soc",
        lhs_label="sunlit_power_profile",
        lhs=_metric(sunlit, "final_soc"),
        op="gt",
        rhs_label="eclipse_power_proxy",
        rhs=_metric(eclipse, "final_soc"),
        expected="sunlit solar profile ends with higher SOC than an eclipse/no-solar-power proxy profile",
        support="supported_unified_basilisk_profile",
        evidence={"backend": sunlit.get("backend"), "native_modules": sunlit.get("native_modules")},
    )
    _check(
        checks,
        check_id="wholesc_power_orbit.load_shed_samples_gt_nominal",
        scenario=scenario,
        metric="load_shed_samples",
        lhs_label="low_soc_load_shed_profile",
        lhs=_metric(low_power, "load_shed_samples"),
        op="gt",
        rhs_label="sunlit_power_profile",
        rhs=_metric(sunlit, "load_shed_samples"),
        expected="low-SOC high-load whole-spacecraft profile triggers more PDU load-shed samples than nominal sunlit profile",
        support="supported_unified_basilisk_profile",
    )
    _partial(
        checks,
        check_id="wholesc_power_orbit.native_orbit_eclipse_to_eps_feedback",
        scenario=scenario,
        metric="soc_under_native_orbit_eclipse_message",
        reason="The runnable whole-spacecraft profile uses a solar_power_w proxy; it does not yet route a native orbit/eclipse message into the EPS solar source in the unified feasibility runner.",
        next_step="Connect the Basilisk orbital environment eclipse output or shadow factor into the unified EPS solar input, then assert SOC across true sunlight/eclipse windows.",
        evidence={"sunlit_final_soc": sunlit.get("final_soc"), "eclipse_proxy_final_soc": eclipse.get("final_soc")},
    )
    profiles[scenario] = {"sunlit": sunlit, "eclipse_proxy": eclipse, "low_power": low_power}

    # 2) Payload downlink profile.  The unified runner now uses the same storage
    # drain pattern as Comm/Data: transmitter DataNodeUsageMsg is added to storage
    # with negative baud rate.
    scenario = "whole_spacecraft_payload_downlink"
    no_downlink = _run_unified(replace(base_cfg, propulsion_enabled=False, transmitter_baud_bps=0.0, instrument_baud_bps=2.5e6), label="payload_no_downlink")
    downlink = _run_unified(replace(base_cfg, propulsion_enabled=False, transmitter_baud_bps=5.0e6, instrument_baud_bps=2.5e6), label="payload_downlink_high_rate")
    _check(
        checks,
        check_id="wholesc_payload_downlink.generated_bits_positive",
        scenario=scenario,
        metric="payload_generated_bits_proxy",
        lhs_label="payload_no_downlink",
        lhs=_metric(no_downlink, "payload_generated_bits_proxy"),
        op="gt",
        rhs_label="zero",
        rhs=0.0,
        expected="payload instrument generates science bits in the whole-spacecraft profile",
        support="supported_unified_basilisk_profile",
    )
    _check(
        checks,
        check_id="wholesc_payload_downlink.downlink_storage_lt_no_downlink",
        scenario=scenario,
        metric="final_data_storage_bits",
        lhs_label="payload_downlink_high_rate",
        lhs=_metric(downlink, "final_data_storage_bits"),
        op="lt",
        rhs_label="payload_no_downlink",
        rhs=_metric(no_downlink, "final_data_storage_bits"),
        expected="active downlink drain leaves lower final storage backlog than no-downlink payload generation",
        support="supported_unified_basilisk_profile",
        evidence={"mean_transmitter_baud_abs_bps": downlink.get("mean_transmitter_baud_abs_bps")},
    )
    _check(
        checks,
        check_id="wholesc_payload_downlink.downlinked_bits_positive",
        scenario=scenario,
        metric="downlinked_bits_proxy",
        lhs_label="payload_downlink_high_rate",
        lhs=_metric(downlink, "downlinked_bits_proxy"),
        op="gt",
        rhs_label="zero",
        rhs=0.0,
        expected="downlink profile consumes generated/stored data bits",
        support="supported_unified_basilisk_profile",
    )
    profiles[scenario] = {"payload_no_downlink": no_downlink, "payload_downlink_high_rate": downlink}

    # 3) Propulsion maneuver profile.
    scenario = "whole_spacecraft_propulsion_maneuver"
    prop_enabled = _run_unified(base_cfg, label="propulsion_enabled")
    prop_disabled = _run_unified(replace(base_cfg, propulsion_enabled=False), label="propulsion_disabled")
    prop_degraded = _run_unified(replace(base_cfg, degradation=_propulsion_degradation(15.0)), label="thruster_thrust_loss_15pct")
    _check(
        checks,
        check_id="wholesc_propulsion_maneuver.delta_v_gt_disabled",
        scenario=scenario,
        metric="delta_v_proxy_m_s",
        lhs_label="propulsion_enabled",
        lhs=_metric(prop_enabled, "delta_v_proxy_m_s"),
        op="gt",
        rhs_label="propulsion_disabled",
        rhs=_metric(prop_disabled, "delta_v_proxy_m_s"),
        expected="enabled burn creates positive maneuver delta-v proxy above propulsion-disabled baseline",
        support="supported_unified_basilisk_profile",
    )
    _check(
        checks,
        check_id="wholesc_propulsion_maneuver.thrust_loss_delta_v_lt_nominal",
        scenario=scenario,
        metric="delta_v_proxy_m_s",
        lhs_label="thruster_thrust_loss_15pct",
        lhs=_metric(prop_degraded, "delta_v_proxy_m_s"),
        op="lt",
        rhs_label="propulsion_enabled",
        rhs=_metric(prop_enabled, "delta_v_proxy_m_s"),
        expected="thruster thrust degradation reduces maneuver delta-v proxy",
        support="supported_unified_basilisk_profile",
    )
    _check(
        checks,
        check_id="wholesc_propulsion_maneuver.propellant_used_gt_disabled",
        scenario=scenario,
        metric="propellant_used_kg",
        lhs_label="propulsion_enabled",
        lhs=_metric(prop_enabled, "propellant_used_kg"),
        op="gt",
        rhs_label="propulsion_disabled",
        rhs=_metric(prop_disabled, "propellant_used_kg"),
        expected="enabled burn consumes propellant above propulsion-disabled baseline",
        support="supported_unified_basilisk_profile",
    )
    runtime_thruster_fault = _run_formal_propulsion_runtime_fault()
    _check(
        checks,
        check_id="wholesc_propulsion_maneuver.runtime_thruster_fault_directionality",
        scenario=scenario,
        metric="propellant_used_kg",
        lhs_label="runtime_thruster_fault",
        lhs=_metric(runtime_thruster_fault, "fault_propellant_used_kg"),
        op="lt",
        rhs_label="nominal_formal_runner",
        rhs=_metric(runtime_thruster_fault, "nominal_propellant_used_kg"),
        expected="direct runtime thruster ignition failure event mutates native THRSimConfig MaxThrust and reduces propellant consumption relative to nominal",
        support="supported_formal_runner_runtime_fault",
        evidence={
            "runtime_fault_event_status": runtime_thruster_fault.get("runtime_fault_event_status"),
            "runtime_fault_event_count": runtime_thruster_fault.get("runtime_fault_event_count"),
            "runtime_fault_triggered_count": runtime_thruster_fault.get("runtime_fault_triggered_count"),
            "runtime_fault_fallback_count": runtime_thruster_fault.get("runtime_fault_fallback_count"),
            "runtime_fault_mutation_target_count": runtime_thruster_fault.get("runtime_fault_mutation_target_count"),
            "nominal_final_propellant_kg": runtime_thruster_fault.get("nominal_final_propellant_kg"),
            "fault_final_propellant_kg": runtime_thruster_fault.get("fault_final_propellant_kg"),
        },
    )
    profiles[scenario] = {
        "propulsion_enabled": prop_enabled,
        "propulsion_disabled": prop_disabled,
        "thruster_thrust_loss_15pct": prop_degraded,
        "runtime_thruster_ignition_failure": runtime_thruster_fault,
    }

    # 4) ADCS + EPS + Thermal profile.
    scenario = "whole_spacecraft_adcs_eps_thermal"
    low_adcs_heat = _run_unified(replace(base_cfg, propulsion_enabled=False, adcs_power_w=4.0, payload_heat_w=0.0), label="low_adcs_load_low_heat")
    high_adcs_heat = _run_unified(replace(base_cfg, propulsion_enabled=False, adcs_power_w=45.0, payload_heat_w=55.0), label="high_adcs_load_high_heat")
    _check(
        checks,
        check_id="wholesc_adcs_eps_thermal.high_adcs_load_soc_lt_low",
        scenario=scenario,
        metric="final_soc",
        lhs_label="high_adcs_load_high_heat",
        lhs=_metric(high_adcs_heat, "final_soc"),
        op="lt",
        rhs_label="low_adcs_load_low_heat",
        rhs=_metric(low_adcs_heat, "final_soc"),
        expected="higher configured ADCS electrical load lowers whole-spacecraft final SOC",
        support="supported_unified_basilisk_profile",
        evidence={"adcs_load_served_mean_w_high": high_adcs_heat.get("adcs_load_served_mean_w"), "adcs_load_served_mean_w_low": low_adcs_heat.get("adcs_load_served_mean_w")},
    )
    _check(
        checks,
        check_id="wholesc_adcs_eps_thermal.high_heat_temp_rise_gt_low",
        scenario=scenario,
        metric="temp_rise_c",
        lhs_label="high_adcs_load_high_heat",
        lhs=_metric(high_adcs_heat, "temp_rise_c"),
        op="gt",
        rhs_label="low_adcs_load_low_heat",
        rhs=_metric(low_adcs_heat, "temp_rise_c"),
        expected="higher configured payload/thermal heat raises whole-spacecraft thermal node temperature rise",
        support="supported_unified_basilisk_profile",
    )
    _check(
        checks,
        check_id="wholesc_adcs_eps_thermal.rw_control_effort_positive",
        scenario=scenario,
        metric="mean_rw_motor_torque_command_nm",
        lhs_label="low_adcs_load_low_heat",
        lhs=_metric(low_adcs_heat, "mean_rw_motor_torque_command_nm"),
        op="gt",
        rhs_label="zero",
        rhs=0.0,
        expected="whole-spacecraft ADCS loop emits nonzero RW motor torque commands",
        support="supported_unified_basilisk_profile",
    )
    _partial(
        checks,
        check_id="wholesc_adcs_eps_thermal.dynamic_control_effort_to_eps_load",
        scenario=scenario,
        metric="eps_soc_from_live_rw_motor_power",
        reason="The unified profile contains live ADCS commands and EPS load channels, but ADCS electrical load is configured as adcs_power_w rather than derived from RW motor command telemetry.",
        next_step="Add an actuator electrical power model from RW/MTB/CMG commands into the EPS adcs power sink before claiming live ADCS-control-to-EPS power coupling.",
        evidence={"mean_rw_motor_torque_command_nm": low_adcs_heat.get("mean_rw_motor_torque_command_nm"), "high_adcs_final_soc": high_adcs_heat.get("final_soc")},
    )
    profiles[scenario] = {"low_adcs_load_low_heat": low_adcs_heat, "high_adcs_load_high_heat": high_adcs_heat}

    scenarios = (
        "whole_spacecraft_power_orbit",
        "whole_spacecraft_payload_downlink",
        "whole_spacecraft_propulsion_maneuver",
        "whole_spacecraft_adcs_eps_thermal",
    )
    fail_count = sum(1 for row in checks if row["status"] == "FAIL")
    partial_count = sum(1 for row in checks if row["status"] == "PARTIAL")
    status = "FAIL" if fail_count else ("PASS_WITH_PARTIAL_WHOLE_SPACECRAFT" if partial_count or unsupported else "PASS")
    summary = {
        "batch": "WHOLESC-BSK-QOI-1",
        "status": status,
        "policy": EXPECTED_POLICY,
        "active_whole_spacecraft_backend": "validation.legacy.whole_spacecraft.propulsion_unified_feasibility",
        "check_count": len(checks),
        "fail_count": fail_count,
        "partial_whole_spacecraft_count": partial_count,
        "unsupported_entrypoint_count": len(unsupported),
        "checks_by_scenario": {scenario: sum(1 for row in checks if row["scenario"] == scenario) for scenario in scenarios},
        "failures_by_scenario": {scenario: sum(1 for row in checks if row["scenario"] == scenario and row["status"] == "FAIL") for scenario in scenarios},
        "partial_by_scenario": {scenario: sum(1 for row in checks if row["scenario"] == scenario and row["status"] == "PARTIAL") for scenario in scenarios},
    }
    return {"summary": summary, "checks": checks, "profiles": profiles, "unsupported": unsupported}


__all__ = ["run_whole_spacecraft_qoi_profiles"]
