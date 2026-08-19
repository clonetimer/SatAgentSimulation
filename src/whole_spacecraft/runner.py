"""Whole-spacecraft Basilisk runner."""
from __future__ import annotations

import bisect
import csv
import json
import math
from math import atan, isfinite, pi
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

class _LazyBasiliskMacros:
    """Load Basilisk utilities only when a simulation actually executes."""

    def __getattr__(self, name: str) -> Any:
        from Basilisk.utilities import macros as basilisk_macros

        return getattr(basilisk_macros, name)


macros = _LazyBasiliskMacros()

from parameters.provenance import (
    build_parameter_provenance_payload,
    calibration_status_from_gate,
)

from subsystems.adcs.degradation import ADCSDegradation
from subsystems.eps.degradation import EPSDegradation
from subsystems.propulsion.degradation import PropulsionDegradation
from subsystems.thermal.degradation import ThermalDegradation

from .schemas import (
    WholeSpacecraftConfig,
    WholeSpacecraftGraph,
    WholeSpacecraftRunConfig,
    WholeSpacecraftSummary,
    WholeSpacecraftTraceRow,
)
from .builder import build_whole_spacecraft_graph
from .task_cadence import evaluate_task_cadence
from .degradation import DegradationScenario, WholeSpacecraftDegradation, get_degradation_scenario_config
from .faults import runtime_fault_specs_for_scenario




def _is_finite_scalar(value: Any) -> bool:
    try:
        return isfinite(float(value))
    except Exception:
        return False


def _finite_rows_ok(rows: list[WholeSpacecraftTraceRow]) -> bool:
    critical_fields = (
        "battery_storage_j",
        "battery_soc",
        "net_power_w",
        "data_storage_bits",
        "thermal_temp_c",
        "thermal_margin_c",
        "attitude_error_norm",
        "rate_error_norm_rad_s",
        "propellant_remaining_kg",
    )
    return bool(rows) and all(_is_finite_scalar(getattr(row, field)) for row in rows for field in critical_fields)


def _finite_adcs_recorders_ok(recorders: dict[str, Any]) -> bool:
    for rec_name, fields in (
        ("adcs_attitude", ("sigma_BR", "omega_BR_B")),
        ("adcs_rate", ("sigma_BR", "omega_BR_B")),
        ("rw_speeds", ("wheelSpeeds",)),
    ):
        recorder = recorders.get(rec_name)
        if recorder is None:
            return False
        for field in fields:
            if not hasattr(recorder, field):
                continue
            try:
                values = getattr(recorder, field)
            except Exception:
                return False
            try:
                iterator = values.flatten() if hasattr(values, "flatten") else values
                for item in iterator:
                    if isinstance(item, (list, tuple)):
                        if not all(_is_finite_scalar(x) for x in item):
                            return False
                    elif not _is_finite_scalar(item):
                        return False
            except Exception:
                return False
    return True


TRACE_FIELDS: tuple[str, ...] = (
    "time_s",
    "battery_storage_j",
    "battery_soc",
    "net_power_w",
    "battery_temp_c",
    "shunt_dissipated_w",
    "data_storage_bits",
    "instrument_baud_bps",
    "transmitter_baud_bps",
    "thermal_temp_c",
    "thermal_margin_c",
    "thermal_safe",
    "attitude_error_norm",
    "attitude_error_deg",
    "rate_error_norm_rad_s",
    "payload_enabled",
    "payload_generated_bps",
    "downlink_requested_rate_bps",
    "downlink_rate_bps",
    "downlink_attempted_bps",
    "downlink_delivered_bps",
    "downlink_dropped_bps",
    "downlink_storage_removal_bps",
    "downlink_ber",
    "downlink_per",
    "cumulative_delivered_bits",
    "cumulative_dropped_bits",
    "cumulative_removed_bits",
    "power_bus_available",
    "propellant_remaining_kg",
    "fuel_mass_flow_kg_s",
    "tank_pressure_pa",
    "tank_pressure_available",
    "adcs_control_power_w",
    "propulsion_power_w",
    "propulsion_activity",
    "propulsion_cumulative_energy_j",
    "heater_eps_load_w",
    "heater_active_count",
    "pdu_heater_enabled",
    "eclipse_shadow_factor",
    "ground_access",
    "ground_slant_range_m",
    "rf_link_distance_m",
    "rf_cnr_linear",
    "orbit_radius_m",
    "orbit_speed_m_s",
    "spacecraft_position_x_m",
    "thermal_adcs_temp_c",
    "thermal_structure_temp_c",
    "thermal_solar_panel_temp_c",
    "gravity_coupling_enabled",
    "propulsion_effector_coupling_enabled",
    "propellant_used_kg",
)


def _mrp_norm_to_principal_angle_deg(sigma_norm: float) -> float:
    """Return the MRP principal rotation angle in degrees."""

    return 4.0 * atan(max(0.0, float(sigma_norm))) * 180.0 / pi


def _rec_value(recorder: Any | None, field: str, index: int, default: float = 0.0) -> float:
    if recorder is None or not hasattr(recorder, field):
        return float(default)
    try:
        values = getattr(recorder, field)
        if index < len(values):
            return float(values[index])
    except Exception:
        return float(default)
    return float(default)


def _rec_vector(recorder: Any | None, field: str, index: int) -> tuple[float, ...]:
    if recorder is None or not hasattr(recorder, field):
        return ()
    try:
        values = getattr(recorder, field)
        if index >= len(values):
            return ()
        value = values[index]
        if hasattr(value, "flatten"):
            value = value.flatten()
        return tuple(float(item) for item in value)
    except Exception:
        return ()


def _vector_norm(values: tuple[float, ...]) -> float:
    return sum(value * value for value in values) ** 0.5


def _trace_sample_at(trace: list[Any], time_s: float) -> Any | None:
    if not trace:
        return None
    times = [float(getattr(item, "time_ns", 0)) * macros.NANO2SEC for item in trace]
    index = bisect.bisect_right(times, float(time_s) + 1e-12) - 1
    if index < 0:
        return trace[0]
    return trace[min(index, len(trace) - 1)]


def _trace_cumulative_energy_at(trace: list[Any], time_s: float) -> float:
    if len(trace) < 2:
        return 0.0
    total = 0.0
    previous_t = float(getattr(trace[0], "time_ns", 0)) * macros.NANO2SEC
    for item in trace[1:]:
        current_t = float(getattr(item, "time_ns", 0)) * macros.NANO2SEC
        if previous_t >= float(time_s):
            break
        end_t = min(current_t, float(time_s))
        if end_t > previous_t:
            total += max(0.0, float(getattr(item, "electrical_power_w", 0.0))) * (end_t - previous_t)
        previous_t = current_t
        if current_t >= float(time_s):
            break
    return total


def _recorder_times_s(recorder: Any | None) -> list[float]:
    if recorder is None or not hasattr(recorder, "times"):
        return []
    try:
        return [float(value) * macros.NANO2SEC for value in recorder.times()]
    except Exception:
        return []


def _recorder_values(recorder: Any | None, field: str) -> list[float]:
    if recorder is None or not hasattr(recorder, field):
        return []
    try:
        return [float(value) for value in getattr(recorder, field)]
    except Exception:
        return []


def _max_recorder_interval_s(recorder: Any | None, fallback_s: float) -> float:
    """Return the largest native recorder interval used by conservation integration."""

    times = _recorder_times_s(recorder)
    intervals = [max(0.0, times[index] - times[index - 1]) for index in range(1, len(times))]
    return max([max(1.0e-9, float(fallback_s)), *intervals])


def _integrate_recorder_field(
    recorder: Any | None,
    field: str,
    *,
    nonnegative: bool = False,
    absolute: bool = False,
) -> float:
    """Right-endpoint integral for a recorder field over its native samples.

    Basilisk recorders at t=0 can observe a value before scheduled command
    modules execute.  The right-endpoint convention therefore matches the rate
    active during the following completed interval more closely than including
    the t=0 sample as a full interval.
    """

    times = _recorder_times_s(recorder)
    values = _recorder_values(recorder, field)
    count = min(len(times), len(values))
    total = 0.0
    for index in range(1, count):
        dt = max(0.0, times[index] - times[index - 1])
        value = values[index]
        if absolute:
            value = abs(value)
        if nonnegative:
            value = max(0.0, value)
        total += value * dt
    return total


def _first_last_recorder_value(recorder: Any | None, field: str) -> tuple[float, float]:
    values = _recorder_values(recorder, field)
    if not values:
        return 0.0, 0.0
    return values[0], values[-1]


def _native_downlink_sample(recorders: dict[str, Any], index: int) -> dict[str, float]:
    rec = recorders.get("downlink")
    node_rec = recorders.get("downlink_node")
    return {
        "attempted": _rec_value(rec, "attemptedDataRate", index),
        "delivered": _rec_value(rec, "deliveredDataRate", index),
        "dropped": _rec_value(rec, "droppedDataRate", index),
        "removed": _rec_value(rec, "storageRemovalRate", index),
        "ber": _rec_value(rec, "ber", index),
        "per": _rec_value(rec, "per", index),
        "cum_delivered": _rec_value(rec, "cumulativeDeliveredBits", index),
        "cum_dropped": _rec_value(rec, "cumulativeDroppedBits", index),
        "cum_removed": _rec_value(rec, "cumulativeRemovedBits", index),
        "node_baud": _rec_value(node_rec, "baudRate", index),
    }




def _native_ground_access_sample(recorders: dict[str, Any], index: int) -> dict[str, float]:
    rec = recorders.get("ground_access")
    return {
        "has_access": _rec_value(rec, "hasAccess", index),
        "slant_range_m": _rec_value(rec, "slantRange", index),
        "elevation_rad": _rec_value(rec, "elevation", index),
    }

def _runtime_unsupported_count(audit: dict[str, Any] | None) -> int:
    if not audit:
        return 0
    count = 0
    for item in (audit.get("events") or {}).values():
        route = item.get("route") or {}
        if not route.get("handled"):
            count += 1
    return count


def _runtime_open_circuit_windows(audit: dict[str, Any] | None) -> tuple[tuple[float, float], ...]:
    windows: list[tuple[float, float]] = []
    if not audit:
        return ()
    for item in (audit.get("events") or {}).values():
        spec = item.get("spec") or {}
        if str(spec.get("fault_type")) != "open_circuit" or not item.get("injection_triggered"):
            continue
        start = float(spec.get("onset_time_s", 0.0))
        duration = float(spec.get("duration_s", -1.0))
        end = start + duration if duration >= 0.0 else float("inf")
        windows.append((start, end))
    return tuple(windows)


def _power_bus_available_at(time_s: float, windows: tuple[tuple[float, float], ...]) -> bool:
    return not any(start <= float(time_s) <= end for start, end in windows)


def _structure_for_run_config(cfg: WholeSpacecraftRunConfig) -> WholeSpacecraftConfig:
    """Resolve structure while keeping output sampling independent from physics tasks."""

    structure = cfg.structure or WholeSpacecraftConfig()
    if abs(float(structure.recorder_step_s) - float(cfg.sample_s)) > 1e-12:
        structure = replace(structure, recorder_step_s=float(cfg.sample_s))
    return structure


def _apply_run_config_to_graph(graph: WholeSpacecraftGraph, cfg: WholeSpacecraftRunConfig) -> None:
    if graph.mission_gate is not None:
        graph.mission_gate.access_window_s = float(cfg.access_window_s)
        graph.mission_gate.access_period_s = float(cfg.access_period_s)
        graph.mission_gate.max_pointing_error_deg = float(cfg.max_pointing_error_deg)


def run_whole_spacecraft_native_case(
    cfg: WholeSpacecraftRunConfig | None = None,
) -> tuple[WholeSpacecraftSummary, tuple[WholeSpacecraftTraceRow, ...]]:
    """Run the whole-spacecraft Basilisk simulation."""

    cfg = cfg or WholeSpacecraftRunConfig()
    if min(cfg.duration_s, cfg.sample_s) <= 0:
        raise ValueError("duration_s and sample_s must be positive")

    structure_cfg = _structure_for_run_config(cfg)
    graph = build_whole_spacecraft_graph(structure_cfg)
    _apply_run_config_to_graph(graph, cfg)

    return _execute_whole_spacecraft_graph(graph, cfg)


def _whole_spacecraft_native_modules(graph: WholeSpacecraftGraph) -> tuple[str, ...]:
    modules = [
        "spacecraft.Spacecraft",
        "simpleNav.SimpleNav",
        "inertial3D.inertial3D",
        "attTrackingError.attTrackingError",
        "mrpFeedback.mrpFeedback",
        "rwMotorTorque.rwMotorTorque",
        "reactionWheelStateEffector.ReactionWheelStateEffector",
        "simpleBattery.SimpleBattery",
        "simplePowerSink.SimplePowerSink",
        "simpleSolarPanel.SimpleSolarPanel",
        "ReactionWheelPower.ReactionWheelPower",
        "antennaPower.AntennaPower",
        "simpleInstrument.SimpleInstrument",
        "simpleStorageUnit.SimpleStorageUnit",
        "simpleTransmitter.SimpleTransmitter",
    ]
    if graph.component_registry.get("native_downlink") is not None:
        modules.extend([
            "groundLocation.GroundLocation",
            "simpleAntenna.SimpleAntenna",
            "linkBudget.LinkBudget",
            "downlinkHandling.DownlinkHandling",
            "AccessMsg",
            "AntennaLogMsg",
            "LinkBudgetMsg",
            "DownlinkHandlingMsg",
        ])
    if graph.orb_env_graph is not None:
        mag_tag = getattr(graph.orb_env_graph.magnetic_field_module, "ModelTag", "")
        modules.append("magneticFieldWMM.MagneticFieldWMM" if "WMM" in str(mag_tag) else "magneticFieldCenteredDipole.MagneticFieldCenteredDipole")
        if graph.orb_env_graph.eclipse_module is not None:
            modules.append("eclipse.Eclipse")
        if graph.orb_env_graph.spice_module is not None:
            modules.append("spiceInterface.SpiceInterface")
    if graph.propulsion_graph is not None:
        modules.extend([
            "thrusterDynamicEffector.ThrusterDynamicEffector",
            "fuelTank.FuelTank",
        ])
    return tuple(dict.fromkeys(modules))


def _execute_whole_spacecraft_graph(
    graph: WholeSpacecraftGraph,
    cfg: WholeSpacecraftRunConfig,
) -> tuple[WholeSpacecraftSummary, tuple[WholeSpacecraftTraceRow, ...]]:
    structure_cfg = _structure_for_run_config(cfg)
    project_root = Path(__file__).resolve().parents[2]
    parameter_payload = build_parameter_provenance_payload(
        project_root,
        registry_path=structure_cfg.parameter_registry_path,
        requested_profile=structure_cfg.parameter_profile,
    )
    parameter_gate = dict(parameter_payload.get("summary", {}))
    if bool(structure_cfg.strict_parameter_provenance) and parameter_gate.get("status") != "PASS":
        raise ValueError(
            "Parameter provenance gate failed for profile "
            f"{structure_cfg.parameter_profile}: "
            f"missing={parameter_gate.get('missing_parameter_count')} "
            f"invalid={parameter_gate.get('invalid_parameter_count')}"
        )

    sim = graph.sim
    recorders = graph.recorders
    thermal = graph.thermal_graph.evaluator
    thermal_cfg = graph.thermal_graph.config

    eps_cfg = graph.eps_graph.cfg
    nominal_net_power = abs(eps_cfg.solar_power_w) - sum(abs(float(v)) for v in eps_cfg.loads_w.values())

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s)))
    sim.ExecuteSimulation()

    runtime_task_cadence = evaluate_task_cadence(
        graph.task_cadence_probes,
        duration_s=float(cfg.duration_s),
    )

    runtime_fault_audit = graph.fault_injector.event_audit() if graph.fault_injector is not None else None
    runtime_unsupported_count = _runtime_unsupported_count(runtime_fault_audit)
    open_circuit_windows = _runtime_open_circuit_windows(runtime_fault_audit)

    battery_rec = recorders["battery"]
    storage_rec = recorders["storage"]
    inst_rec = recorders["instrument"]
    tx_rec = recorders["transmitter"]
    fuel_tank_rec = recorders.get("fuel_tank")
    att_sigma = list(recorders["adcs_attitude"].sigma_BR)
    att_rate = list(recorders["adcs_rate"].omega_BR_B)

    # Build time-indexed thermal truth from the scheduled thermal network trace.
    thermal_by_node: dict[str, list[Any]] = {}
    has_network = hasattr(graph.thermal_graph, "network") and graph.thermal_graph.network.nodes
    if has_network and hasattr(graph.thermal_graph.network, "trace"):
        for thermal_row in list(graph.thermal_graph.network.trace):
            thermal_by_node.setdefault(str(thermal_row.node_name), []).append(thermal_row)
    elif hasattr(thermal, "trace"):
        for thermal_row in list(thermal.trace):
            node_name = str(getattr(thermal_row, "node_name", "thermal"))
            thermal_by_node.setdefault(node_name, []).append(thermal_row)
    thermal_times_by_node = {
        node: [float(getattr(row, "time_s", 0.0)) for row in rows]
        for node, rows in thermal_by_node.items()
    }

    def _thermal_row_at(node_name: str, time_s: float):
        node_rows = thermal_by_node.get(node_name) or thermal_by_node.get("thermal") or []
        if not node_rows:
            return None
        node_times = thermal_times_by_node.get(node_name) or thermal_times_by_node.get("thermal") or []
        idx = bisect.bisect_right(node_times, time_s + 1e-9) - 1
        idx = max(0, min(idx, len(node_rows) - 1))
        return node_rows[idx]

    def _thermal_sample(time_s: float) -> tuple[float, float, bool, float]:
        if has_network:
            battery_row = _thermal_row_at("battery", time_s)
            electronics_row = _thermal_row_at("electronics", time_s)
            if battery_row is not None:
                battery_temp_c = float(battery_row.temp_k) - 273.15
            else:
                battery_node = graph.thermal_graph.network.nodes.get("battery")
                battery_temp_c = float(getattr(battery_node, "temp_k", 298.15)) - 273.15
            if electronics_row is not None:
                thermal_temp_c = float(electronics_row.temp_k) - 273.15
            else:
                thermal_temp_c = battery_temp_c
            node_rows = [
                row for rows in thermal_by_node.values()
                for row in rows
                if abs(float(getattr(row, "time_s", -1.0)) - float(time_s)) <= max(1e-9, float(cfg.sample_s) * 0.51)
            ]
            thermal_safe = all(bool(getattr(row, "thermal_safe", True)) for row in node_rows) if node_rows else all(node.thermal_safe for node in graph.thermal_graph.network.nodes.values())
            margins = []
            for name, node in graph.thermal_graph.network.nodes.items():
                row = _thermal_row_at(name, time_s)
                temp_k = float(getattr(row, "temp_k", node.temp_k)) if row is not None else float(node.temp_k)
                margins.append(float(node.params.max_temp_k) - temp_k)
            thermal_margin_c = min(margins, default=50.0)
            return battery_temp_c, thermal_temp_c, thermal_safe, thermal_margin_c
        row = _thermal_row_at("thermal", time_s)
        if row is not None:
            return (
                float(getattr(row, "temp_c", thermal_cfg.initial_temp_c if hasattr(thermal_cfg, "initial_temp_c") else 25.0)),
                float(getattr(row, "temp_c", thermal_cfg.initial_temp_c if hasattr(thermal_cfg, "initial_temp_c") else 25.0)),
                bool(getattr(row, "thermal_safe", True)),
                float(getattr(row, "thermal_margin_c", 50.0)),
            )
        return 25.0, thermal_cfg.initial_temp_c if hasattr(thermal_cfg, "initial_temp_c") else 25.0, True, (thermal_cfg.max_safe_temp_c - thermal_cfg.initial_temp_c if hasattr(thermal_cfg, "max_safe_temp_c") else 50.0)

    def _thermal_node_temp_c(node_name: str, time_s: float, default_c: float = 0.0) -> float:
        row = _thermal_row_at(node_name, time_s)
        if row is not None and hasattr(row, "temp_k"):
            return float(row.temp_k) - 273.15
        if has_network:
            node = graph.thermal_graph.network.nodes.get(node_name)
            if node is not None:
                return float(getattr(node, "temp_k", default_c + 273.15)) - 273.15
        return float(default_c)

    adcs_power_trace = list(getattr(graph.component_registry.get("adcs_power_bridge"), "trace", ()))
    propulsion_power_trace = list(getattr(graph.component_registry.get("propulsion_power_bridge"), "trace", ()))
    fuel_values = _recorder_values(fuel_tank_rec, "fuelMass")
    initial_propellant_kg = fuel_values[0] if fuel_values else 0.0
    gravity_enabled_value = 1.0 if graph.coupling_matrix.get("gravity_to_spacecraft") and graph.coupling_matrix["gravity_to_spacecraft"].coupling_status != "disabled_by_schema" else 0.0
    propulsion_effector_enabled_value = 1.0 if graph.coupling_matrix.get("propulsion_effector_to_spacecraft") and graph.coupling_matrix["propulsion_effector_to_spacecraft"].coupling_status != "disabled_by_schema" else 0.0

    rows: list[WholeSpacecraftTraceRow] = []
    times = list(battery_rec.times())
    for i, t_ns in enumerate(times):
        time_s = float(t_ns) * macros.NANO2SEC
        cap = float(battery_rec.storageCapacity[i])
        storage_j = float(battery_rec.storageLevel[i])
        data_level = float(storage_rec.storageLevel[i]) if hasattr(storage_rec, "storageLevel") else 0.0
        sigma_vec = [float(x) for x in att_sigma[i]] if i < len(att_sigma) else [0.0, 0.0, 0.0]
        rate_vec = [float(x) for x in att_rate[i]] if i < len(att_rate) else [0.0, 0.0, 0.0]
        attitude_error_norm = sum(x * x for x in sigma_vec) ** 0.5
        attitude_error_deg = _mrp_norm_to_principal_angle_deg(attitude_error_norm)
        propellant_remaining_kg = 0.0
        fuel_mass_flow_kg_s = 0.0
        tank_pressure_pa = 0.0
        tank_pressure_available = False
        if fuel_tank_rec is not None and i < len(fuel_tank_rec.times()):
            propellant_remaining_kg = float(fuel_tank_rec.fuelMass[i]) if hasattr(fuel_tank_rec, "fuelMass") else 0.0
            fuel_mass_flow_kg_s = float(fuel_tank_rec.fuelMassDot[i]) if hasattr(fuel_tank_rec, "fuelMassDot") else 0.0
            if hasattr(fuel_tank_rec, "pressure"):
                tank_pressure_pa = float(fuel_tank_rec.pressure[i])
                tank_pressure_available = True
        battery_temp_c, thermal_temp_c, thermal_safe, thermal_margin_c = _thermal_sample(time_s)
        downlink = _native_downlink_sample(recorders, i)
        _ground_access = _native_ground_access_sample(recorders, i)
        net_power_w = _rec_value(battery_rec, "currentNetPower", i, nominal_net_power)
        sc_position = _rec_vector(recorders.get("spacecraft_state"), "r_BN_N", i)
        sc_velocity = _rec_vector(recorders.get("spacecraft_state"), "v_BN_N", i)
        adcs_sample = _trace_sample_at(adcs_power_trace, time_s)
        propulsion_sample = _trace_sample_at(propulsion_power_trace, time_s)
        heater_names = [name for name in recorders if name.startswith("heater_") and name != "heater_eps_feedback"]
        heater_active_count = sum(1.0 for name in heater_names if _rec_value(recorders.get(name), "deviceStatus", i, 0.0) > 0.5)
        pdu_heater_enabled = 1.0 if _rec_value(recorders.get("pdu_heater_status"), "deviceStatus", i, 0.0) > 0.5 else 0.0
        rf_cnr = max(
            _rec_value(recorders.get("rf_link_budget"), "CNR1", i, 0.0),
            _rec_value(recorders.get("rf_link_budget"), "CNR2", i, 0.0),
        )
        rows.append(
            WholeSpacecraftTraceRow(
                time_s=time_s,
                battery_storage_j=storage_j,
                battery_soc=storage_j / cap if cap else 0.0,
                net_power_w=net_power_w,
                battery_temp_c=battery_temp_c,
                shunt_dissipated_w=0.0,
                data_storage_bits=data_level,
                instrument_baud_bps=float(inst_rec.baudRate[i]) if hasattr(inst_rec, "baudRate") else 0.0,
                transmitter_baud_bps=float(tx_rec.baudRate[i]) if hasattr(tx_rec, "baudRate") else 0.0,
                thermal_temp_c=thermal_temp_c,
                thermal_margin_c=thermal_margin_c,
                thermal_safe=thermal_safe,
                attitude_error_norm=attitude_error_norm,
                attitude_error_deg=attitude_error_deg,
                rate_error_norm_rad_s=sum(x * x for x in rate_vec) ** 0.5,
                payload_enabled=False,
                payload_generated_bps=0.0,
                downlink_requested_rate_bps=0.0,
                downlink_rate_bps=downlink["delivered"],
                downlink_attempted_bps=downlink["attempted"],
                downlink_delivered_bps=downlink["delivered"],
                downlink_dropped_bps=downlink["dropped"],
                downlink_storage_removal_bps=downlink["removed"],
                downlink_ber=downlink["ber"],
                downlink_per=downlink["per"],
                cumulative_delivered_bits=downlink["cum_delivered"],
                cumulative_dropped_bits=downlink["cum_dropped"],
                cumulative_removed_bits=downlink["cum_removed"],
                power_bus_available=_power_bus_available_at(time_s, open_circuit_windows),
                propellant_remaining_kg=propellant_remaining_kg,
                fuel_mass_flow_kg_s=fuel_mass_flow_kg_s,
                tank_pressure_pa=tank_pressure_pa,
                tank_pressure_available=tank_pressure_available,
                adcs_control_power_w=max(0.0, -_rec_value(recorders.get("adcs_power"), "netPower", i, float(getattr(adcs_sample, "electrical_power_w", 0.0)))),
                propulsion_power_w=max(0.0, -_rec_value(recorders.get("propulsion_power"), "netPower", i, float(getattr(propulsion_sample, "electrical_power_w", 0.0)))),
                propulsion_activity=float(getattr(propulsion_sample, "activity", 0.0)),
                propulsion_cumulative_energy_j=_trace_cumulative_energy_at(propulsion_power_trace, time_s),
                heater_eps_load_w=max(0.0, -_rec_value(recorders.get("heater_eps_feedback"), "netPower", i, 0.0)),
                heater_active_count=heater_active_count,
                pdu_heater_enabled=pdu_heater_enabled,
                eclipse_shadow_factor=_rec_value(recorders.get("eclipse"), "shadowFactor", i, 1.0),
                ground_access=1.0 if _ground_access["has_access"] > 0.5 else 0.0,
                ground_slant_range_m=_ground_access["slant_range_m"],
                rf_link_distance_m=_rec_value(recorders.get("rf_link_budget"), "distance", i, 0.0),
                rf_cnr_linear=rf_cnr,
                orbit_radius_m=_vector_norm(sc_position),
                orbit_speed_m_s=_vector_norm(sc_velocity),
                spacecraft_position_x_m=sc_position[0] if sc_position else 0.0,
                thermal_adcs_temp_c=_thermal_node_temp_c("adcs", time_s, thermal_temp_c),
                thermal_structure_temp_c=_thermal_node_temp_c("structure", time_s, thermal_temp_c),
                thermal_solar_panel_temp_c=_thermal_node_temp_c("solar_panel", time_s, thermal_temp_c),
                gravity_coupling_enabled=gravity_enabled_value,
                propulsion_effector_coupling_enabled=propulsion_effector_enabled_value,
                propellant_used_kg=max(0.0, initial_propellant_kg - propellant_remaining_kg),
            )
        )

    mission_trace = list(graph.mission_gate.trace) if graph.mission_gate is not None else []
    mission_times = [item.time_s for item in mission_trace] if mission_trace else []
    if mission_trace:
        gated_rows: list[WholeSpacecraftTraceRow] = []
        for row in rows:
            idx = bisect.bisect_right(mission_times, row.time_s + 1e-9) - 1
            idx = max(0, min(idx, len(mission_trace) - 1))
            gate = mission_trace[idx]
            gated_rows.append(
                replace(
                    row,
                    payload_enabled=gate.payload_enabled,
                    payload_generated_bps=gate.payload_generated_bps,
                    downlink_requested_rate_bps=getattr(gate, "downlink_requested_rate_bps", gate.downlink_rate_bps),
                    downlink_rate_bps=row.downlink_delivered_bps,
                )
            )
        rows = gated_rows

    conservation_battery_rec = recorders.get("conservation_battery")
    if conservation_battery_rec is None:
        conservation_battery_rec = battery_rec
    conservation_storage_rec = recorders.get("conservation_storage")
    if conservation_storage_rec is None:
        conservation_storage_rec = storage_rec
    conservation_instrument_rec = recorders.get("conservation_instrument")
    if conservation_instrument_rec is None:
        conservation_instrument_rec = inst_rec
    conservation_downlink_rec = recorders.get("conservation_downlink")
    if conservation_downlink_rec is None:
        conservation_downlink_rec = recorders.get("downlink")

    battery_initial_j, battery_final_j = _first_last_recorder_value(
        conservation_battery_rec, "storageLevel"
    )
    battery_storage_delta_j = battery_final_j - battery_initial_j
    integrated_net_power_j = _integrate_recorder_field(
        conservation_battery_rec, "currentNetPower"
    )
    energy_balance_residual_j = battery_storage_delta_j - integrated_net_power_j
    net_power_values = _recorder_values(conservation_battery_rec, "currentNetPower")
    energy_conservation_interval_s = _max_recorder_interval_s(
        conservation_battery_rec, float(structure_cfg.adcs_dyn_step_s)
    )
    energy_balance_tolerance_j = max(
        1.0,
        2.0 * max((abs(value) for value in net_power_values), default=0.0)
        * energy_conservation_interval_s,
    )
    energy_balance_relative_error = abs(energy_balance_residual_j) / max(
        1.0, abs(battery_storage_delta_j), abs(integrated_net_power_j)
    )
    energy_conservation_status = (
        "PASS" if abs(energy_balance_residual_j) <= energy_balance_tolerance_j else "FAIL"
    )

    data_initial_bits, data_final_bits = _first_last_recorder_value(
        conservation_storage_rec, "storageLevel"
    )
    data_storage_delta_bits = data_final_bits - data_initial_bits
    integrated_payload_generated_bits = _integrate_recorder_field(
        conservation_instrument_rec, "baudRate", nonnegative=True
    )
    removed_values = _recorder_values(conservation_downlink_rec, "cumulativeRemovedBits")
    if removed_values:
        data_balance_removed_bits = max(removed_values)
    else:
        # The reduced/fixed-rate transmitter reports a negative baudRate sink
        # rather than native cumulative removal telemetry.
        data_balance_removed_bits = _integrate_recorder_field(
            recorders.get("transmitter"), "baudRate", absolute=True
        )
    data_balance_residual_bits = (
        data_initial_bits
        + integrated_payload_generated_bits
        - data_balance_removed_bits
        - data_final_bits
    )
    payload_rate_values = _recorder_values(conservation_instrument_rec, "baudRate")
    data_balance_scale_bits = max(
        1.0,
        integrated_payload_generated_bits,
        abs(data_storage_delta_bits) + data_balance_removed_bits,
    )
    # The physical tolerance covers at most two dynamics ticks of source-rate
    # ordering.  Add only a one-bit/relative floating-point cushion so a residual
    # sitting on that deterministic boundary is not failed by sub-bit RF/ARQ
    # accumulation round-off.
    data_balance_tolerance_bits = max(
        1.0,
        2.0 * max((max(0.0, value) for value in payload_rate_values), default=0.0)
        * max(0.1, float(structure_cfg.adcs_dyn_step_s)),
    ) + max(1.0, 1.0e-9 * data_balance_scale_bits)
    data_balance_relative_error = abs(data_balance_residual_bits) / max(
        1.0,
        integrated_payload_generated_bits,
        abs(data_storage_delta_bits) + data_balance_removed_bits,
    )
    data_conservation_status = (
        "PASS" if abs(data_balance_residual_bits) <= data_balance_tolerance_bits else "FAIL"
    )

    eps_to_thermal_electrical_energy_j = 0.0
    eps_to_thermal_heat_energy_j = 0.0
    heater_thermal_energy_j = 0.0
    thermal_power_bridges = getattr(getattr(graph.thermal_graph, "network", None), "power_bridges", {})
    for bridge in thermal_power_bridges.values():
        for label, energy_j in getattr(bridge, "cumulative_source_electrical_energy_j", {}).items():
            if str(label).startswith("eps."):
                eps_to_thermal_electrical_energy_j += float(energy_j)
        for label, energy_j in getattr(bridge, "cumulative_source_heat_energy_j", {}).items():
            if str(label).startswith("eps."):
                eps_to_thermal_heat_energy_j += float(energy_j)
            elif str(label).startswith("heater."):
                heater_thermal_energy_j += float(energy_j)
    heater_electrical_energy_j = _integrate_recorder_field(
        recorders.get("heater_eps_feedback"), "netPower", absolute=True
    )
    thermal_power_bridge_count = len(thermal_power_bridges)
    thermal_power_bridge_exercised_count = sum(
        1
        for bridge in thermal_power_bridges.values()
        if float(getattr(bridge, "cumulative_heat_energy_j", 0.0)) > 0.0
    )
    solar_rec = recorders.get("solar_power")
    solar_samples: list[float] = []
    if solar_rec is not None and hasattr(solar_rec, "netPower"):
        try:
            solar_samples = [max(0.0, float(value)) for value in solar_rec.netPower]
        except Exception:
            solar_samples = []
    min_solar_power_w = min(solar_samples, default=0.0)
    max_solar_power_w = max(solar_samples, default=0.0)

    def _max_cnr(recorder: Any | None) -> float:
        if recorder is None:
            return 0.0
        values: list[float] = []
        for field_name in ("CNR1", "CNR2"):
            if hasattr(recorder, field_name):
                try:
                    values.extend(max(0.0, float(v)) for v in getattr(recorder, field_name))
                except Exception as exc:
                    raise RuntimeError(
                        f"Failed to read native LinkBudget recorder field {field_name!r}."
                    ) from exc
        return max(values, default=0.0)

    native_rf_cnr_max_linear = _max_cnr(recorders.get("rf_link_budget"))
    gated_downlink_cnr_max_linear = _max_cnr(recorders.get("gated_downlink_link_budget"))
    rf_graph = graph.component_registry.get("native_rf_access_graph")
    rf_environment_wiring_count = len(getattr(rf_graph, "environment_wiring", ())) if rf_graph is not None else 0
    def _coupling_requires_runtime_evidence(name: str) -> bool:
        record = graph.coupling_matrix.get(name)
        if record is None:
            return False
        status = str(record.coupling_status)
        return status not in {
            "disabled_by_schema",
            "not_coupled_no_eclipse_message",
            "not_coupled_no_heater_model",
        }

    proxy_checks: list[bool] = []
    if _coupling_requires_runtime_evidence("eps_loads_to_thermal_heat"):
        if thermal_power_bridges:
            proxy_checks.append(
                bool(
                    eps_to_thermal_electrical_energy_j > 0.0
                    and eps_to_thermal_heat_energy_j > 0.0
                )
            )
        else:
            reduced_heat_input = getattr(graph.thermal_graph, "heat_input", None)
            proxy_checks.append(bool(getattr(reduced_heat_input, "powerInMsgs", ())))
    if _coupling_requires_runtime_evidence("adcs_pointing_to_payload_comm_gate"):
        proxy_checks.append(bool(graph.mission_gate is not None and mission_trace))
    if _coupling_requires_runtime_evidence("thermal_heaters_to_eps_load"):
        proxy_checks.append(bool(recorders.get("heater_eps_feedback") is not None))
    if rf_graph is not None:
        proxy_checks.append(rf_environment_wiring_count >= 8)
    solar_power_coupling_required = _coupling_requires_runtime_evidence("orbit_sun_attitude_eclipse_to_eps_solar_power")
    solar_power_coupling_status = (
        "PASS"
        if (not solar_power_coupling_required or (solar_samples and all(math.isfinite(v) for v in solar_samples)))
        else "FAIL"
    )
    if solar_power_coupling_required:
        proxy_checks.append(solar_power_coupling_status == "PASS")
    proxy_coupling_runtime_status = "PASS" if all(proxy_checks) else "FAIL"

    min_margin = min((r.thermal_margin_c for r in rows), default=float("nan"))
    min_soc = min((r.battery_soc for r in rows), default=float("nan"))
    min_propellant = min((r.propellant_remaining_kg for r in rows), default=float("nan"))
    all_battery_ok = all(r.battery_soc > 0.0 for r in rows) if rows else False
    all_thermal_ok = all(r.thermal_safe for r in rows) if rows else False
    all_storage_ok = all(r.data_storage_bits >= 0.0 for r in rows) if rows else False
    all_power_bus_ok = all(r.power_bus_available for r in rows) if rows else False
    finite_telemetry_ok = _finite_rows_ok(rows) and _finite_adcs_recorders_ok(recorders)
    runtime_fault_ok = True
    if runtime_fault_audit is not None:
        runtime_fault_ok = runtime_fault_audit.get("runtime_event_status") == "PASS" and runtime_unsupported_count == 0
    payload_enabled_count = sum(1 for r in rows if r.payload_enabled)
    access_count = sum(1 for r in rows if r.downlink_requested_rate_bps > 0.0)
    geometric_access_count = 0
    ga_rec = recorders.get("ground_access")
    if ga_rec is not None and hasattr(ga_rec, "hasAccess"):
        try:
            geometric_access_count = sum(1 for value in ga_rec.hasAccess if int(value) != 0)
        except Exception:
            geometric_access_count = access_count
    else:
        geometric_access_count = access_count
    generated_bits = integrated_payload_generated_bits
    delivered_bits = max((r.cumulative_delivered_bits for r in rows), default=0.0)
    dropped_bits = max((r.cumulative_dropped_bits for r in rows), default=0.0)
    structural_status = (
        "PASS"
        if rows and graph.coupling_matrix and runtime_task_cadence.get("status") == "PASS"
        else "FAIL"
    )
    numerical_status = "PASS" if finite_telemetry_ok else "FAIL"
    runtime_injection_status = "PASS" if runtime_fault_ok else "FAIL"
    single_run_physics_ok = (
        all_battery_ok
        and all_thermal_ok
        and all_storage_ok
        and all_power_bus_ok
        and energy_conservation_status == "PASS"
        and data_conservation_status == "PASS"
        and proxy_coupling_runtime_status == "PASS"
    )
    # A single run can establish wiring, finite numerics, conservation and
    # response observations, but not counterfactual causality.  Reserve plain
    # PASS for a separate paired baseline/one-factor perturbation report.
    physics_status = "PASS_SINGLE_RUN_LIMITED" if single_run_physics_ok else "FAIL"
    mission_status = "PASS" if payload_enabled_count > 0 and geometric_access_count > 0 and generated_bits > 0.0 and delivered_bits > 0.0 else "FAIL"
    execution_status = (
        "PASS"
        if structural_status == numerical_status == runtime_injection_status == "PASS" and single_run_physics_ok
        else "FAIL"
    )
    status = "PASS" if execution_status == mission_status == "PASS" else "FAIL"

    def _fraction(predicate) -> float:
        return (sum(1 for row in rows if predicate(row)) / len(rows)) if rows else 0.0

    attitude_ok_fraction = _fraction(lambda row: row.attitude_error_deg <= float(cfg.max_pointing_error_deg))
    power_ok_fraction = _fraction(lambda row: row.power_bus_available and row.battery_soc > 0.0)
    thermal_ok_fraction = _fraction(lambda row: row.thermal_safe)
    payload_enabled_fraction = _fraction(lambda row: row.payload_enabled)
    data_delivery_ratio = min(1.0, delivered_bits / generated_bits) if generated_bits > 0.0 else 0.0
    mission_success_score = max(0.0, min(1.0, (
        attitude_ok_fraction
        + power_ok_fraction
        + thermal_ok_fraction
        + payload_enabled_fraction
        + data_delivery_ratio
    ) / 5.0))

    coupling_records = tuple(graph.coupling_matrix.values())
    disabled_coupling_count = sum(1 for record in coupling_records if record.coupling_status == "disabled_by_schema")
    proxy_coupling_count = sum(1 for record in coupling_records if "proxy" in record.coupling_status)
    native_coupling_count = sum(1 for record in coupling_records if record.coupling_status == "coupled")
    active_coupling_count = len(coupling_records) - disabled_coupling_count

    propulsion_enabled = graph.propulsion_graph is not None
    included_subsystems = ("orbit_environment", "spacecraft_dynamics", "adcs", "eps", "payload", "comm_data", "thermal")
    if propulsion_enabled:
        included_subsystems = included_subsystems + ("propulsion",)
    actual_step_s = float(cfg.sample_s)
    if len(rows) > 1:
        actual_step_s = float(rows[1].time_s - rows[0].time_s)
    summary = WholeSpacecraftSummary(
        backend="whole_spacecraft_basilisk_assembly",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        unified_simbase=True,
        native_modules=_whole_spacecraft_native_modules(graph),
        scheduled_custom_modules=(
            "ThermalStatusEvaluator(SysModel)",
            "ConstantHeatInput(SysModel)",
            "WholeSpacecraftMissionGate(SysModel)",
        ),
        included_subsystems=included_subsystems,
        excluded_subsystems=() if propulsion_enabled else ("propulsion",),
        duration_s=float(cfg.duration_s),
        step_s=actual_step_s,
        dynamics_step_s=float(structure_cfg.adcs_dyn_step_s),
        fsw_step_s=float(structure_cfg.adcs_fsw_step_s),
        orb_env_step_s=float(structure_cfg.orb_env_step_s),
        thermal_step_s=float(structure_cfg.thermal_step_s),
        recorder_step_s=float(structure_cfg.recorder_step_s),
        step_semantics="output_sample_interval_not_physics_integration_step",
        sample_count=len(rows),
        final_soc=rows[-1].battery_soc if rows else 0.0,
        min_soc=min_soc,
        final_data_storage_bits=rows[-1].data_storage_bits if rows else 0.0,
        min_thermal_margin_c=min_margin,
        final_propellant_kg=rows[-1].propellant_remaining_kg if rows else 0.0,
        min_propellant_kg=min_propellant,
        status=status,
        not_claimed=("native_basilisk_thermal_network", "flight_or_ground_calibrated_parameters", "native_tank_pressure_thermodynamics"),
        execution_status=execution_status,
        structural_status=structural_status,
        runtime_injection_status=runtime_injection_status,
        numerical_status=numerical_status,
        physics_status=physics_status,
        coupling_causality_status="NOT_EVALUATED_PAIRED_RUN_REQUIRED",
        physics_claim_scope="single_run_wiring_numerics_conservation_and_response_observation_only",
        mission_status=mission_status,
        calibration_status=calibration_status_from_gate(parameter_gate),
        parameter_profile=str(parameter_gate.get("requested_profile", structure_cfg.parameter_profile)),
        parameter_registry_status=str(parameter_gate.get("registry_status", "UNKNOWN")),
        parameter_minimum_confidence=str(parameter_gate.get("minimum_observed_confidence", "demo")),
        parameter_registry_record_count=int(parameter_gate.get("record_count", 0)),
        assumed_parameter_count=int(parameter_gate.get("assumed_parameter_count", 0)),
        uncalibrated_parameter_count=int(parameter_gate.get("uncalibrated_parameter_count", 0)),
        extrapolated_parameter_count=int(parameter_gate.get("extrapolated_parameter_count", 0)),
        missing_parameter_count=int(parameter_gate.get("missing_parameter_count", 0)),
        invalid_parameter_count=int(parameter_gate.get("invalid_parameter_count", 0)),
        parameter_registry_path=str((parameter_payload.get("registry") or {}).get("registry_path", "")),
        mission_payload_enabled_count=payload_enabled_count,
        mission_access_count=access_count,
        mission_geometric_access_count=geometric_access_count,
        mission_generated_bits=generated_bits,
        mission_delivered_bits=delivered_bits,
        mission_dropped_bits=dropped_bits,
        max_downlink_delivered_bps=max((r.downlink_delivered_bps for r in rows), default=0.0),
        max_downlink_dropped_bps=max((r.downlink_dropped_bps for r in rows), default=0.0),
        final_attitude_error_deg=rows[-1].attitude_error_deg if rows else 0.0,
        runtime_unsupported_count=runtime_unsupported_count,
        runtime_fault_event_status=(runtime_fault_audit.get("runtime_event_status") if runtime_fault_audit is not None else "not_applicable"),
        runtime_fault_event_count=int(runtime_fault_audit.get("fault_count", 0)) if runtime_fault_audit is not None else 0,
        runtime_fault_triggered_count=int(runtime_fault_audit.get("event_triggered_count", 0)) if runtime_fault_audit is not None else 0,
        runtime_fault_fallback_count=int(runtime_fault_audit.get("fallback_count", 0)) if runtime_fault_audit is not None else 0,
        runtime_fault_mutation_target_count=int(runtime_fault_audit.get("mutation_target_count", 0)) if runtime_fault_audit is not None else 0,
        runtime_fault_recovery_expected_count=int(runtime_fault_audit.get("recovery_expected_count", 0)) if runtime_fault_audit is not None else 0,
        runtime_fault_recovery_registered_count=int(runtime_fault_audit.get("recovery_registered_count", 0)) if runtime_fault_audit is not None else 0,
        runtime_fault_recovery_triggered_count=int(runtime_fault_audit.get("recovery_triggered_count", 0)) if runtime_fault_audit is not None else 0,
        runtime_fault_recovery_failure_count=int(runtime_fault_audit.get("recovery_restore_failure_count", 0)) if runtime_fault_audit is not None else 0,
        coupling_count=len(coupling_records),
        active_coupling_count=active_coupling_count,
        native_coupling_count=native_coupling_count,
        proxy_coupling_count=proxy_coupling_count,
        disabled_coupling_count=disabled_coupling_count,
        coupling_runtime_catalog=tuple(
            {
                "coupling_id": record.name,
                "source_subsystem": record.source_subsystem,
                "sink_subsystem": record.sink_subsystem,
                "runtime_status": record.coupling_status,
                "active": record.coupling_status not in {
                    "disabled_by_schema",
                    "not_coupled_no_eclipse_message",
                    "not_coupled_no_heater_model",
                },
                "interface": record.interface,
                "claim_scope": "runtime_wiring_evidence_not_paired_counterfactual_causality",
            }
            for record in coupling_records
        ),
        mission_success_score=mission_success_score,
        mission_success_score_basis="deterministic_gate_fraction_not_probability",
        battery_storage_delta_j=battery_storage_delta_j,
        integrated_net_power_j=integrated_net_power_j,
        energy_balance_residual_j=energy_balance_residual_j,
        energy_balance_relative_error=energy_balance_relative_error,
        energy_balance_tolerance_j=energy_balance_tolerance_j,
        energy_conservation_status=energy_conservation_status,
        data_storage_delta_bits=data_storage_delta_bits,
        integrated_payload_generated_bits=integrated_payload_generated_bits,
        data_balance_removed_bits=data_balance_removed_bits,
        data_balance_residual_bits=data_balance_residual_bits,
        data_balance_relative_error=data_balance_relative_error,
        data_balance_tolerance_bits=data_balance_tolerance_bits,
        data_conservation_status=data_conservation_status,
        eps_to_thermal_electrical_energy_j=eps_to_thermal_electrical_energy_j,
        eps_to_thermal_heat_energy_j=eps_to_thermal_heat_energy_j,
        heater_electrical_energy_j=heater_electrical_energy_j,
        heater_thermal_energy_j=heater_thermal_energy_j,
        thermal_power_bridge_count=thermal_power_bridge_count,
        thermal_power_bridge_exercised_count=thermal_power_bridge_exercised_count,
        proxy_coupling_runtime_status=proxy_coupling_runtime_status,
        solar_power_coupling_status=solar_power_coupling_status,
        solar_power_sample_count=len(solar_samples),
        min_solar_power_w=min_solar_power_w,
        max_solar_power_w=max_solar_power_w,
        native_rf_cnr_max_linear=native_rf_cnr_max_linear,
        gated_downlink_cnr_max_linear=gated_downlink_cnr_max_linear,
        rf_environment_wiring_count=rf_environment_wiring_count,
        runtime_task_cadence_status=str(runtime_task_cadence.get("status", "UNKNOWN")),
        runtime_task_cadence=runtime_task_cadence,
    )
    return summary, tuple(rows)

def write_whole_spacecraft_native_dataset(
    output_dir: str | Path,
    cfg: WholeSpacecraftRunConfig | None = None,
) -> dict[str, str]:
    """Run the simulation and write summary / trace / manifest to output directory."""

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    summary, rows = run_whole_spacecraft_native_case(cfg)
    summary_path = output_dir / "whole_spacecraft_native_summary.json"
    trace_path = output_dir / "whole_spacecraft_native_trace.csv"
    manifest_path = output_dir / "whole_spacecraft_native_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(TRACE_FIELDS))
        writer.writeheader()
        for row in rows:
            writer.writerow(asdict(row))
    manifest = {
        "dataset_type": "whole_spacecraft_basilisk_assembly",
        "backend_truth": "one SimulationBaseClass containing ADCS, EPS, Payload, Comm/Data, Thermal, Propulsion and Orbit/Environment modules",
        "files": {"summary": summary_path.name, "trace": trace_path.name},
        "summary": asdict(summary),
        "parameter_provenance": {
            "profile": summary.parameter_profile,
            "registry_status": summary.parameter_registry_status,
            "calibration_status": summary.calibration_status,
            "registry_path": summary.parameter_registry_path,
            "record_count": summary.parameter_registry_record_count,
            "assumed_parameter_count": summary.assumed_parameter_count,
            "uncalibrated_parameter_count": summary.uncalibrated_parameter_count,
            "extrapolated_parameter_count": summary.extrapolated_parameter_count,
        },
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


def run_whole_spacecraft_degradation_case(
    cfg: WholeSpacecraftRunConfig | None = None,
    eps_degradation: EPSDegradation | None = None,
    adcs_degradation: ADCSDegradation | None = None,
    propulsion_degradation: PropulsionDegradation | None = None,
    thermal_degradation: ThermalDegradation | None = None,
    payload_degradation: Any | None = None,
    comm_data_degradation: Any | None = None,
) -> tuple[WholeSpacecraftSummary, tuple[WholeSpacecraftTraceRow, ...]]:
    cfg = cfg or WholeSpacecraftRunConfig()
    structure_cfg = _structure_for_run_config(cfg)
    degraded_structure = replace(
        structure_cfg,
        eps_degradation=eps_degradation,
        adcs_degradation=adcs_degradation,
        propulsion_degradation=propulsion_degradation,
        thermal_degradation=thermal_degradation,
        payload_degradation=payload_degradation,
        comm_data_degradation=comm_data_degradation,
    )
    graph = build_whole_spacecraft_graph(degraded_structure)
    _apply_run_config_to_graph(graph, cfg)
    return _execute_whole_spacecraft_graph(graph, cfg)


def run_whole_spacecraft_fault_case(
    cfg: WholeSpacecraftRunConfig | None = None,
    fault_specs: list[Any] | None = None,
) -> tuple[WholeSpacecraftSummary, tuple[WholeSpacecraftTraceRow, ...]]:
    cfg = cfg or WholeSpacecraftRunConfig()
    structure_cfg = _structure_for_run_config(cfg)
    fault_structure = replace(structure_cfg, fault_specs=fault_specs or [])
    graph = build_whole_spacecraft_graph(fault_structure)
    _apply_run_config_to_graph(graph, cfg)
    return _execute_whole_spacecraft_graph(graph, cfg)


def run_whole_spacecraft_effect_case(
    cfg: WholeSpacecraftRunConfig | None = None,
    *,
    fault_specs: list[Any] | tuple[Any, ...] | None = None,
    degradation: WholeSpacecraftDegradation | None = None,
) -> tuple[WholeSpacecraftSummary, tuple[WholeSpacecraftTraceRow, ...]]:
    """Run the unified graph with build-time degradation and runtime faults.

    This is the V37-B execution owner for mixed scenarios.  It does not add a
    second model path: both effects are attached to the existing
    ``WholeSpacecraftConfig`` before the same graph builder is invoked.
    """

    cfg = cfg or WholeSpacecraftRunConfig()
    structure_cfg = _structure_for_run_config(cfg)
    effect_structure = replace(
        structure_cfg,
        fault_specs=list(fault_specs or ()),
        eps_degradation=(degradation.eps_degradation if degradation is not None else structure_cfg.eps_degradation),
        adcs_degradation=(degradation.adcs_degradation if degradation is not None else structure_cfg.adcs_degradation),
        propulsion_degradation=(degradation.propulsion_degradation if degradation is not None else structure_cfg.propulsion_degradation),
        thermal_degradation=(degradation.thermal_degradation if degradation is not None else structure_cfg.thermal_degradation),
        payload_degradation=(degradation.payload_degradation if degradation is not None else structure_cfg.payload_degradation),
        comm_data_degradation=(degradation.comm_data_degradation if degradation is not None else structure_cfg.comm_data_degradation),
    )
    graph = build_whole_spacecraft_graph(effect_structure)
    _apply_run_config_to_graph(graph, cfg)
    return _execute_whole_spacecraft_graph(graph, cfg)


def run_whole_spacecraft_all_modes(
    cfg: WholeSpacecraftRunConfig | None = None,
) -> dict[str, Any]:
    results = {}

    print("Running nominal case...")
    summary, _rows = run_whole_spacecraft_native_case(cfg)
    results["nominal"] = {
        "final_soc": summary.final_soc,
        "min_soc": summary.min_soc,
        "final_propellant_kg": summary.final_propellant_kg,
        "min_thermal_margin_c": summary.min_thermal_margin_c,
        "status": summary.status,
        "sample_count": summary.sample_count,
    }

    print("Running degradation case...")
    whole_deg = get_degradation_scenario_config(DegradationScenario.D8)
    eps_deg = whole_deg.eps_degradation
    adcs_deg = whole_deg.adcs_degradation
    propulsion_deg = whole_deg.propulsion_degradation
    thermal_deg = whole_deg.thermal_degradation
    payload_deg = whole_deg.payload_degradation
    comm_data_deg = whole_deg.comm_data_degradation
    summary, _rows = run_whole_spacecraft_degradation_case(cfg, eps_deg, adcs_deg, propulsion_deg, thermal_deg, payload_deg, comm_data_deg)
    results["degradation"] = {
        "final_soc": summary.final_soc,
        "min_soc": summary.min_soc,
        "final_propellant_kg": summary.final_propellant_kg,
        "min_thermal_margin_c": summary.min_thermal_margin_c,
        "status": summary.status,
        "sample_count": summary.sample_count,
        "degradation": {
            "eps_battery_capacity_loss_pct": eps_deg.battery_degradation.capacity_loss_pct,
            "eps_solar_efficiency_loss_pct": eps_deg.solar_panel_degradation.efficiency_loss_pct,
            "adcs_rw_friction_factor": adcs_deg.rw_friction_factor,
            "adcs_sensor_noise_factor": adcs_deg.sensor_noise_factor,
            "propulsion_thrust_loss_pct": propulsion_deg.thruster_degradation.thrust_loss_pct,
            "thermal_heater_efficiency_loss_pct": thermal_deg.heater_degradation.efficiency_loss_pct,
            "payload_instrument_baud_factor": getattr(payload_deg, "instrument_baud_factor", None),
            "comm_transmitter_baud_factor": getattr(comm_data_deg, "transmitter_baud_factor", None),
            "comm_storage_capacity_factor": getattr(comm_data_deg, "storage_capacity_factor", None),
        },
    }

    print("Running fault case (battery_open_circuit)...")
    faults1 = list(runtime_fault_specs_for_scenario("eps_battery_open_circuit"))
    summary, _rows = run_whole_spacecraft_fault_case(cfg, faults1)
    results["fault_battery_open_circuit"] = {
        "final_soc": summary.final_soc,
        "min_soc": summary.min_soc,
        "final_propellant_kg": summary.final_propellant_kg,
        "min_thermal_margin_c": summary.min_thermal_margin_c,
        "status": summary.status,
        "sample_count": summary.sample_count,
        "faults": [{"type": str(f.fault_type), "magnitude": f.magnitude} for f in faults1],
    }

    print("Running fault case (rw_jamming)...")
    faults2 = list(runtime_fault_specs_for_scenario("adcs_rw_jamming"))
    summary, _rows = run_whole_spacecraft_fault_case(cfg, faults2)
    results["fault_rw_jamming"] = {
        "final_soc": summary.final_soc,
        "min_soc": summary.min_soc,
        "final_propellant_kg": summary.final_propellant_kg,
        "min_thermal_margin_c": summary.min_thermal_margin_c,
        "status": summary.status,
        "sample_count": summary.sample_count,
        "faults": [{"type": str(f.fault_type), "magnitude": f.magnitude} for f in faults2],
    }

    return results


def print_whole_spacecraft_summary(data: dict[str, Any]) -> None:
    print("\n" + "=" * 60)
    print("Whole Spacecraft Simulation Results Summary")
    print("=" * 60)

    for mode, result in data.items():
        print(f"\n--- {mode.upper()} ---")
        print(f"Final SOC: {result['final_soc']:.4f}")
        print(f"Min SOC: {result['min_soc']:.4f}")
        print(f"Final propellant: {result['final_propellant_kg']:.4f} kg")
        print(f"Min thermal margin: {result['min_thermal_margin_c']:.2f} C")
        print(f"Status: {result['status']}")
        if "degradation" in result:
            d = result["degradation"]
            print(
                "Degradation: "
                f"battery_capacity_loss={d['eps_battery_capacity_loss_pct']}%, "
                f"solar_efficiency_loss={d['eps_solar_efficiency_loss_pct']}%"
            )
        if "faults" in result:
            for f in result["faults"]:
                print(f"  Fault: {f['type']}, magnitude={f['magnitude']}")


__all__ = [
    "WholeSpacecraftRunConfig",
    "WholeSpacecraftTraceRow",
    "WholeSpacecraftSummary",
    "run_whole_spacecraft_native_case",
    "write_whole_spacecraft_native_dataset",
    "run_whole_spacecraft_degradation_case",
    "run_whole_spacecraft_fault_case",
    "run_whole_spacecraft_effect_case",
    "run_whole_spacecraft_all_modes",
]


if __name__ == "__main__":
    try:
        results = run_whole_spacecraft_all_modes()
        print_whole_spacecraft_summary(results)

        output_file = Path("whole_spacecraft_all_modes.json")
        output_file.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"\nResults saved to: {output_file.name}")
    except ImportError as e:
        print(f"Skipping whole spacecraft test: Basilisk not available - {e}")
