"""THERM-1 orbit/attitude thermal input coupling helpers.

The helpers convert a compact orbit/attitude intent into scalar face-exposure
maps consumed by ``subsystems.thermal.network``.  They deliberately stay at a
reduced-order level and do not claim Basilisk, Thermal Desktop, ESATAN, or
flight-correlated heat-flux equivalence.
"""
from __future__ import annotations

from typing import Any, Mapping

ORBIT_ATTITUDE_THERMAL_SCHEMA_VERSION = "therm1.orbit_attitude_thermal_coupling.v1"


def exposure_maps_for_attitude_mode(mode: str) -> dict[str, dict[str, float]]:
    """Return deterministic face-exposure maps for a coarse attitude mode."""

    mode_key = str(mode or "sun_pointing").strip().lower()
    if mode_key in {"sun_pointing", "sun_safe", "solar_array_sun"}:
        solar = {"+X": 1.0, "+Y": 0.20, "-Y": 0.20}
        albedo = {"-Z": 0.80, "+Z": 0.20}
        earth_ir = {"-Z": 1.0, "+X": 0.15, "-X": 0.10, "+Y": 0.15, "-Y": 0.15}
    elif mode_key in {"nadir", "hill", "lvlh"}:
        solar = {"+X": 0.65, "+Y": 0.35, "-Y": 0.20}
        albedo = {"-Z": 1.0, "+X": 0.10, "+Y": 0.10, "-Y": 0.10}
        earth_ir = {"-Z": 1.0, "+X": 0.20, "-X": 0.20, "+Y": 0.20, "-Y": 0.20}
    elif mode_key in {"tumbling", "detumble"}:
        solar = {face: 1.0 / 6.0 for face in ("+X", "-X", "+Y", "-Y", "+Z", "-Z")}
        albedo = {face: 1.0 / 6.0 for face in ("+X", "-X", "+Y", "-Y", "+Z", "-Z")}
        earth_ir = {face: 1.0 / 6.0 for face in ("+X", "-X", "+Y", "-Y", "+Z", "-Z")}
    else:
        solar = {"+X": 1.0}
        albedo = {"-Z": 1.0}
        earth_ir = {"-Z": 1.0, "+X": 0.15, "-X": 0.15}
    return {"face_solar_exposure": solar, "face_albedo_exposure": albedo, "face_earth_ir_exposure": earth_ir}


def build_orbit_attitude_thermal_environment(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Build a thermal environment mapping from a task spec.

    The returned payload can be placed under ``parameters.environment`` for
    ``subsystem.thermal_reduced_order.v1``.
    """

    params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
    orbit = spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}
    attitude = params.get("attitude") if isinstance(params.get("attitude"), Mapping) else {}
    thermal_env = params.get("environment") if isinstance(params.get("environment"), Mapping) else {}
    mode = str(attitude.get("mode", params.get("attitude_mode", "sun_pointing")))
    maps = exposure_maps_for_attitude_mode(mode)
    altitude_m = float(orbit.get("altitude_m", params.get("altitude_m", 520_000.0)))
    # Keep this intentionally simple: higher LEO altitude slightly reduces albedo/IR
    # proxy heat inputs but does not perform view-factor geometry.
    altitude_factor = max(0.70, min(1.10, 1.0 - (altitude_m - 400_000.0) / 4.0e6))
    env = {
        "schema_version": ORBIT_ATTITUDE_THERMAL_SCHEMA_VERSION,
        "attitude_mode": mode,
        "altitude_m": altitude_m,
        "solar_flux_w_m2": float(thermal_env.get("solar_flux_w_m2", 1361.0)),
        "albedo_flux_w_m2": float(thermal_env.get("albedo_flux_w_m2", 120.0)) * altitude_factor,
        "earth_ir_flux_w_m2": float(thermal_env.get("earth_ir_flux_w_m2", 237.0)) * altitude_factor,
        "deep_space_temp_k": float(thermal_env.get("deep_space_temp_k", 3.0)),
        "shadow_factor": float(thermal_env.get("shadow_factor", 1.0)),
        "eclipse_period_s": float(thermal_env.get("eclipse_period_s", orbit.get("eclipse_period_s", 5400.0))),
        "eclipse_duration_s": float(thermal_env.get("eclipse_duration_s", orbit.get("eclipse_duration_s", 0.0))),
        "eclipse_start_s": float(thermal_env.get("eclipse_start_s", orbit.get("eclipse_start_s", 0.0))),
        **maps,
    }
    return env


__all__ = ["ORBIT_ATTITUDE_THERMAL_SCHEMA_VERSION", "exposure_maps_for_attitude_mode", "build_orbit_attitude_thermal_environment", "build_whole_spacecraft_thermal_task_spec"]


def build_whole_spacecraft_thermal_task_spec(spec: Mapping[str, Any]) -> dict[str, Any]:
    """Build a subsystem thermal TaskSpec from a whole-spacecraft coupling spec.

    This is the integration-layer boundary: orbit/attitude/power inputs are
    converted into thermal boundary conditions, then delegated to
    ``subsystem.thermal_reduced_order.v1``.
    """

    params = spec.get("parameters") if isinstance(spec.get("parameters"), Mapping) else {}
    spacecraft = spec.get("spacecraft") if isinstance(spec.get("spacecraft"), Mapping) else {}
    eps = spacecraft.get("eps") if isinstance(spacecraft.get("eps"), Mapping) else {}
    thermal = params.get("thermal") if isinstance(params.get("thermal"), Mapping) else {}
    power = params.get("power") if isinstance(params.get("power"), Mapping) else {}
    attitude = params.get("attitude") if isinstance(params.get("attitude"), Mapping) else {}
    template_id = str(thermal.get("template_id", params.get("template_id", "cubesat_7node_box_template")))
    internal_power = thermal.get("internal_power_by_node_w")
    if not isinstance(internal_power, Mapping):
        bus = float(power.get("bus_load_power_w", eps.get("bus_load_power_w", params.get("bus_load_power_w", 12.0))))
        payload = float(power.get("payload_load_power_w", eps.get("payload_load_power_w", params.get("payload_load_power_w", 6.0))))
        adcs = float(power.get("adcs_load_power_w", eps.get("adcs_load_power_w", params.get("adcs_load_power_w", 3.0))))
        comm = float(power.get("comm_load_power_w", eps.get("comm_load_power_w", params.get("comm_load_power_w", 2.0))))
        internal_power = {"internal": max(0.0, bus + payload + adcs + comm)}
    thermal_params: dict[str, Any] = {
        "template_id": template_id,
        "attitude": dict(attitude or {"mode": params.get("attitude_mode", "sun_pointing")}),
        "internal_power_by_node_w": dict(internal_power),
        "heater_power_w": float(thermal.get("heater_power_w", params.get("heater_power_w", 8.0))),
        "heater_setpoint_c": float(thermal.get("heater_setpoint_c", params.get("heater_setpoint_c", 2.0))),
        "heater_deadband_c": float(thermal.get("heater_deadband_c", params.get("heater_deadband_c", 2.0))),
    }
    if isinstance(thermal.get("heater_control"), Mapping):
        thermal_params["heater_control"] = dict(thermal["heater_control"])
    elif isinstance(params.get("heater_control"), Mapping):
        thermal_params["heater_control"] = dict(params["heater_control"])
    if isinstance(thermal.get("template_overrides"), Mapping):
        thermal_params["template_overrides"] = dict(thermal["template_overrides"])
    if isinstance(thermal.get("environment"), Mapping):
        thermal_params["environment"] = dict(thermal["environment"])
    return {
        "schema_version": str(spec.get("schema_version", "0.1.0")),
        "task_id": f"{spec.get('task_id', 'whole_spacecraft_thermal')}_thermal_child",
        "task_type": "subsystem",
        "capability_id": "subsystem.thermal_reduced_order.v1",
        "target": {"level": "subsystem", "name": "thermal_reduced_order", "mode": (spec.get("target") or {}).get("mode", "nominal") if isinstance(spec.get("target"), Mapping) else "nominal"},
        "simulation": dict(spec.get("simulation") if isinstance(spec.get("simulation"), Mapping) else {}),
        "orbit_environment": dict(spec.get("orbit_environment") if isinstance(spec.get("orbit_environment"), Mapping) else {}),
        "parameters": thermal_params,
        "outputs": dict(spec.get("outputs") if isinstance(spec.get("outputs"), Mapping) else {"output_root": "datasets/whole_spacecraft_orbit_attitude_thermal", "trace_format": "csv"}),
        "metadata": {
            "parent_capability_id": "whole_spacecraft.orbit_attitude_thermal.v1",
            "parent_task_id": str(spec.get("task_id", "whole_spacecraft_thermal")),
            "integration_boundary": "orbit_attitude_power_to_thermal_boundary_conditions",
        },
    }
