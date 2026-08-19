"""Second-batch dynamic integration QoI profiles.

This batch is deliberately stricter about support claims than the earlier
pairwise profiles.  A supported check must be tied to an actual Basilisk runner
or a single unified Basilisk assembly.  Where the current project only exposes
separate subsystem knobs, the result is PARTIAL rather than a fabricated closed
loop.
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
    pair: str,
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
            "pair": pair,
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
    pair: str,
    metric: str,
    reason: str,
    next_step: str,
    evidence: dict[str, Any] | None = None,
) -> None:
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


def _torque_norm(row: Any) -> float:
    return sqrt(
        _f(getattr(row, "rw_motor_torque_command_0_nm", 0.0), 0.0) ** 2
        + _f(getattr(row, "rw_motor_torque_command_1_nm", 0.0), 0.0) ** 2
        + _f(getattr(row, "rw_motor_torque_command_2_nm", 0.0), 0.0) ** 2
    )


def _run_unified(cfg: Any, *, label: str) -> dict[str, Any]:
    unified = importlib.import_module("validation.legacy.whole_spacecraft.propulsion_unified_feasibility")
    summary, rows = unified.run_propulsion_unified_feasibility(cfg)
    data = _jsonable(summary)
    row_list = list(rows)
    data["label"] = label
    data["sample_count"] = len(row_list)
    data["initial_soc"] = _f(row_list[0].battery_soc, _f(getattr(cfg, "initial_soc", 0.0), 0.0)) if row_list else _f(getattr(cfg, "initial_soc", 0.0), 0.0)
    data["soc_delta"] = _f(data.get("final_soc"), 0.0) - _f(data.get("initial_soc"), 0.0)
    data["initial_data_storage_bits"] = _f(row_list[0].data_storage_bits, 0.0) if row_list else 0.0
    data["data_storage_growth_bits"] = _f(data.get("final_data_storage_bits"), 0.0) - data["initial_data_storage_bits"]
    data["max_data_storage_bits"] = max((_f(row.data_storage_bits, 0.0) for row in row_list), default=0.0)
    data["min_data_storage_bits"] = min((_f(row.data_storage_bits, 0.0) for row in row_list), default=0.0)
    data["final_attitude_error_norm"] = _f(row_list[-1].attitude_error_norm, 0.0) if row_list else 0.0
    data["max_attitude_error_norm"] = max((_f(row.attitude_error_norm, 0.0) for row in row_list), default=0.0)
    data["mean_rw_motor_torque_command_nm"] = sum(_torque_norm(row) for row in row_list) / max(len(row_list), 1)
    data["max_rw_motor_torque_command_nm"] = max((_torque_norm(row) for row in row_list), default=0.0)
    data["delta_v_proxy_m_s"] = abs(_f(data.get("final_velocity_x_m_s"), 0.0))
    data["initial_temp_c"] = _f(row_list[0].thermal_temp_c, _f(getattr(cfg, "initial_temp_c", 0.0), 0.0)) if row_list else _f(getattr(cfg, "initial_temp_c", 0.0), 0.0)
    data["final_temp_c"] = _f(row_list[-1].thermal_temp_c, data["initial_temp_c"]) if row_list else data["initial_temp_c"]
    data["max_temp_c"] = max((_f(row.thermal_temp_c, 0.0) for row in row_list), default=data["initial_temp_c"])
    data["temp_rise_c"] = data["final_temp_c"] - data["initial_temp_c"]
    dt = _f(getattr(cfg, "sample_s", 1.0), 1.0)
    data["payload_generated_bits_proxy"] = sum(max(0.0, _f(row.instrument_baud_bps, 0.0)) * dt for row in row_list)
    data["downlinked_bits_proxy"] = max(0.0, data["payload_generated_bits_proxy"] + data["initial_data_storage_bits"] - _f(data.get("final_data_storage_bits"), 0.0))
    data["mean_transmitter_baud_abs_bps"] = sum(abs(_f(row.transmitter_baud_bps, 0.0)) for row in row_list) / max(len(row_list), 1)
    data["load_shed_samples"] = sum(1 for row in row_list if bool(row.load_shed_active))
    return data


def _make_zero_degradation() -> Any:
    ws_deg = importlib.import_module("whole_spacecraft.degradation")
    eps_deg_mod = importlib.import_module("subsystems.eps.degradation")
    adcs_deg_mod = importlib.import_module("subsystems.adcs.degradation")
    prop_deg_mod = importlib.import_module("subsystems.propulsion.degradation")
    therm_deg_mod = importlib.import_module("subsystems.thermal.degradation")
    battery_deg_mod = importlib.import_module("components.battery.degradation")
    solar_deg_mod = importlib.import_module("components.solar_panel.degradation")
    thruster_deg_mod = importlib.import_module("components.thruster.degradation")
    tank_deg_mod = importlib.import_module("components.fuel_tank.degradation")
    heater_deg_mod = importlib.import_module("components.heater.degradation")
    radiator_deg_mod = importlib.import_module("components.radiator.degradation")
    return ws_deg.WholeSatelliteDegradation(
        eps_degradation=eps_deg_mod.EPSDegradation(battery_deg_mod.BatteryDegradation(), solar_deg_mod.SolarPanelDegradation(), 0.0),
        propulsion_degradation=prop_deg_mod.PropulsionDegradation(thruster_deg_mod.ThrusterDegradation(), tank_deg_mod.FuelTankDegradation()),
        adcs_degradation=adcs_deg_mod.ADCSDegradation(),
        thermal_degradation=therm_deg_mod.ThermalDegradation(heater_deg_mod.HeaterDegradation(), radiator_deg_mod.RadiatorDegradation()),
    )


def _propulsion_degradation(thrust_loss_pct: float) -> Any:
    ws_deg = importlib.import_module("whole_spacecraft.degradation")
    prop_deg_mod = importlib.import_module("subsystems.propulsion.degradation")
    thruster_deg_mod = importlib.import_module("components.thruster.degradation")
    tank_deg_mod = importlib.import_module("components.fuel_tank.degradation")
    base = _make_zero_degradation()
    return ws_deg.WholeSatelliteDegradation(
        eps_degradation=base.eps_degradation,
        propulsion_degradation=prop_deg_mod.PropulsionDegradation(
            thruster_deg_mod.ThrusterDegradation(thrust_loss_pct=float(thrust_loss_pct)),
            tank_deg_mod.FuelTankDegradation(),
        ),
        adcs_degradation=base.adcs_degradation,
        thermal_degradation=base.thermal_degradation,
    )


def _run_eps_cfg(eps: Any, cfg: Any) -> dict[str, Any]:
    summary, rows = eps.run_eps_basilisk_scenario(cfg)
    data = _jsonable(summary)
    data["sample_count"] = len(rows)
    data["storage_delta_j"] = _f(data["final_storage_j"]) - _f(data["initial_storage_j"])
    data["mean_net_power_w_proxy"] = data["storage_delta_j"] / max(_f(data["duration_s"]), 1e-9)
    return data


def _run_payload_cfg(payload: Any, cfg: Any) -> dict[str, Any]:
    summary, rows = payload.run_payload_basilisk_scenario(cfg)
    data = _jsonable(summary)
    data["sample_count"] = len(rows)
    return data


def run_dynamic_profiles() -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    profiles: dict[str, Any] = {}

    unified = importlib.import_module("validation.legacy.whole_spacecraft.propulsion_unified_feasibility")
    adcs = importlib.import_module("subsystems.adcs.runner")
    eps = importlib.import_module("subsystems.eps.runner")
    payload = importlib.import_module("subsystems.payload.runner")

    # Propulsion + ADCS: use the unified Basilisk feasibility assembly because
    # it contains thruster, fuel tank, spacecraft, RW ADCS and sensors in the same
    # SimulationBaseClass.  The current thruster geometry produces a robust
    # maneuver proxy but only a negligible attitude disturbance, so attitude
    # response is partial rather than over-claimed.
    pair = "Propulsion+ADCS"
    base_cfg = unified.PropulsionUnifiedFeasibilityConfig(
        duration_s=80.0,
        sample_s=10.0,
        propulsion_on_time_s=(0.20, 0.20),
        max_final_attitude_ratio=99.0,
    )
    prop_enabled = _run_unified(base_cfg, label="propulsion_enabled")
    prop_disabled = _run_unified(replace(base_cfg, propulsion_enabled=False), label="propulsion_disabled")
    prop_degraded = _run_unified(replace(base_cfg, degradation=_propulsion_degradation(15.0)), label="thruster_thrust_loss_15pct")
    _check(
        checks,
        check_id="prop_adcs.enabled_delta_v_gt_disabled",
        pair=pair,
        metric="delta_v_proxy_m_s",
        lhs_label="propulsion_enabled",
        lhs=_metric(prop_enabled, "delta_v_proxy_m_s"),
        op="gt",
        rhs_label="propulsion_disabled",
        rhs=_metric(prop_disabled, "delta_v_proxy_m_s"),
        expected="thruster-enabled unified run produces maneuver delta-v proxy above a propulsion-disabled run",
        support="supported_unified_basilisk_profile",
        evidence={"unified_simbase": prop_enabled.get("unified_simbase"), "included_subsystems": prop_enabled.get("included_subsystems")},
    )
    _check(
        checks,
        check_id="prop_adcs.thrust_loss_delta_v_lt_nominal",
        pair=pair,
        metric="delta_v_proxy_m_s",
        lhs_label="thruster_thrust_loss_15pct",
        lhs=_metric(prop_degraded, "delta_v_proxy_m_s"),
        op="lt",
        rhs_label="propulsion_enabled",
        rhs=_metric(prop_enabled, "delta_v_proxy_m_s"),
        expected="thruster thrust degradation lowers maneuver delta-v proxy in the unified Basilisk assembly",
        support="supported_unified_basilisk_profile",
    )
    _check(
        checks,
        check_id="prop_adcs.thrust_loss_propellant_use_lt_nominal",
        pair=pair,
        metric="propellant_used_kg",
        lhs_label="thruster_thrust_loss_15pct",
        lhs=_metric(prop_degraded, "propellant_used_kg"),
        op="lt",
        rhs_label="propulsion_enabled",
        rhs=_metric(prop_enabled, "propellant_used_kg"),
        expected="lower thrust configuration consumes less propellant in the focused burn profile",
        support="supported_unified_basilisk_profile",
    )
    _partial(
        checks,
        check_id="prop_adcs.thruster_fault_to_attitude_response",
        pair=pair,
        metric="final_attitude_error_norm_under_thruster_fault",
        reason="The unified runner places propulsion and ADCS on the same spacecraft, but the current focused thruster geometry creates a maneuver proxy with negligible attitude disturbance; runtime thruster fault injection is directionally silent in this runner.",
        next_step="Add an off-axis torque-producing thruster profile or explicit maneuver attitude reference, then assert attitude-error / control-effort degradation under a direct runtime thruster fault.",
        evidence={
            "nominal_final_attitude_error_norm": prop_enabled.get("final_attitude_error_norm"),
            "degraded_final_attitude_error_norm": prop_degraded.get("final_attitude_error_norm"),
            "delta_v_nominal": prop_enabled.get("delta_v_proxy_m_s"),
            "delta_v_degraded": prop_degraded.get("delta_v_proxy_m_s"),
        },
    )
    profiles["propulsion_adcs"] = {"propulsion_enabled": prop_enabled, "propulsion_disabled": prop_disabled, "thruster_thrust_loss_15pct": prop_degraded}

    # ADCS + Payload: both subsystem Basilisk runners expose the relevant QoIs,
    # but no message/API bridge currently maps ADCS pointing state into the
    # payload instrument enable/baud command.
    pair = "ADCS+Payload"
    adcs_nadir = adcs.run_normal_scenario("nadir_pointing")
    adcs_slew = adcs.run_normal_scenario("slew")
    payload_obs = payload.run_normal_scenario("observation")
    payload_limited = payload.run_normal_scenario("pointing_limited")
    _check(
        checks,
        check_id="adcs_payload.slew_attitude_error_gt_nadir",
        pair=pair,
        metric="final_sigma_norm",
        lhs_label="adcs_slew",
        lhs=_metric(adcs_slew, "final_sigma_norm"),
        op="gt",
        rhs_label="adcs_nadir_pointing",
        rhs=_metric(adcs_nadir, "final_sigma_norm"),
        expected="higher-torque ADCS slew profile has larger final attitude-error proxy than low-torque nadir profile",
        support="supported_subsystem_basilisk_qoi",
    )
    _check(
        checks,
        check_id="adcs_payload.pointing_limited_bits_lt_observation",
        pair=pair,
        metric="generated_bits",
        lhs_label="payload_pointing_limited",
        lhs=_metric(payload_limited, "generated_bits"),
        op="lt",
        rhs_label="payload_observation",
        rhs=_metric(payload_obs, "generated_bits"),
        expected="payload pointing-limited profile lowers generated science bits against normal observation",
        support="supported_subsystem_basilisk_qoi",
    )
    _partial(
        checks,
        check_id="adcs_payload.adcs_pointing_to_payload_command_feedback",
        pair=pair,
        metric="payload_effective_observation_after_adcs_degradation",
        reason="ADCS exposes attitude-error QoI and Payload exposes generated-bits QoI, but the payload Basilisk runner does not consume an ADCS pointing-ready/status message.",
        next_step="Bridge ADCS attitude-error/pointing-ready output into PayloadBasiliskConfig or a scheduled payload command gate, then compare generated_bits under nominal vs degraded pointing.",
        evidence={"adcs_slew_final_sigma_norm": adcs_slew.get("final_sigma_norm"), "payload_pointing_limited_bits": payload_limited.get("generated_bits")},
    )
    profiles["adcs_payload"] = {"adcs_nadir": adcs_nadir, "adcs_slew": adcs_slew, "payload_observation": payload_obs, "payload_pointing_limited": payload_limited}

    # ADCS + EPS: EPS has an ADCS load channel.  Dynamic torque-to-power feedback
    # is not yet wired, so supported checks are limited to the exposed load channel.
    pair = "ADCS+EPS"
    eps_base = eps.EPSBasiliskConfig(
        duration_s=180.0,
        step_s=10.0,
        initial_soc=0.72,
        solar_power_w=75.0,
        bus_power_w=12.0,
        payload_power_w=0.0,
        adcs_power_w=4.0,
        comm_power_w=0.0,
        heater_power_w=0.0,
    )
    eps_low_adcs = _run_eps_cfg(eps, eps_base)
    eps_high_adcs = _run_eps_cfg(eps, replace(eps_base, adcs_power_w=45.0))
    _check(
        checks,
        check_id="adcs_eps.high_adcs_load_final_soc_lt_low",
        pair=pair,
        metric="final_soc",
        lhs_label="eps_high_adcs_load",
        lhs=_metric(eps_high_adcs, "final_soc"),
        op="lt",
        rhs_label="eps_low_adcs_load",
        rhs=_metric(eps_low_adcs, "final_soc"),
        expected="higher ADCS electrical load lowers EPS final SOC in the Basilisk EPS/PDU profile",
        support="supported_pairwise_profile",
        evidence={"coupling_path": "EPS simplePowerSink channel named adcs"},
    )
    _check(
        checks,
        check_id="adcs_eps.slew_control_effort_gt_nadir",
        pair=pair,
        metric="control_effort_proxy",
        lhs_label="adcs_slew",
        lhs=_metric(adcs_slew, "control_effort_proxy"),
        op="gt",
        rhs_label="adcs_nadir_pointing",
        rhs=_metric(adcs_nadir, "control_effort_proxy"),
        expected="ADCS slew scenario increases control effort proxy relative to nadir pointing",
        support="supported_subsystem_basilisk_qoi",
    )
    _partial(
        checks,
        check_id="adcs_eps.control_effort_to_power_load_feedback",
        pair=pair,
        metric="eps_load_after_adcs_control_effort_change",
        reason="ADCS control effort is measurable and EPS has an ADCS load channel, but current runners do not convert RW/MTB command effort into a dynamic EPS power sink demand.",
        next_step="Add an ADCS actuator power estimator or RW motor electrical model feeding EPS adcs load before asserting torque-effort-driven EPS power changes.",
        evidence={"eps_low_adcs_final_soc": eps_low_adcs.get("final_soc"), "eps_high_adcs_final_soc": eps_high_adcs.get("final_soc")},
    )
    profiles["adcs_eps"] = {"eps_low_adcs_load": eps_low_adcs, "eps_high_adcs_load": eps_high_adcs, "adcs_nadir": adcs_nadir, "adcs_slew": adcs_slew}

    # Thermal + Payload: use the unified Basilisk assembly's payload heat input
    # for a real single-SimBaseClass temperature response.  It is still
    # config-driven rather than generated directly from the payload instrument.
    pair = "Thermal+Payload"
    no_payload_heat = _run_unified(replace(base_cfg, propulsion_enabled=False, payload_heat_w=0.0), label="payload_heat_0w")
    high_payload_heat = _run_unified(replace(base_cfg, propulsion_enabled=False, payload_heat_w=55.0), label="payload_heat_55w")
    payload_standby = payload.run_normal_scenario("standby")
    payload_data = payload.run_normal_scenario("data_generation")
    _check(
        checks,
        check_id="thermal_payload.payload_heat_temp_rise_gt_zero_heat",
        pair=pair,
        metric="temp_rise_c",
        lhs_label="payload_heat_55w",
        lhs=_metric(high_payload_heat, "temp_rise_c"),
        op="gt",
        rhs_label="payload_heat_0w",
        rhs=_metric(no_payload_heat, "temp_rise_c"),
        expected="configured payload heat input raises thermal node temperature in the unified Basilisk assembly",
        support="supported_unified_basilisk_profile",
        evidence={"scheduled_custom_modules": high_payload_heat.get("scheduled_custom_modules")},
    )
    _check(
        checks,
        check_id="thermal_payload.payload_data_bits_gt_standby",
        pair=pair,
        metric="generated_bits",
        lhs_label="payload_data_generation",
        lhs=_metric(payload_data, "generated_bits"),
        op="gt",
        rhs_label="payload_standby",
        rhs=_metric(payload_standby, "generated_bits"),
        expected="payload data-generation profile produces more science bits than standby",
        support="supported_subsystem_basilisk_qoi",
    )
    _partial(
        checks,
        check_id="thermal_payload.generated_payload_power_to_thermal_heat_feedback",
        pair=pair,
        metric="thermal_temperature_from_payload_runtime_generation",
        reason="The unified runner contains both payload instrument and thermal scheduled node, but thermal heat is still a configured ConstantThermalPowerInput rather than a live conversion from payload power/generation state.",
        next_step="Bridge payload enabled/power/heat output into ThermalNodeScheduledSysModel heat input before claiming a dynamic payload-to-thermal closed loop.",
        evidence={"high_payload_heat_temp_rise_c": high_payload_heat.get("temp_rise_c"), "payload_data_generation_bits": payload_data.get("generated_bits")},
    )
    profiles["thermal_payload"] = {"payload_heat_0w": no_payload_heat, "payload_heat_55w": high_payload_heat, "payload_standby": payload_standby, "payload_data_generation": payload_data}

    pairs = ("Propulsion+ADCS", "ADCS+Payload", "ADCS+EPS", "Thermal+Payload")
    fail_count = sum(1 for row in checks if row["status"] == "FAIL")
    partial_count = sum(1 for row in checks if row["status"] == "PARTIAL")
    status = "FAIL" if fail_count else ("PASS_WITH_PARTIAL_INTEGRATION" if partial_count else "PASS")
    summary = {
        "batch": "INTEG-BSK-QOI-2",
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
